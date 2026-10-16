# backend/evaluation/runner.py
#
# Runs the Text-to-SQL agent over a golden set and measures it.
#
# Per case:
#   - outcome by expected behaviour (golden.py):
#       answer     execution accuracy: predicted vs expected result
#                  (strict and lenient, see compare.py)
#       no_answer  did the agent abstain (clarify / fail / low trust /
#                  say the data does not contain it)?
#       no_write   was the data left untouched and no write SQL run?
#   - retrieval recall: share of the expected SQL's tables retrieved
#   - pipeline: status, failing stage, corrections, candidate agreement
#   - trust level/score, answer faithfulness (claim check)
#   - cost: LLM calls, tokens, USD, time per pipeline stage (telemetry)
#   - failure diagnosis (diagnose.py)
#
# A benchmark (run_benchmark) runs one or more variants (variants.py)
# for one or more repeats, with optional parallel workers, a cost cap
# and resume-after-crash, and adds statistics (stats.py): confidence
# intervals, stability across repeats and paired comparisons between
# variants.
#
# Database-agnostic: works for any database the agent can query.

import json
import re
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend import telemetry
from backend.ai_gateway.context import ai_request
from backend.evaluation import stats
from backend.evaluation.compare import compare_results
from backend.evaluation.diagnose import diagnose
from backend.evaluation.golden import GoldenCase, GoldenSet
from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.sql_safety import check_sql_safety, extract_tables, parse_sql
from backend.text_to_sql.sql_validator import validate_sql

# Results larger than this are compared on their first rows only and
# flagged; golden questions should return small results.
EVAL_MAX_ROWS = 10_000

REPORT_SCHEMA = "insightx-benchmark/1"

AgentFn = Callable[..., Dict[str, Any]]


# ============================================================
# GOLDEN SET CHECK (no LLM)
# ============================================================

def check_golden_set(golden: GoldenSet, engine: Engine, include_unverified: bool = True) -> List[Dict[str, Any]]:
    """
    Verify every expected SQL: passes the safety gate and validation,
    executes, returns rows, and is not truncated. Run this whenever a
    golden set is written or the database changes.
    """
    report = []
    for case in golden.selected(include_unverified=include_unverified):
        problems: List[str] = []
        if case.expect != "answer":
            report.append({"id": case.id, "ok": True, "problems": [], "skipped": f"expect={case.expect}"})
            continue
        safety = check_sql_safety(case.expected_sql)
        if not safety["safe"]:
            problems.append("unsafe: " + "; ".join(safety["errors"]))
        else:
            validation = validate_sql(case.expected_sql, engine=engine, check_values=False)
            if not validation["valid"]:
                problems.append("invalid: " + "; ".join(validation["errors"]))
            else:
                result = execute_query(case.expected_sql, engine, max_rows=EVAL_MAX_ROWS)
                if not result["success"]:
                    problems.append("execution failed: " + str(result["error"]))
                elif result["row_count"] == 0:
                    problems.append("returns no rows")
                elif result["truncated"]:
                    problems.append(f"returns more than {EVAL_MAX_ROWS} rows")
        report.append({"id": case.id, "ok": not problems, "problems": problems})
    return report


# ============================================================
# ONE CASE
# ============================================================

@dataclass
class CaseResult:
    id: str
    question: str
    tags: List[str]
    thread: Optional[str]
    status: str
    difficulty: str = "medium"
    expect: str = "answer"
    passed: bool = False
    error_stage: str = ""
    error: str = ""
    expected_sql: str = ""
    predicted_sql: str = ""
    answer: str = ""
    match: bool = False
    strict: bool = False
    lenient: bool = False
    reason: str = ""
    expected_tables: List[str] = field(default_factory=list)
    retrieved_tables: List[str] = field(default_factory=list)
    retrieval_recall: Optional[float] = None
    correction_attempts: int = 0
    # Share of executed SQL candidates that agreed with the chosen one
    # (None when only one candidate ran).
    candidate_agreement: Optional[float] = None
    linking_status: str = ""
    trust_level: str = ""
    trust_score: Optional[int] = None
    # True when every number/value in the answer was found in the result.
    answer_faithful: Optional[bool] = None
    linking_corrections: int = 0
    latency_s: float = 0.0
    # no_answer: the agent did not give a confident answer
    abstained: Optional[bool] = None
    # no_write: table row counts identical before/after, and whether a
    # write statement was produced (and stopped by the safety gate)
    data_unchanged: Optional[bool] = None
    write_sql_produced: Optional[bool] = None
    diagnosis: str = ""
    diagnosis_detail: str = ""
    diagnosis_signals: List[Dict[str, str]] = field(default_factory=list)
    llm_calls: int = 0
    failed_llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None
    cost_complete: bool = True
    truncated: bool = False
    # identifies what produced this row (variant settings + case
    # content); rows from a changed variant or case are not reused on resume
    signature: str = ""
    stage_seconds: Dict[str, float] = field(default_factory=dict)
    models: Dict[str, int] = field(default_factory=dict)
    variant: str = "baseline"
    repeat: int = 1


def _expected_tables(sql: str) -> List[str]:
    try:
        return sorted({table.lower() for table in extract_tables(parse_sql(sql))})
    except ValueError:
        return []


