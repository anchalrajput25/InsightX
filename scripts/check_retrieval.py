"""Free check (no AI calls): does InsightX show the right tables for each test question?

    python scripts/check_retrieval.py --db insightx_big --golden backend/evaluation/golden/warehouse.yaml

For every question with expected SQL, the tables that SQL uses must all
be in the part of the schema InsightX would send to the AI.
"""

import argparse
import getpass
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import sqlglot  # noqa: E402
import yaml  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402

from backend.config import query_settings  # noqa: E402
from backend.schema_profiler import get_database_schema  # noqa: E402
from backend.text_to_sql.schema_context import build_agent_schema  # noqa: E402
from backend.text_to_sql.schema_focus import build_schema_focus  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--golden", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--user", default="postgres")
    args = parser.parse_args()
    logging.disable(logging.WARNING)

    password = os.environ.get("PGPASSWORD") or getpass.getpass(f"PostgreSQL password for {args.user}: ")
    engine = create_engine(URL.create("postgresql+psycopg2", username=args.user, password=password,
                                      host=args.host, port=args.port, database=args.db))
    started = time.time()
    schema = build_agent_schema(get_database_schema(engine))
    print(f"Read {len(schema['tables'])} tables in {time.time() - started:.0f}s\n")

    cases = yaml.safe_load(open(args.golden, encoding="utf-8"))["cases"]
    found = total = 0
    for case in cases:
        if not case.get("expected_sql") or (case.get("thread") and case is not next(
                c for c in cases if c.get("thread") == case["thread"])):
            continue  # follow-ups depend on the earlier answer
        needed = {table.name for table in sqlglot.parse_one(case["expected_sql"], read="postgres").find_all(sqlglot.exp.Table)}
        focus = build_schema_focus(
            schema=schema, queries=[case["question"]], question=case["question"],
            top_k=query_settings.retrieval_top_k, relative_cutoff=query_settings.retrieval_relative_cutoff,
            wide_table_columns=query_settings.wide_table_columns,
        )
        shown = {table["table_name"] for table in focus["focused_schema"]["tables"]}
        ok = needed <= shown
        found += ok
        total += 1
        missing = "" if ok else f"   missing: {', '.join(sorted(needed - shown))}"
        print(f"{'OK  ' if ok else 'MISS'} {case['id']}  ({len(shown)} tables shown){missing}")
    print(f"\n{found}/{total} questions get every table they need.")


if __name__ == "__main__":
    main()
