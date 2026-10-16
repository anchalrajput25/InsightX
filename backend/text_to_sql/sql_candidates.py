# backend/text_to_sql/sql_candidates.py
#
# Several SQL candidates per question, chosen by execution agreement
# (self-consistency).
#
# Different formulations of a correct query return the same rows; wrong
# ones tend to disagree with each other in different ways. So every
# candidate that passes the safety gate and validation is executed
# (read-only, row-capped, statement timeout), the results are grouped
# by equivalence (same rows, column names/order ignored, extra columns
# allowed), and the largest group wins. Its lowest-index member is
# used, so the deterministic first candidate wins ties.
#
# The agreement ratio is reported with the answer: 3/3 agreeing is
# strong evidence, 1/3 is a warning sign.

import logging
import re
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Sequence

from sqlalchemy.engine import Engine

from backend import telemetry
from backend.evaluation.compare import compare_results
from backend.text_to_sql.query_executor import execute_query
from backend.text_to_sql.sql_safety import check_sql_safety, parse_sql
from backend.text_to_sql.sql_validator import validate_sql
from backend import dialects

logger = logging.getLogger("insightx.candidates")


# ============================================================
# PARALLEL CALLS
# ============================================================

def run_parallel(calls: Sequence[Callable[[], Any]], wait_s: Optional[float] = None) -> List[Any]:
    """
    Run calls concurrently. The first call is always awaited; the
    others get at most wait_s more seconds after it finishes (None:
    wait for all). Results are values, Exceptions, or None (timed out).
    """
    if not calls:
        return []
    results: List[Any] = [None] * len(calls)
    executor = ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="sql-candidate")
    try:
        futures = [executor.submit(telemetry.in_current_context(call)) for call in calls]
        wait([futures[0]])
        wait(futures[1:], timeout=None if wait_s is None else max(wait_s, 0))
        for index, future in enumerate(futures):
            if not future.done():
                continue
            try:
                results[index] = future.result()
            except Exception as exc:  # one failing candidate never fails the question
                results[index] = exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results


# ============================================================
# EVALUATION
# ============================================================

def normalize_sql(sql: str) -> str:
    """Canonical text, so trivially different spellings count as one SQL."""
    try:
        return parse_sql(sql).sql(dialect=dialects.sqlglot_name(), normalize=True)
    except Exception:
        return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def _short(error: Any, limit: int = 300) -> str:
    text = str(error or "").strip().splitlines()
    return (text[0] if text else "")[:limit]


def evaluate_sql(sql: str, engine: Engine, max_rows: int) -> Dict[str, Any]:
    """Safety gate -> validation (EXPLAIN + filter values) -> execution."""
    safety = check_sql_safety(sql)
    if not safety.get("safe"):
        return {"status": "unsafe", "error": "; ".join(safety.get("errors") or [])}
    validation = validate_sql(sql, engine=engine)
    if not validation.get("valid"):
        return {"status": "invalid", "error": "; ".join(validation.get("errors") or [])}
    result = execute_query(sql, engine, max_rows=max_rows)
    if not result.get("success"):
        return {"status": "failed", "error": _short(result.get("error"))}
    return {"status": "ok", "result": result}


def _agree(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    for left, right in ((a, b), (b, a)):
        if compare_results(left["rows"], left["columns"], right["rows"], right["columns"])["match"]:
            return True
    return False


def select_candidate(
    candidates: List[Dict[str, Any]],
    engine: Engine,
    max_rows: int,
    evaluate: Callable[[str, Engine, int], Dict[str, Any]] = evaluate_sql,
) -> Dict[str, Any]:
    """
    candidates: [{"index": int, "sql": str}, ...] (index 0 = primary)

    Returns:
        winner      the chosen candidate dict (or None when none ran)
        execution   the winner's execution result (reused downstream)
        summary     per-candidate outcome for the UI / evaluation
        selection   {"candidates", "unique_sql", "executed_ok",
                     "clusters", "winner", "agreement", "reason"}
    """
    # Identical SQL is evaluated once; each copy still counts as a vote.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(normalize_sql(candidate["sql"]), []).append(candidate)
    unique = list(groups.values())

    outcomes = run_parallel(
        [lambda sql=members[0]["sql"]: evaluate(sql, engine, max_rows) for members in unique],
        wait_s=None,  # validation/execution is bounded by statement_timeout
    )

    summary: List[Dict[str, Any]] = []
    executed: List[Dict[str, Any]] = []  # one entry per unique SQL that ran
    for members, outcome in zip(unique, outcomes):
        if isinstance(outcome, Exception) or outcome is None:
            outcome = {"status": "failed", "error": _short(outcome)}
        for member in members:
            entry = {
                "index": member["index"],
                "sql": member["sql"],
                "status": outcome["status"],
                "error": outcome.get("error", ""),
            }
            if outcome["status"] == "ok":
                entry["row_count"] = outcome["result"].get("row_count", 0)
            summary.append(entry)
        if outcome["status"] == "ok":
            executed.append({"members": members, "result": outcome["result"]})

    summary.sort(key=lambda item: item["index"])
    selection: Dict[str, Any] = {
        "candidates": len(candidates),
        "unique_sql": len(unique),
        "executed_ok": sum(len(item["members"]) for item in executed),
    }

    if not executed:
        selection.update({"clusters": [], "winner": None, "agreement": 0.0,
                          "reason": "no candidate passed validation and execution"})
        return {"winner": None, "execution": None, "summary": summary, "selection": selection}

    # Group results that agree.
    clusters: List[List[Dict[str, Any]]] = []
    for item in executed:
        for cluster in clusters:
            if _agree(cluster[0]["result"], item["result"]):
                cluster.append(item)
                break
        else:
            clusters.append([item])

    def votes(cluster):
        return sum(len(item["members"]) for item in cluster)

    def first_index(cluster):
        return min(member["index"] for item in cluster for member in item["members"])

    def non_empty(cluster):
        return cluster[0]["result"].get("row_count", 0) > 0

    # Prefer results with rows (an empty result is usually a wrong
    # filter), then more votes, then the primary candidate.
    best = max(clusters, key=lambda cluster: (non_empty(cluster), votes(cluster), -first_index(cluster)))
    winner_item = min(best, key=lambda item: min(member["index"] for member in item["members"]))
    winner = min(winner_item["members"], key=lambda member: member["index"])

    cluster_of = {}
    for number, cluster in enumerate(sorted(clusters, key=lambda c: (-votes(c), first_index(c)))):
        for item in cluster:
            for member in item["members"]:
                cluster_of[member["index"]] = number
    for entry in summary:
        if entry["index"] in cluster_of:
            entry["cluster"] = cluster_of[entry["index"]]
        entry["selected"] = entry["index"] == winner["index"]

    agreement = votes(best) / selection["executed_ok"]
    selection.update({
        "clusters": sorted((votes(cluster) for cluster in clusters), reverse=True),
        "winner": winner["index"],
        "agreement": round(agreement, 3),
        "reason": (
            f"{votes(best)} of {selection['executed_ok']} executed candidates returned this result"
            + ("" if len(clusters) == 1 else f"; {len(clusters)} different results")
        ),
    })
    return {"winner": winner, "execution": winner_item["result"], "summary": summary, "selection": selection}