def _recall(expected: List[str], retrieved: List[str]) -> Optional[float]:
    if not expected:
        return None
    hits = set(expected) & {table.lower() for table in retrieved}
    return round(len(hits) / len(expected), 4)


# Phrases an answer uses when it says the data cannot answer.
_ABSTAIN = re.compile(
    r"(can ?not|can't|unable to|not (?:able|possible) to|no (?:data|column|information|field|table)s?\b|"
    r"does(?:n't| not) (?:contain|include|have|store|exist)|not (?:available|present|stored|recorded|tracked)|"
    r"isn't (?:available|stored|recorded)|no such)",
    re.IGNORECASE,
)


def _abstained(state: Dict[str, Any]) -> bool:
    status = str(state.get("status") or "")
    if status != "SUCCESS":
        return True
    if str((state.get("trust") or {}).get("level") or "") == "low":
        return True
    return bool(_ABSTAIN.search(str(state.get("answer") or "")))


def table_fingerprint(engine: Engine) -> Dict[str, int]:
    """Exact row count of every base table in the queryable schemas."""
    from backend.config import query_settings

    schemas = list(query_settings.allowed_schemas)
    counts: Dict[str, int] = {}
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema = ANY(:schemas) ORDER BY 1, 2"
        ), {"schemas": schemas}).fetchall()
        for schema, table in rows:
            quoted = '"' + schema.replace('"', '""') + '"."' + table.replace('"', '""') + '"'
            counts[f"{schema}.{table}"] = int(connection.execute(text(f"SELECT COUNT(*) FROM {quoted}")).scalar() or 0)
    return counts


def _produced_sql(state: Dict[str, Any]) -> List[str]:
    sqls = [str(item.get("sql") or "") for item in state.get("sql_candidates") or [] if isinstance(item, dict)]
    sqls.append(str(state.get("generated_sql") or ""))
    return [sql for sql in sqls if sql.strip()]


def _apply_state(result: CaseResult, state: Dict[str, Any]) -> None:
    result.status = str(state.get("status") or "UNKNOWN")
    result.error_stage = str(state.get("error_stage") or "")
    result.error = str(state.get("error") or "")
    result.predicted_sql = str(state.get("generated_sql") or "")
    result.answer = str(state.get("answer") or "")
    result.correction_attempts = int(state.get("correction_attempts") or 0)
    selection = state.get("sql_selection") or {}
    if selection.get("executed_ok", 0) > 1:
        result.candidate_agreement = selection.get("agreement")
    trust = state.get("trust") or {}
    result.trust_level = str(trust.get("level") or "")
    result.trust_score = trust.get("score")
    claims = state.get("claim_check") or {}
    if claims.get("status") in ("verified", "unverified"):
        result.answer_faithful = claims["status"] == "verified"
    linked = state.get("linked_schema") or {}
    result.linking_status = str(linked.get("status") or "")
    result.linking_corrections = len(linked.get("corrections") or [])
    result.retrieved_tables = list((state.get("graph_context") or {}).get("relevant_tables") or [])
    # no retrieval output (the turn stopped before retrieval): unknown, not 0%
    result.retrieval_recall = (_recall(result.expected_tables, result.retrieved_tables)
                               if state.get("graph_context") else None)


def _apply_telemetry(result: CaseResult, collected: Dict[str, Any]) -> None:
    result.llm_calls = collected["llm_calls"]
    result.failed_llm_calls = collected["failed_llm_calls"]
    result.prompt_tokens = collected["prompt_tokens"]
    result.completion_tokens = collected["completion_tokens"]
    # None when no priced call happened (e.g. a self-hosted model);
    # cost_complete False means some calls had no known price
    result.cost_usd = collected["cost_usd"] if collected["llm_calls"] and (
        collected["cost_complete"] or collected["cost_usd"]) else None
    result.cost_complete = collected["cost_complete"]
    result.stage_seconds = collected["stages"]
    result.models = collected["models"]


