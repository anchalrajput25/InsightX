# backend/evaluation/__main__.py
#
# Command line for the evaluation harness. Works with any PostgreSQL
# database the agent can query.
#
#   Check a golden set (no LLM calls):
#     python -m backend.evaluation --db olist check --golden backend/evaluation/golden/olist.yaml
#
#   Benchmark the agent (calls the configured LLM):
#     python -m backend.evaluation --db olist run --golden backend/evaluation/golden/olist.yaml
#
#   ...with ablations, 3 repeats, 4 parallel questions and a $2 cap:
#     python -m backend.evaluation --db olist run --golden backend/evaluation/golden/olist.yaml \
#         --ablation --repeats 3 --workers 4 --max-cost 2
#
#   ...or custom variants (see variants.py):
#     --variant baseline --variant single:candidates=1 --variant nofix:correction=0
#
#   Resume a run that stopped (crash, Ctrl+C, cost cap):
#     ... run --golden ... --resume logs/evaluation/olist-20261009-231500.partial.jsonl
#
#   Propose candidate cases for a new database (review before use):
#     python -m backend.evaluation --db my_db generate --name my_db --count 30 \
#         --out backend/evaluation/golden/my_db.yaml
#
# Connecting: --db NAME (with optional --host/--port/--user; the
# password is read from PGPASSWORD or asked for, never echoed), or a
# full SQLAlchemy URL in --database-url / INSIGHTX_EVAL_DATABASE_URL.
# Passwords never appear in output or reports.

import argparse
import getpass
import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from backend.config import PROJECT_ROOT, database_settings
from backend.evaluation.golden import DIFFICULTIES, GoldenSetError, load_golden_set, save_golden_set

DEFAULT_REPORT_DIR = PROJECT_ROOT / "logs" / "evaluation"

# --ablation: what is each component worth?
ABLATION_PRESET = [
    "baseline",
    "always_all_candidates:adaptive=0",
    "single_candidate:candidates=1",
    "no_correction:correction=0",
    "minimal:candidates=1,correction=0",
]


def _url(args) -> URL:
    raw = args.database_url or os.getenv("INSIGHTX_EVAL_DATABASE_URL")
    if raw:
        return make_url(raw)
    if not args.db:
        sys.exit("Give --db NAME (or --database-url / INSIGHTX_EVAL_DATABASE_URL).")
    password = os.getenv("PGPASSWORD")
    if password is None and sys.stdin.isatty():
        password = getpass.getpass(f"PostgreSQL password for {args.user}@{args.host} (empty for none): ")
    return URL.create("postgresql+psycopg2", username=args.user, password=password or None,
                      host=args.host, port=args.port, database=args.db)


def _engine(args):
    url = _url(args)
    args.database_label = f"{url.host or 'localhost'}:{url.port or 5432}/{url.database}"
    return create_engine(url, pool_pre_ping=True, pool_size=max(5, getattr(args, "workers", 1) * 2),
                         connect_args={"connect_timeout": database_settings.connect_timeout_s})


def _check(args) -> int:
    from backend.evaluation.runner import check_golden_set

    golden = load_golden_set(args.golden)
    report = check_golden_set(golden, _engine(args))
    bad = [item for item in report if not item["ok"]]
    for item in report:
        if item.get("skipped"):
            print(f"SKIP {item['id']}  ({item['skipped']})")
            continue
        print(("OK   " if item["ok"] else "FAIL ") + item["id"] + ("" if item["ok"] else "  -> " + "; ".join(item["problems"])))
    print(f"\n{len(report) - len(bad)}/{len(report)} cases are usable.")
    return 1 if bad else 0


def _split(value):
    return [item.strip() for item in value.split(",") if item.strip()] if value else None


