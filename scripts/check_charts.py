"""
Check which chart every benchmark question would get (no AI calls).

Runs each golden case's expected SQL on the database, adds the chart
context rows the backend would add, and prints the chart the UI would
draw. Use it to spot questions that end up with no chart.

    python scripts/check_charts.py --db postgresql+psycopg2://user:pw@localhost/insightx_big \
        --golden backend/evaluation/golden/warehouse.yaml
"""

import argparse
import sys
from pathlib import Path

import yaml
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.text_to_sql.chart_context import chart_context  # noqa: E402
from backend.text_to_sql.query_executor import execute_query  # noqa: E402
from chart_builder import METRIC, plan_chart, to_frame, with_context  # noqa: E402


def chart_for(sql, question, engine):
    execution = execute_query(sql=sql, engine=engine)
    if not execution["success"]:
        return "SQL error", execution["error"].splitlines()[0]
    if not execution["rows"]:
        return "no rows", ""
    rows = jsonable_encoder(execution["rows"])
    context = jsonable_encoder(chart_context(sql, execution, engine))
    answer_plan = plan_chart(to_frame(rows, execution["columns"]), question)
    plan = None
    if context.get("rows"):
        _, plan = with_context(rows, context, question)
    plan = plan or answer_plan
    if plan is None:
        return "NO CHART", f"columns: {', '.join(execution['columns'])}"
    kind = plan.kind
    if answer_plan is not None and answer_plan.kind == METRIC and kind != METRIC:
        kind = f"number + {kind}"
    extra = context.get("kind", "")
    return kind, (f"({extra}) " if extra else "") + (plan.note or "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--golden", nargs="+", required=True)
    args = parser.parse_args()

    engine = create_engine(args.db)
    counts = {}
    for path in args.golden:
        cases = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["cases"]
        for case in cases:
            for step in case.get("turns") or [case]:
                sql = step.get("expected_sql")
                if not sql:
                    continue
                kind, detail = chart_for(sql, step.get("question", ""), engine)
                counts[kind] = counts.get(kind, 0) + 1
                print(f"{kind:<16} {case['id']:<28} {detail}")
    print()
    for kind, count in sorted(counts.items(), key=lambda item: -item[1]):
        print(f"{count:>4}  {kind}")


if __name__ == "__main__":
    main()
