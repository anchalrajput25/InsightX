# evals/run_eval.py
#
# InsightX accuracy test (execution accuracy, the metric used by the
# Spider and BIRD benchmarks).
#
# For every test question:
#   1. InsightX gets only the question and the database (the full
#      Text-to-SQL pipeline, same code as the /query API).
#   2. The SQL InsightX wrote and the gold SQL both run on the same
#      database.
#   3. The result rows are compared. Same data = pass. The SQL text
#      does not matter.
#
# Two scores are reported for every set:
#   strict   same columns and same rows (the official Spider rule)
#   lenient  every expected column is there with the same rows; extra
#            helpful columns are allowed (InsightX's own headline rule)
#
# Uses the existing harness in backend/evaluation (no app code is
# changed). Saved AI answers and the similar-question cache are turned
# off, so every question is really answered by the model.
#
#   python evals/run_eval.py                 Spider + own set
#   python evals/run_eval.py --suite spider  only Spider
#   python evals/run_eval.py --suite own     only the own set
#   python evals/run_eval.py --limit 3       3 questions per database (quick try)
#   python evals/run_eval.py --resume        continue the last run (after a
#                                            crash, Ctrl+C or rate limit)

import argparse
import json
import math
import re
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
sys.path.insert(0, str(ROOT))

# Every question must be answered fresh by the model.
os.environ["INSIGHTX_QUESTION_CACHE"] = "0"

GOLDEN = EVALS / "golden"
SPIDER_DB = EVALS / "spider_data" / "database"
RESULTS = EVALS / "results"
LEVELS = ("easy", "medium", "hard", "extra")
LIMIT_WAIT_S = 70
# Failures caused by the AI service (rate limit, timeout, no key), not by
# a wrong answer. --resume asks these questions again.
INFRA_PATTERN = re.compile(r"rate.?limit|\b429\b|AIUnavailable|quota|too many requests|"
                           r"no ai model|all models failed|connection (error|refused|reset)", re.I)


def is_infra(case) -> bool:
    if case.get("status") == "EXCEPTION":
        return True
    if case.get("passed"):
        return False
    return bool(INFRA_PATTERN.search(str(case.get("error") or "")))


def test_sets(which: str):
    sets = []
    if which in ("spider", "all"):
        for path in sorted((GOLDEN / "spider").glob("*.yaml")):
            sets.append(("spider", path, SPIDER_DB / path.stem / f"{path.stem}.sqlite"))
    if which in ("own", "all"):
        sets.append(("own", GOLDEN / "shop.yaml", ROOT / "sample_datasets" / "shop.sqlite"))
    return sets


def wilson(passed: int, total: int):
    if not total:
        return (0.0, 0.0)
    z = 1.96
    p = passed / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def _drop_infra_failures(partial: Path) -> int:
    """Remove AI-service failures from a saved run so --resume asks them again."""
    if not partial.exists():
        return 0
    kept, dropped = [], 0
    for line in partial.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            infra = is_infra(json.loads(line))
        except json.JSONDecodeError:
            dropped += 1
            continue
        if infra:
            dropped += 1
        else:
            kept.append(line)
    partial.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return dropped


def _case_dict(case):
    return asdict(case) if is_dataclass(case) else dict(case)