def _run(args) -> int:
    from backend.evaluation.runner import render_benchmark_markdown, run_benchmark, write_report
    from backend.evaluation.variants import VariantError, parse_variants

    golden = load_golden_set(args.golden)
    specs = list(args.variant or [])
    if args.ablation:
        specs = ABLATION_PRESET + [spec for spec in specs if spec.split(":")[0] not in
                                   {s.split(":")[0] for s in ABLATION_PRESET}]
    try:
        variants = parse_variants(specs)
    except VariantError as exc:
        sys.exit(str(exc))

    difficulties = _split(args.difficulty)
    if difficulties and set(difficulties) - set(DIFFICULTIES):
        sys.exit(f"--difficulty takes {', '.join(DIFFICULTIES)}.")
    out_dir = Path(args.out)
    partial = Path(args.resume) if args.resume else out_dir / f"{golden.name}-{datetime.now():%Y%m%d-%H%M%S}.partial.jsonl"
    engine = _engine(args)
    selected = golden.selected(args.include_unverified, _split(args.tags), difficulties, _split(args.ids))
    print(f"{golden.name}: {len(selected)} cases x {args.repeats} repeat(s) x {len(variants)} variant(s) "
          f"on {args.database_label}, {args.workers} worker(s).")
    print(f"Progress is saved to {partial} (resume with --resume {partial}).\n")

    # profile the schema and build the retrieval index once, before
    # parallel workers would each do it on their first question
    from backend.text_to_sql.retriever import warm_schema_index
    from backend.text_to_sql.schema_context import get_agent_schema

    # Saved AI answers would make repeats and variants replay the first
    # run instead of measuring the model, so the benchmark never uses them.
    from backend.ai_gateway import get_gateway

    get_gateway().settings.cache_enabled = False

    print("Reading the database schema...", flush=True)
    schema = get_agent_schema(engine)
    warm_schema_index(schema)
    print(f"Schema ready: {len(schema.get('tables', []))} tables.\n", flush=True)

    def progress(case):
        mark = "PASS" if case.passed else "FAIL"
        cost = f" ${case.cost_usd:.4f}" if case.cost_usd else ""
        detail = "" if case.passed else " -> " + (case.diagnosis or case.reason or case.error)[:100]
        print(f"[{case.variant} #{case.repeat}] {mark} {case.id} ({case.difficulty}, {case.expect}) "
              f"{case.latency_s}s{cost}{detail}", flush=True)

    try:
        report = run_benchmark(
            golden, engine, variants=variants, repeats=args.repeats, workers=args.workers,
            include_unverified=args.include_unverified, tags=_split(args.tags), difficulties=difficulties,
            ids=_split(args.ids), partial_path=partial, max_cost_usd=args.max_cost, on_case=progress,
            database_label=args.database_label,
        )
    except KeyboardInterrupt:
        print(f"\nStopped. Resume with: --resume {partial}")
        return 130
    paths = write_report(report, out_dir)
    print("\n" + render_benchmark_markdown(report).split("## Detail:")[0])
    print(f"Report: {paths['markdown']}\nData:   {paths['json']}  (open the Benchmark page in the app to explore it)")
    return 0


def _generate(args) -> int:
    from backend.evaluation.generate import generate_candidates

    outcome = generate_candidates(_engine(args), name=args.name, count=args.count)
    out = Path(args.out)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists; use --force to overwrite.")
    out.parent.mkdir(parents=True, exist_ok=True)
    save_golden_set(outcome["golden"], out)
    print(f"Kept {len(outcome['golden'].cases)} candidates -> {out}")
    for item in outcome["rejected"]:
        print(f"Rejected: {item['question']} ({item['reason'][:100]})")
    print("Review every case, fix or delete wrong ones, and set verified: true.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.evaluation", description="InsightX evaluation harness")
    parser.add_argument("--database-url", help="SQLAlchemy URL (default: INSIGHTX_EVAL_DATABASE_URL)")
    parser.add_argument("--db", help="database name (with --host/--port/--user)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="postgres")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="verify the expected SQL of a golden set (no LLM)")
    check.add_argument("--golden", type=Path, required=True)
    check.set_defaults(func=_check)

    run = sub.add_parser("run", help="benchmark the agent on a golden set")
    run.add_argument("--golden", type=Path, required=True)
    run.add_argument("--variant", action="append", help="name[:key=value,...]; repeatable (see variants.py)")
    run.add_argument("--ablation", action="store_true", help="add the standard ablation variants")
    run.add_argument("--repeats", type=int, default=1, help="run every case N times (measures stability)")
    run.add_argument("--workers", type=int, default=1, help="questions answered in parallel")
    run.add_argument("--max-cost", type=float, help="stop starting new questions after this many USD")
    run.add_argument("--tags", help="only cases with these comma-separated tags")
    run.add_argument("--difficulty", help="only these difficulties, e.g. hard,extra")
    run.add_argument("--ids", help="only these comma-separated case ids")
    run.add_argument("--resume", help="partial .jsonl file of an earlier run to continue")
    run.add_argument("--include-unverified", action="store_true")
    run.add_argument("--out", default=str(DEFAULT_REPORT_DIR))
    run.set_defaults(func=_run)

    generate = sub.add_parser("generate", help="propose candidate cases for a database")
    generate.add_argument("--name", required=True)
    generate.add_argument("--count", type=int, default=30)
    generate.add_argument("--out", type=Path, required=True)
    generate.add_argument("--force", action="store_true")
    generate.set_defaults(func=_generate)

    args = parser.parse_args(argv)
    if getattr(args, "repeats", 1) < 1 or getattr(args, "workers", 1) < 1:
        sys.exit("--repeats and --workers must be at least 1.")
    try:
        return args.func(args)
    except GoldenSetError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