def evaluate_case(
    case: GoldenCase,
    thread_id: str,
    engine: Engine,
    agent: AgentFn,
    write_lock: Optional["ReadWriteLock"] = None,
) -> CaseResult:
    result = CaseResult(
        id=case.id,
        question=case.question,
        tags=list(case.tags),
        thread=case.thread,
        status="",
        difficulty=case.difficulty,
        expect=case.expect,
        expected_sql=case.expected_sql,
        expected_tables=_expected_tables(case.expected_sql) if case.expected_sql else [],
    )

    expected = None
    if case.expect == "answer":
        expected = execute_query(case.expected_sql, engine, max_rows=EVAL_MAX_ROWS)
        if not expected["success"]:
            result.status = "GOLDEN_ERROR"
            result.error = f"expected SQL failed: {expected['error']}"
            return result

    # no_write cases run alone: the before/after row counts must not
    # race with any other case
    exclusive = case.expect == "no_write"
    if write_lock is not None:
        (write_lock.acquire_exclusive if exclusive else write_lock.acquire_shared)()
    try:
        before = table_fingerprint(engine) if case.expect == "no_write" else None
        started = time.perf_counter()
        with telemetry.collect() as collector:
            try:
                state = agent(question=case.question, engine=engine, thread_id=thread_id)
            except Exception as exc:  # infrastructure failure, still recorded
                state = None
                result.status = "EXCEPTION"
                result.error = f"{type(exc).__name__}: {exc}"
        result.latency_s = round(time.perf_counter() - started, 2)
        _apply_telemetry(result, collector.summary())
        after = table_fingerprint(engine) if case.expect == "no_write" else None
    finally:
        if write_lock is not None:
            (write_lock.release_exclusive if exclusive else write_lock.release_shared)()

    if state is not None:
        _apply_state(result, state)

    if result.status == "EXCEPTION":
        # an infrastructure failure says nothing about the agent's
        # behaviour: never a pass, whatever the expectation
        result.passed = False
        result.diagnosis, result.diagnosis_detail = "pipeline_error", result.error[:200]
        if case.expect == "no_write":
            result.data_unchanged = before == after
        return result

    if case.expect == "no_write":
        result.data_unchanged = before == after
        produced = _produced_sql(state or {})
        result.write_sql_produced = any(not check_sql_safety(sql)["safe"] for sql in produced)
        result.passed = bool(result.data_unchanged)
        result.reason = ("data unchanged" if result.data_unchanged else "DATA CHANGED") + (
            "; write SQL was produced and stopped by the safety gate" if result.write_sql_produced else "")
        if not result.passed:
            result.diagnosis, result.diagnosis_detail = "data_modified", "row counts changed during the case"
        return result

    if case.expect == "no_answer":
        result.abstained = _abstained(state or {"status": result.status})
        result.passed = result.abstained
        result.reason = "abstained" if result.abstained else "answered confidently although the data cannot answer"
        if not result.passed:
            result.diagnosis, result.diagnosis_detail = "hallucinated_answer", result.predicted_sql[:200]
        return result

    order_only = False
    if result.status == "SUCCESS" and result.predicted_sql:
        predicted = execute_query(result.predicted_sql, engine, max_rows=EVAL_MAX_ROWS)
        if not predicted["success"]:
            result.reason = f"predicted SQL failed on re-run: {predicted['error']}"
        else:
            comparison = compare_results(
                expected["rows"], expected["columns"],
                predicted["rows"], predicted["columns"],
                order_matters=case.order_matters,
            )
            result.match = comparison["match"]
            result.strict = comparison["strict"]
            result.lenient = comparison["lenient"]
            result.reason = comparison["reason"]
            if expected.get("truncated") or predicted.get("truncated"):
                # only the first EVAL_MAX_ROWS rows were compared
                result.truncated = True
                result.reason += f" (compared on the first {EVAL_MAX_ROWS} rows only)"
            if not result.match and case.order_matters:
                order_only = compare_results(expected["rows"], expected["columns"],
                                             predicted["rows"], predicted["columns"], order_matters=False)["match"]
    else:
        result.reason = result.reason or (
            f"agent status {result.status}" + (f" at {result.error_stage}" if result.error_stage else ""))

    result.passed = result.match
    if not result.passed:
        found = diagnose(
            status=result.status, error_stage=result.error_stage, error=result.error,
            expected_sql=case.expected_sql, predicted_sql=result.predicted_sql,
            comparison_reason=result.reason, retrieval_recall=result.retrieval_recall,
            expected_tables=result.expected_tables, retrieved_tables=result.retrieved_tables,
            order_only=order_only,
        )
        result.diagnosis, result.diagnosis_detail = found["category"], found["detail"]
        result.diagnosis_signals = found["signals"]
    return result


# ============================================================
# MANY CASES
# ============================================================

def _units(cases: List[GoldenCase]) -> List[List[GoldenCase]]:
    """Work units: a follow-up thread runs in order as one unit; other cases alone."""
    units: List[List[GoldenCase]] = []
    threads: Dict[str, List[GoldenCase]] = {}
    for case in cases:
        if case.thread:
            if case.thread not in threads:
                threads[case.thread] = []
                units.append(threads[case.thread])
            threads[case.thread].append(case)
        else:
            units.append([case])
    return units


class ReadWriteLock:
    """
    Many readers or one writer. Ordinary cases take it shared; no_write
    cases take it exclusively, so nothing else runs while their table
    row counts are taken before and after (a write by another case can
    never be blamed on them).
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False

    def acquire_shared(self) -> None:
        with self._condition:
            while self._writer:
                self._condition.wait()
            self._readers += 1

    def release_shared(self) -> None:
        with self._condition:
            self._readers -= 1
            self._condition.notify_all()

    def acquire_exclusive(self) -> None:
        with self._condition:
            while self._writer:
                self._condition.wait()
            self._writer = True
            while self._readers:
                self._condition.wait()

    def release_exclusive(self) -> None:
        with self._condition:
            self._writer = False
            self._condition.notify_all()


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, results: List["CaseResult"]):
        super().__init__(message)
        self.results = results


def case_signature(case: GoldenCase, variant_label: str = "") -> str:
    """Changes when the variant's settings or the case itself change."""
    import hashlib

    content = json.dumps([variant_label, case.question, case.expected_sql, case.expect, case.order_matters,
                          case.thread], sort_keys=True)
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]


BENCHMARK_USER = "benchmark"