def summarize(cases, run_dir: Path, started_at: str, model: str) -> str:
    answered = [c for c in cases if c.get("expect", "answer") == "answer"]
    # The gateway falls back across free models, so report the ones that really answered.
    calls = Counter(name for c in answered for name in (c.get("models") or []))
    used = ", ".join(f"{name} ({100 * count / sum(calls.values()):.0f}%)" for name, count in calls.most_common(6))
    lines = [f"# InsightX accuracy test", "",
             f"Run started {started_at}. Models that answered (share of AI calls): {used or model}.", "",
             "Execution accuracy: InsightX's SQL and the gold SQL are both run, and the result rows are compared.",
             "**Strict** = same columns and rows (official Spider rule). "
             "**Lenient** = expected columns and rows all present, extra columns allowed.", ""]

    def block(title, group):
        rows = ["| " + title + " | Questions | Strict | Lenient | Lenient 95% CI | AI-service errors |",
                "|---|---|---|---|---|---|"]
        for name, items in group:
            n = len(items)
            strict = sum(bool(c.get("strict")) for c in items)
            lenient = sum(bool(c.get("passed")) for c in items)
            infra = sum(is_infra(c) for c in items)
            low, high = wilson(lenient, n)
            rows.append(f"| {name} | {n} | {pct(strict, n)} ({strict}) | **{pct(lenient, n)}** ({lenient}) | "
                        f"{100 * low:.1f}% to {100 * high:.1f}% | {infra} |")
        return rows

    by_suite = defaultdict(list)
    for c in answered:
        by_suite[c["suite"]].append(c)
    suite_names = {"spider": "Spider dev (unseen databases)", "own": "Own gold set (shop.sqlite)"}
    groups = [(suite_names.get(k, k), v) for k, v in sorted(by_suite.items(), reverse=True)]
    groups.append(("All", answered))
    lines += ["## Overall", ""] + block("Test set", groups) + [""]

    for suite, items in sorted(by_suite.items(), reverse=True):
        by_level = [(lvl, [c for c in items if c.get("difficulty") == lvl]) for lvl in LEVELS]
        lines += [f"## {suite_names.get(suite, suite)}: by difficulty", ""]
        lines += block("Difficulty", [g for g in by_level if g[1]]) + [""]
        by_db = defaultdict(list)
        for c in items:
            by_db[c["database"]].append(c)
        lines += [f"## {suite_names.get(suite, suite)}: by database", ""]
        lines += block("Database", sorted(by_db.items())) + [""]

    failed = [c for c in answered if not c.get("passed")]
    causes = Counter((c.get("diagnosis") or c.get("status") or "unknown") for c in failed)
    lines += ["## Why questions failed", "", "| Cause | Count |", "|---|---|"]
    lines += [f"| {cause} | {count} |" for cause, count in causes.most_common()] + [""]

    lines += ["## Every failed question", ""]
    for c in failed:
        lines += [f"### {c['id']} ({c['database']}, {c.get('difficulty')})", "",
                  f"- Question: {c.get('question')}",
                  f"- Status: {c.get('status')}; cause: {c.get('diagnosis') or '-'}",
                  f"- Detail: {(c.get('diagnosis_detail') or c.get('reason') or c.get('error') or '-')[:400]}",
                  "", "Gold SQL:", "```sql", (c.get("expected_sql") or "").strip(), "```",
                  "InsightX SQL:", "```sql", (c.get("predicted_sql") or "(none)").strip(), "```", ""]
    text = "\n".join(lines)
    (run_dir / "SUMMARY.md").write_text(text, encoding="utf-8")
    (run_dir / "all_cases.json").write_text(json.dumps(cases, indent=2, default=str), encoding="utf-8")
    (RESULTS / "LATEST_SUMMARY.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="InsightX accuracy test")
    parser.add_argument("--suite", choices=("spider", "own", "all"), default="all")
    parser.add_argument("--limit", type=int, default=0, help="questions per database (0 = all)")
    parser.add_argument("--resume", action="store_true", help="continue the last run")
    args = parser.parse_args()

    from backend.ai_gateway import get_gateway
    import backend.config  # noqa: F401  (loads .env)
    from backend import dialects
    from backend.database import create_sqlite_engine
    from backend.evaluation.golden import load_golden_set
    from backend.evaluation.runner import run_benchmark, write_report

    get_gateway().settings.cache_enabled = False
    model = f"{os.getenv('LLM_PROVIDER', '?')} / {os.getenv('OPENROUTER_SQL_MODEL') or os.getenv('LLM_SQL_MODEL') or 'default'}"

    RESULTS.mkdir(parents=True, exist_ok=True)
    previous = sorted(p for p in RESULTS.glob("run-*") if p.is_dir())
    if args.resume and previous:
        run_dir = previous[-1]
        print(f"Continuing {run_dir.name}")
    else:
        run_dir = RESULTS / f"run-{datetime.now():%Y%m%d-%H%M%S}"
        run_dir.mkdir(parents=True)
    started_at = run_dir.name[4:]

    sets = test_sets(args.suite)
    if args.suite in ("spider", "all") and not any(s == "spider" for s, _, _ in sets):
        print("No Spider questions yet. Run PREPARE_SPIDER.bat (python evals/prepare_spider.py) first.")
        if args.suite == "spider":
            return 1

    all_cases = []
    for suite, golden_path, db_path in sets:
        if not db_path.exists():
            print(f"SKIP {golden_path.name}: database file not found ({db_path})")
            continue
        golden = load_golden_set(golden_path)
        ids = [c.id for c in golden.cases][: args.limit] if args.limit else None
        partial = run_dir / f"{golden.name}.partial.jsonl"
        if args.resume:
            again = _drop_infra_failures(partial)
            if again:
                print(f"{golden.name}: asking {again} question(s) again that failed because of the AI service")
        engine = create_sqlite_engine(str(db_path))
        count = len(ids) if ids else len(golden.cases)
        print(f"\n=== {golden.name}: {count} questions ===", flush=True)

        def progress(case):
            mark = "PASS" if case.passed else "FAIL"
            why = "" if case.passed else "  <- " + (case.diagnosis or case.reason or case.error or case.status)[:90]
            print(f"  {mark} {case.id} ({case.difficulty}) {case.latency_s}s{why}", flush=True)
            # All free models out of their per-minute limit: wait, so the next
            # questions do not fail the same way. --resume asks this one again.
            if not case.passed and INFRA_PATTERN.search(str(case.error or "")):
                print(f"  (AI service limit, waiting {LIMIT_WAIT_S}s)", flush=True)
                time.sleep(LIMIT_WAIT_S)

        try:
            with dialects.using(engine):
                report = run_benchmark(golden, engine, ids=ids, partial_path=partial, on_case=progress,
                                       database_label=f"{suite}:{db_path.name}")
        finally:
            engine.dispose()
        write_report(report, run_dir)
        for case in report.get("cases") or []:
            item = _case_dict(case)
            item["suite"] = suite
            item["database"] = golden.name
            all_cases.append(item)

    if not all_cases:
        print("Nothing was run.")
        return 1
    summarize(all_cases, run_dir, started_at, model)
    answered = [c for c in all_cases if c.get("expect", "answer") == "answer"]
    lenient = sum(bool(c.get("passed")) for c in answered)
    strict = sum(bool(c.get("strict")) for c in answered)
    infra = sum(is_infra(c) for c in answered)
    print("\n" + "=" * 60)
    print(f"RESULT: {lenient}/{len(answered)} correct = {pct(lenient, len(answered))} (lenient), "
          f"{pct(strict, len(answered))} strict (Spider rule)")
    if infra:
        print(f"{infra} question(s) failed because of the AI service (rate limit / timeout). "
              f"Run CONTINUE_ACCURACY_TEST.bat to ask them again.")
    print(f"Full report: {run_dir / 'SUMMARY.md'}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