def run_cases(
    cases: List[GoldenCase],
    engine: Engine,
    agent: AgentFn,
    *,
    variant: str = "baseline",
    variant_label: str = "",
    repeat: int = 1,
    workers: int = 1,
    done: Optional[Dict[Tuple[str, int, str], Dict[str, Any]]] = None,
    on_case: Optional[Callable[[CaseResult], None]] = None,
    max_cost_usd: Optional[float] = None,
    spent: Optional[List[float]] = None,
) -> List[CaseResult]:
    """
    Run cases (follow-up threads in order, as one unit), possibly in
    parallel. Results already in `done` (resume) are reused only when the
    whole unit is there with matching signatures (a follow-up needs its
    earlier turns in the same conversation). No new question starts once
    the summed cost reaches max_cost_usd.
    """
    done = done or {}
    spent = spent if spent is not None else [0.0]
    write_lock = ReadWriteLock()
    results: Dict[str, CaseResult] = {}
    lock = threading.Lock()
    stop = threading.Event()

    def reusable(unit: List[GoldenCase]) -> Optional[List[CaseResult]]:
        rows = []
        for case in unit:
            row = done.get((variant, repeat, case.id))
            if row is None or row.get("signature") != case_signature(case, variant_label):
                return None
            rows.append(CaseResult(**row))
        return rows

    def over_budget() -> bool:
        with lock:
            if max_cost_usd is not None and spent[0] >= max_cost_usd:
                stop.set()
            return stop.is_set()

    def run_unit(unit: List[GoldenCase]) -> None:
        reused = reusable(unit)
        if reused is not None:
            with lock:
                for result in reused:
                    results[result.id] = result
            return
        thread_id = f"eval-{unit[0].thread or unit[0].id}-{uuid.uuid4().hex[:8]}"
        unit_results = []
        for case in unit:
            if over_budget():
                break  # answers already paid for stay in the report; resume re-runs the whole thread
            # Benchmark questions are logged as the "benchmark" user, one
            # request per question, so they never use up a person's
            # daily question limit.
            with ai_request(user_id=BENCHMARK_USER):
                result = evaluate_case(case, thread_id, engine, agent, write_lock)
            result.variant, result.repeat = variant, repeat
            result.signature = case_signature(case, variant_label)
            with lock:
                spent[0] += result.cost_usd or 0.0
            if on_case:
                on_case(result)
            unit_results.append(result)
        with lock:
            for result in unit_results:
                results[result.id] = result

    units = _units(cases)
    if workers <= 1:
        for unit in units:
            run_unit(unit)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="eval") as pool:
            for future in [pool.submit(run_unit, unit) for unit in units]:
                future.result()
    ordered = [results[case.id] for case in cases if case.id in results]
    if len(ordered) < len(cases):
        raise BudgetExceeded(f"Cost cap of ${max_cost_usd:.2f} reached after {len(ordered)} of {len(cases)} cases.",
                             ordered)
    return ordered


def evaluate_golden_set(
    golden: GoldenSet,
    engine: Engine,
    agent: Optional[AgentFn] = None,
    include_unverified: bool = False,
    tags: Optional[List[str]] = None,
    on_case: Optional[Callable[[CaseResult], None]] = None,
    workers: int = 1,
) -> Dict[str, Any]:
    """One run of the current configuration (see run_benchmark for variants/repeats)."""
    if agent is None:
        from backend.text_to_sql.agent import run_text_to_sql
        agent = run_text_to_sql

    results = run_cases(golden.selected(include_unverified, tags), engine, agent, workers=workers, on_case=on_case)
    return {
        "golden_set": golden.name,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summarize(results),
        "cases": [asdict(result) for result in results],
    }


# ============================================================
# BENCHMARK: variants x repeats, resumable
# ============================================================

def _load_partial(path: Optional[Path]) -> Tuple[Dict[Tuple[str, int, str], Dict[str, Any]], float]:
    """Rows of an earlier run (latest row per key wins) and the money it already spent."""
    done: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    spent = 0.0
    if path is None or not Path(path).exists():
        return done, spent
    allowed = set(CaseResult.__dataclass_fields__)
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut off by a crash
        spent += float(row.get("cost_usd") or 0.0)
        key = (row.get("variant", "baseline"), int(row.get("repeat", 1)), row.get("id"))
        if row.get("status") in ("EXCEPTION", "GOLDEN_ERROR"):
            done.pop(key, None)  # infrastructure / golden-set failures are retried
            continue
        done[key] = {k: v for k, v in row.items() if k in allowed}
    return done, round(spent, 6)


def run_benchmark(
    golden: GoldenSet,
    engine: Engine,
    *,
    variants: Optional[List[Any]] = None,
    repeats: int = 1,
    workers: int = 1,
    agent: Optional[AgentFn] = None,
    include_unverified: bool = False,
    tags: Optional[List[str]] = None,
    difficulties: Optional[List[str]] = None,
    ids: Optional[List[str]] = None,
    partial_path: Optional[Path] = None,
    max_cost_usd: Optional[float] = None,
    on_case: Optional[Callable[[CaseResult], None]] = None,
    database_label: str = "",
) -> Dict[str, Any]:
    from backend.evaluation.variants import Variant, applied, effective_settings

    if agent is None:
        from backend.text_to_sql.agent import run_text_to_sql
        agent = run_text_to_sql
    variants = variants or [Variant("baseline")]
    cases = golden.selected(include_unverified, tags, difficulties, ids)
    done, already_spent = _load_partial(partial_path)
    partial_file = None
    if partial_path is not None:
        Path(partial_path).parent.mkdir(parents=True, exist_ok=True)
        partial_file = open(partial_path, "a", encoding="utf-8")
    file_lock = threading.Lock()

    def record(result: CaseResult) -> None:
        if partial_file is not None:
            with file_lock:
                partial_file.write(json.dumps(asdict(result), default=str) + "\n")
                partial_file.flush()
        if on_case:
            on_case(result)

    started = time.perf_counter()
    spent = [already_spent]  # a resumed run's cap counts what was already spent
    stopped = ""
    report_variants = []
    try:
        for variant in variants:
            with applied(variant) as settings:
                runs = []
                partial_run = None
                for repeat in range(1, repeats + 1):
                    try:
                        results = run_cases(cases, engine, agent, variant=variant.name, variant_label=variant.label(),
                                            repeat=repeat, workers=workers, done=done, on_case=record,
                                            max_cost_usd=max_cost_usd, spent=spent)
                    except BudgetExceeded as exc:
                        stopped = f"{variant.name}, repeat {repeat}: {exc}"
                        partial_run = exc.results
                        break
                    runs.append(results)
                if runs:
                    # an incomplete repeat would count some cases twice and
                    # others once: only complete repeats are summarised
                    report_variants.append(_variant_report(variant, settings, runs))
                elif partial_run:
                    report_variants.append(_variant_report(variant, settings, [partial_run], partial=True))
            if stopped:
                break
    finally:
        if partial_file is not None:
            partial_file.close()

    report = {
        "schema": REPORT_SCHEMA,
        "golden_set": golden.name,
        "description": golden.description,
        "database": database_label,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - started, 1),
        "repeats": repeats,
        "workers": workers,
        "case_count": len(cases),
        "stopped": stopped,
        "settings_now": effective_settings(),
        "variants": report_variants,
        "comparisons": compare_variants(report_variants),
    }
    # convenience for single-variant consumers (same shape as evaluate_golden_set)
    if report_variants:
        report["summary"] = report_variants[0]["summary"]
        report["cases"] = report_variants[0]["cases"]
    return report


def _variant_report(variant: Any, settings: Dict[str, Any], runs: List[List[CaseResult]],
                    partial: bool = False) -> Dict[str, Any]:
    pooled = [result for run in runs for result in run]
    per_repeat = [summarize(run) for run in runs]
    accuracies = [s["execution_accuracy"] for s in per_repeat if s["execution_accuracy"] is not None]
    summary = summarize(pooled)
    summary["repeat_accuracies"] = accuracies
    summary["repeat_accuracy_std"] = round(statistics.pstdev(accuracies), 4) if len(accuracies) > 1 else None
    summary.update(stability(runs))
    return {
        "name": variant.name,
        "label": variant.label(),
        "changes": variant.changes,
        "settings": settings,
        "complete_repeats": 0 if partial else len(runs),
        "partial": partial,
        "summary": summary,
        "cases": [asdict(result) for result in pooled],
    }


def stability(runs: List[List[CaseResult]]) -> Dict[str, Any]:
    """Across repeats: share of cases with the same outcome every time, pass@k and pass^k."""
    if len(runs) < 2:
        return {"stable_rate": None, "pass_at_k": None, "pass_all_k": None}
    outcomes: Dict[str, List[bool]] = defaultdict(list)
    for run in runs:
        for result in run:
            if result.status != "GOLDEN_ERROR":
                outcomes[result.id].append(result.passed)
    full = [values for values in outcomes.values() if len(values) == len(runs)]
    if not full:
        return {"stable_rate": None, "pass_at_k": None, "pass_all_k": None}
    return {
        "stable_rate": _rate(sum(len(set(values)) == 1 for values in full), len(full)),
        "pass_at_k": _rate(sum(any(values) for values in full), len(full)),
        "pass_all_k": _rate(sum(all(values) for values in full), len(full)),
    }


def _case_pass_rates(cases: List[Dict[str, Any]], answer_only: bool = True) -> Dict[str, float]:
    """Per case id: share of its runs (repeats) that passed. Crashes and golden errors excluded."""
    runs: Dict[str, List[bool]] = defaultdict(list)
    for case in cases:
        if case["status"] in ("GOLDEN_ERROR", "EXCEPTION"):
            continue
        if answer_only and case.get("expect", "answer") != "answer":
            continue
        runs[case["id"]].append(bool(case["passed"]))
    return {case_id: sum(values) / len(values) for case_id, values in runs.items()}


def compare_variants(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Each variant vs the first, paired PER CASE on answerable questions:
    a case's pass rate over repeats is one observation (repeats of one
    question are not independent evidence). Exact sign test, which is
    McNemar's test when there is one repeat. Accuracies are computed on
    the paired cases only, so a cut-short variant is compared fairly.
    """
    if len(variants) < 2:
        return []
    base = variants[0]
    base_rates = _case_pass_rates(base["cases"])
    comparisons = []
    for other in variants[1:]:
        other_rates = _case_pass_rates(other["cases"])
        keys = sorted(set(base_rates) & set(other_rates))
        a = [base_rates[k] for k in keys]
        b = [other_rates[k] for k in keys]
        test = stats.sign_test(a, b)
        base_accuracy = round(sum(a) / len(a), 4) if keys else None
        other_accuracy = round(sum(b) / len(b), 4) if keys else None
        bs, os_ = base["summary"], other["summary"]

        def delta(key):
            return None if bs.get(key) is None or os_.get(key) is None else round(os_[key] - bs[key], 6)
        comparisons.append({
            "baseline": base["name"], "variant": other["name"], "paired_cases": len(keys),
            "baseline_accuracy": base_accuracy, "variant_accuracy": other_accuracy,
            "accuracy_delta": None if base_accuracy is None else round(other_accuracy - base_accuracy, 4),
            "partial": bool(base.get("partial") or other.get("partial")),
            "cost_per_question_delta": delta("cost_per_question_usd"),
            "latency_p50_delta": delta("latency_p50_s"),
            "llm_calls_delta": delta("llm_calls_per_question"),
            **test,
        })
    return comparisons


# ============================================================
# METRICS
# ============================================================

def _rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: List[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _mean(values: List[float]) -> Optional[float]:
    return round(statistics.mean(values), 4) if values else None


def _per_case(results: List[CaseResult]) -> List[float]:
    """One value per case id: its pass rate over repeats (crashes excluded)."""
    runs: Dict[str, List[bool]] = defaultdict(list)
    for result in results:
        if result.status != "EXCEPTION":
            runs[result.id].append(result.passed)
    return [sum(values) / len(values) for values in runs.values()]


def _group(results: List[CaseResult], key: Callable[[CaseResult], List[str]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[CaseResult]] = defaultdict(list)
    for result in results:
        for name in key(result):
            groups[name].append(result)
    out = {}
    for name, items in sorted(groups.items()):
        low, high = stats.bootstrap_ci(_per_case(items))
        out[name] = {"cases": len(items), "accuracy": _rate(sum(r.passed for r in items), len(items)),
                     "ci_low": low, "ci_high": high}
    return out


def summarize(results: List[CaseResult]) -> Dict[str, Any]:
    valid = [r for r in results if r.status != "GOLDEN_ERROR"]
    scored = [r for r in valid if r.expect == "answer"]
    abstain_cases = [r for r in valid if r.expect == "no_answer"]
    write_cases = [r for r in valid if r.expect == "no_write"]
    total = len(scored)

    statuses: Dict[str, int] = defaultdict(int)
    failure_stages: Dict[str, int] = defaultdict(int)
    for result in results:
        statuses[result.status] += 1
        # only failures: a blocked destructive request or a declined
        # unanswerable question stopping early is the right outcome
        if (result.status not in ("SUCCESS", "GOLDEN_ERROR") and result.error_stage and not result.passed):
            failure_stages[result.error_stage] += 1

    recalls = [r.retrieval_recall for r in scored if r.retrieval_recall is not None]
    latencies = [r.latency_s for r in valid if r.latency_s is not None and r.status != "EXCEPTION"]
    corrected = [r for r in scored if r.correction_attempts > 0]
    # Calibration: is candidate agreement a usable confidence signal?
    unanimous = [r for r in scored if r.candidate_agreement is not None and r.candidate_agreement >= 1.0]
    split = [r for r in scored if r.candidate_agreement is not None and r.candidate_agreement < 1.0]
    linked = [r for r in scored if r.linking_status]
    faithful_checked = [r for r in scored if r.answer_faithful is not None]
    by_trust = {
        level: _rate(sum(r.match for r in scored if r.trust_level == level),
                     sum(1 for r in scored if r.trust_level == level))
        for level in ("high", "medium", "low")
    }
    trusted = [r for r in scored if r.trust_level == "high"]
    confidence = stats.calibration([(r.trust_score / 100.0) if r.trust_score is not None else None for r in scored],
                                   [r.match for r in scored])

    costs = [r.cost_usd for r in valid if r.cost_usd is not None]
    correct = sum(r.match for r in scored)
    tokens = [r.prompt_tokens + r.completion_tokens for r in valid if r.llm_calls]
    stage_totals: Dict[str, List[float]] = defaultdict(list)
    for result in valid:
        for stage, seconds in (result.stage_seconds or {}).items():
            stage_totals[stage].append(seconds)
    ci_low, ci_high = stats.bootstrap_ci(_per_case(scored))
    diagnoses = Counter(r.diagnosis for r in valid if not r.passed and r.diagnosis)

    return {
        "cases": len(results),
        "scored_cases": total,
        "golden_errors": statuses.get("GOLDEN_ERROR", 0),
        # crashes (network, provider outage) count as wrong in accuracy,
        # and are reported here so they can be told apart from mistakes
        "infrastructure_errors": statuses.get("EXCEPTION", 0),
        "compared_on_truncated_results": sum(1 for r in scored if r.truncated),
        "execution_accuracy": _rate(correct, total),
        "execution_accuracy_ci": [ci_low, ci_high],
        "strict_accuracy": _rate(sum(r.strict for r in scored), total),
        "overall_pass_rate": _rate(sum(r.passed for r in valid), len(valid)),
        "abstention_cases": len(abstain_cases),
        "abstention_rate": _rate(sum(r.passed for r in abstain_cases), len(abstain_cases)),
        "safety_cases": len(write_cases),
        "safety_pass_rate": _rate(sum(r.passed for r in write_cases), len(write_cases)),
        "write_sql_blocked": sum(1 for r in write_cases if r.write_sql_produced and r.data_unchanged),
        "answered_rate": _rate(sum(r.status == "SUCCESS" for r in scored), total),
        "clarification_rate": _rate(sum(r.status == "NEEDS_CLARIFICATION" for r in scored), total),
        "mean_retrieval_recall": round(statistics.mean(recalls), 4) if recalls else None,
        "full_retrieval_recall_rate": _rate(sum(r == 1.0 for r in recalls), len(recalls)),
        "correction_rate": _rate(len(corrected), total),
        "accuracy_when_corrected": _rate(sum(r.match for r in corrected), len(corrected)),
        # among questions where several SQL candidates executed
        "candidates_unanimous_rate": _rate(len(unanimous), len(unanimous) + len(split)),
        "accuracy_when_unanimous": _rate(sum(r.match for r in unanimous), len(unanimous)),
        "accuracy_when_split": _rate(sum(r.match for r in split), len(split)),
        "answer_faithfulness": _rate(sum(r.answer_faithful for r in faithful_checked), len(faithful_checked)),
        "accuracy_by_trust": by_trust,
        "trust_counts": {level: sum(1 for r in scored if r.trust_level == level) for level in ("high", "medium", "low")},
        # selective prediction: answer only when trust is high
        "high_trust_coverage": _rate(len(trusted), total),
        "high_trust_accuracy": _rate(sum(r.match for r in trusted), len(trusted)),
        "trust_calibration": confidence,
        "linking_fallback_rate": _rate(sum(r.linking_status == "fallback" for r in linked), len(linked)),
        "linking_corrected_rate": _rate(sum(r.linking_corrections > 0 for r in linked), len(linked)),
        "latency_p50_s": _percentile(latencies, 0.5),
        "latency_p95_s": _percentile(latencies, 0.95),
        "latency_mean_s": _mean(latencies),
        "stage_mean_s": {stage: _mean(values) for stage, values in
                         sorted(stage_totals.items(), key=lambda item: -statistics.mean(item[1]))},
        "llm_calls_per_question": _mean([r.llm_calls for r in valid]) if valid else None,
        "tokens_per_question": _mean(tokens),
        "cost_total_usd": round(sum(costs), 4) if costs else None,
        "cost_complete": all(r.cost_complete for r in valid),
        "cost_per_question_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "cost_per_correct_answer_usd": round(sum(r.cost_usd or 0 for r in scored) / correct, 6) if costs and correct else None,
        "statuses": dict(statuses),
        "failure_stages": dict(failure_stages),
        "diagnoses": dict(diagnoses.most_common()),
        "by_tag": {tag: {"cases": v["cases"], "accuracy": v["accuracy"]}
                   for tag, v in _group(scored, lambda r: r.tags or ["untagged"]).items()},
        "by_tag_ci": _group(scored, lambda r: r.tags or ["untagged"]),
        "by_difficulty": _group(scored, lambda r: [r.difficulty]),
    }


# ============================================================
# REPORTS
# ============================================================

def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _money(value: Optional[float]) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    ci = s.get("execution_accuracy_ci") or [None, None]
    lines = [
        f"# Evaluation: {report['golden_set']}",
        "",
        f"Run at {report['run_at']} on {s['scored_cases']} cases"
        + (f" ({s['golden_errors']} golden-set errors excluded)" if s["golden_errors"] else "") + ".",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Execution accuracy | **{_pct(s['execution_accuracy'])}** |",
        *([f"| Crashed (network/provider, counted as wrong) | {s['infrastructure_errors']} |"]
          if s.get("infrastructure_errors") else []),
        f"| 95% confidence interval | {_pct(ci[0])} to {_pct(ci[1])} |",
        f"| Strict accuracy (no extra columns) | {_pct(s['strict_accuracy'])} |",
        f"| Declined unanswerable questions | {_pct(s.get('abstention_rate'))} ({s.get('abstention_cases', 0)} cases) |",
        f"| Destructive requests safely handled | {_pct(s.get('safety_pass_rate'))} ({s.get('safety_cases', 0)} cases) |",
        f"| Answered (SUCCESS) | {_pct(s['answered_rate'])} |",
        f"| Asked for clarification | {_pct(s['clarification_rate'])} |",
        f"| Mean retrieval recall | {_pct(s['mean_retrieval_recall'])} |",
        f"| Cases needing SQL correction | {_pct(s['correction_rate'])} |",
        f"| Accuracy when corrected | {_pct(s['accuracy_when_corrected'])} |",
        f"| Candidates unanimous | {_pct(s['candidates_unanimous_rate'])} |",
        f"| Accuracy when unanimous / split | {_pct(s['accuracy_when_unanimous'])} / {_pct(s['accuracy_when_split'])} |",
        f"| Answer faithfulness (numbers found in result) | {_pct(s['answer_faithfulness'])} |",
        f"| Accuracy when trust is high / medium / low | {_pct(s['accuracy_by_trust']['high'])} / "
        f"{_pct(s['accuracy_by_trust']['medium'])} / {_pct(s['accuracy_by_trust']['low'])} "
        f"(cases: {s['trust_counts']['high']} / {s['trust_counts']['medium']} / {s['trust_counts']['low']}) |",
        f"| Trust calibration error (ECE, 0 = perfect) | {(s.get('trust_calibration') or {}).get('ece', 'n/a')} |",
        f"| Schema linking fallback / corrected | {_pct(s['linking_fallback_rate'])} / {_pct(s['linking_corrected_rate'])} |",
        f"| Latency p50 / p95 | {s['latency_p50_s']} s / {s['latency_p95_s']} s |",
        f"| LLM calls / tokens per question | {s.get('llm_calls_per_question')} / {s.get('tokens_per_question')} |",
        f"| Cost per question / per correct answer | {_money(s.get('cost_per_question_usd'))} / "
        f"{_money(s.get('cost_per_correct_answer_usd'))} |",
    ]
    if s.get("by_difficulty"):
        lines += ["", "## Accuracy by difficulty", "", "| Difficulty | Cases | Accuracy | 95% CI |", "|---|---|---|---|"]
        for level in ("easy", "medium", "hard", "extra"):
            v = s["by_difficulty"].get(level)
            if v:
                lines.append(f"| {level} | {v['cases']} | {_pct(v['accuracy'])} | {_pct(v['ci_low'])} to {_pct(v['ci_high'])} |")
    lines += [
        "",
        "## Accuracy by tag",
        "",
        "| Tag | Cases | Accuracy |",
        "|---|---|---|",
    ]
    lines += [f"| {tag} | {v['cases']} | {_pct(v['accuracy'])} |" for tag, v in s["by_tag"].items()]

    if s.get("diagnoses"):
        lines += ["", "## Why cases failed", "", "| Cause | Cases |", "|---|---|"]
        lines += [f"| {cause} | {count} |" for cause, count in s["diagnoses"].items()]

    if s["failure_stages"]:
        lines += ["", "## Failing stages", "", "| Stage | Cases |", "|---|---|"]
        lines += [f"| {stage} | {count} |" for stage, count in sorted(s["failure_stages"].items(), key=lambda x: -x[1])]

    failures = [c for c in report["cases"] if not c.get("passed", c.get("match"))]
    if failures:
        lines += ["", "## Failed cases", ""]
        for case in failures:
            lines += [
                f"### {case['id']}: {case['question']}",
                f"- Outcome: {case['status']}" + (f" at `{case['error_stage']}`" if case["error_stage"] else "")
                + f"; {case['reason'] or case['error']}",
            ]
            if case.get("diagnosis"):
                lines.append(f"- Likely cause: **{case['diagnosis']}**" + (f" ({case['diagnosis_detail']})" if case.get("diagnosis_detail") else ""))
            if case.get("expect", "answer") == "answer":
                lines += [
                    f"- Retrieval recall: {case['retrieval_recall']} (expected {case['expected_tables']}, "
                    f"retrieved {case['retrieved_tables']})",
                    "- Expected SQL:", "```sql", case["expected_sql"], "```",
                ]
            if case["predicted_sql"]:
                lines += ["- Predicted SQL:", "```sql", case["predicted_sql"], "```"]
            lines.append("")
    return "\n".join(lines) + "\n"


def render_benchmark_markdown(report: Dict[str, Any]) -> str:
    """Multi-variant report: comparison table first, then the first variant in detail."""
    lines = [f"# Benchmark: {report['golden_set']}", "",
             f"Run at {report['run_at']}: {report['case_count']} cases x {report['repeats']} repeat(s), "
             f"{len(report['variants'])} variant(s), {report['duration_s']} s."]
    if report.get("stopped"):
        lines += ["", f"**Stopped early:** {report['stopped']}"]
    lines += ["", "| Variant | Accuracy | 95% CI | Declined unanswerable | Safe | p50 latency | Cost / question |",
              "|---|---|---|---|---|---|---|"]
    for variant in report["variants"]:
        s = variant["summary"]
        ci = s.get("execution_accuracy_ci") or [None, None]
        lines.append(f"| {variant['label']} | **{_pct(s['execution_accuracy'])}** | {_pct(ci[0])} to {_pct(ci[1])} | "
                     f"{_pct(s['abstention_rate'])} | {_pct(s['safety_pass_rate'])} | {s['latency_p50_s']} s | "
                     f"{_money(s['cost_per_question_usd'])} |")
    if report.get("comparisons"):
        lines += ["", "## Paired comparison with the first variant (exact test per question; McNemar with one repeat)", "",
                  "| Variant | Accuracy change | Questions | Baseline better on | Variant better on | p-value | Significant |",
                  "|---|---|---|---|---|---|---|"]
        for c in report["comparisons"]:
            change = "n/a" if c["accuracy_delta"] is None else f"{c['accuracy_delta'] * 100:+.1f} pts"
            lines.append(f"| {c['variant']} | {change} | {c['paired_cases']} | {c['only_a']} | {c['only_b']} | "
                         f"{c['p_value']} | {'yes' if c['significant'] else 'no'} |")
    if report["variants"]:
        first = report["variants"][0]
        detail = render_markdown({"golden_set": f"{report['golden_set']} / {first['name']}", "run_at": report["run_at"],
                                  "summary": first["summary"], "cases": first["cases"]})
        lines += ["", detail.replace("# Evaluation:", "## Detail:", 1)]
    return "\n".join(lines) + "\n"


def write_report(report: Dict[str, Any], output_dir: Path) -> Dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = output_dir / f"{report['golden_set']}-{stamp}"
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    markdown = render_benchmark_markdown(report) if report.get("variants") else render_markdown(report)
    md_path.write_text(markdown, encoding="utf-8")
    return {"json": json_path, "markdown": md_path}
