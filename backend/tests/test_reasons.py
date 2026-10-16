# "Real reason" checks and follow-up questions. A synthetic shop that
# grows every month with a November sale spike each year: December
# "drops" only because November was a one-off, the same thing happened
# a year earlier, and returning customers are the ones who stayed away.

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import text

from backend.investigation.engine import Investigation
from backend.investigation.narrative import fallback_narrative, fallback_real_reason
from backend.investigation.planner import verify_plan
from backend.investigation.reasons import customer_column, find_reasons
from backend.text_to_sql.follow_ups import simple_follow_ups, suggest_follow_ups
from investigation_view import reasons_html

TABLES = "itest_rs_orders, itest_rs_customers"
SETUP = [
    f"DROP TABLE IF EXISTS {TABLES}",
    # 26 months from 2016-01; 100 + 4 per month orders, November x1.6.
    # Every 4th order is by one of 50 returning customers, except in
    # December when returning customers stay away.
    """
    CREATE TABLE itest_rs_orders AS
    SELECT row_number() OVER () AS order_id, m, s,
           (DATE '2016-01-01' + (m || ' month')::interval + ((s % 27) || ' day')::interval) AS purchased_at,
           (2 + s % 5)::numeric AS delivery_days
    FROM generate_series(0, 25) m,
         LATERAL generate_series(1, ((100 + 4 * m) * CASE WHEN m % 12 = 10 THEN 1.6 ELSE 1 END)::int) s
    """,
    """
    CREATE TABLE itest_rs_customers AS
    SELECT order_id AS customer_id,
           CASE WHEN s % 4 = 0 AND m % 12 <> 11 THEN 'r' || (s % 50) ELSE 'n' || m || '_' || s END AS customer_unique_id,
           (ARRAY['SP','RJ','MG'])[1 + s % 3] AS state
    FROM itest_rs_orders
    """,
    "ALTER TABLE itest_rs_customers ADD PRIMARY KEY (customer_id)",
    "ALTER TABLE itest_rs_orders ADD PRIMARY KEY (order_id)",
    "ALTER TABLE itest_rs_orders ADD COLUMN customer_id bigint",
    "UPDATE itest_rs_orders SET customer_id = order_id",
    "ALTER TABLE itest_rs_orders ADD FOREIGN KEY (customer_id) REFERENCES itest_rs_customers (customer_id)",
    "ALTER TABLE itest_rs_orders DROP COLUMN m, DROP COLUMN s",
]

RAW_PLAN = {
    "metric_name": "orders",
    "metric_expression": "COUNT(itest_rs_orders.order_id)",
    "fact_table": "itest_rs_orders",
    "time_column": "itest_rs_orders.purchased_at",
    "grain": "month",
    "direction": "decrease",
    "current_period": "2017-12-01",
    "dimensions": ["itest_rs_customers.state"],
    "filters": None,
}


@pytest.fixture(scope="module")
def shop(engine):
    with engine.begin() as connection:
        for statement in SETUP:
            connection.execute(text(statement))
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql.schema_context import build_agent_schema, cache_schema_from_profile

    profile = get_database_schema(engine)
    cache_schema_from_profile(engine, profile)
    yield build_agent_schema(profile)
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {TABLES}"))


@pytest.fixture(scope="module")
def checked(engine, shop):
    plan = verify_plan(RAW_PLAN, shop, "Why did orders drop in December 2017?", engine)
    investigation = Investigation(plan, shop, engine)
    report = investigation.run()
    report.update(find_reasons(investigation, report))
    return investigation, report


def test_compares_december_with_november(checked):
    _, report = checked
    h = report["headline"]
    assert (h["baseline_label"], h["current_label"]) == ("2017-11", "2017-12")
    assert h["direction"] == "decrease"


def test_one_off_spike_is_the_most_likely_reason(checked):
    _, report = checked
    top = report["reasons"][0]
    assert top["kind"] == "unusual_before"
    assert top["likelihood"] == "Most likely"
    assert top["strength"] >= 60
    assert "2017-11" in top["title"] and "return to normal" in top["title"]


def test_same_drop_a_year_earlier_is_found(checked):
    _, report = checked
    seasonal = next(r for r in report["reasons"] if r["kind"] == "seasonal")
    assert "2016-11" in seasonal["detail"] and "2016-12" in seasonal["detail"]


def test_returning_customers_are_checked(checked):
    investigation, report = checked
    assert customer_column(investigation) == ("itest_rs_customers", "customer_unique_id")
    found = [r for r in report["reasons"] + report["ruled_out"] if r["kind"] == "customers"]
    assert found and found[0]["evidence"]["returning_customers_after"] == 0
    assert any(q["purpose"] == "New vs returning customers" for q in report["queries"])


def test_reasons_are_in_the_facts_and_the_view(checked):
    _, report = checked
    from backend.investigation.narrative import fact_table

    facts = [row["fact"] for row in fact_table(report)["rows"]]
    assert report["reasons"][0]["title"] in facts
    assert fallback_real_reason(report).startswith(report["reasons"][0]["title"])
    assert fallback_narrative(report)["real_reason"]
    page = reasons_html(report)
    assert "Most likely" in page and "evidence strength" in page


def test_no_reasons_no_section():
    assert reasons_html({"reasons": [], "ruled_out": []}) == ""


class Reply:
    def __init__(self, content):
        self.content = content

    def invoke(self, prompt):
        if isinstance(self.content, Exception):
            raise self.content
        return AIMessage(content=self.content)


RESULT = {
    "status": "SUCCESS",
    "generated_sql": "SELECT state, COUNT(*) AS orders FROM orders GROUP BY state",
    "answer": "SP has the most orders.",
    "execution_result": {"columns": ["state", "orders"], "rows": [{"state": "SP", "orders": 10}, {"state": "RJ", "orders": 5}]},
}


def test_follow_ups_come_from_the_ai_and_are_cleaned():
    reply = Reply('```json\n["1. Orders by month for SP", "Which states grew the most?", '
                  '"Which state has the most orders?", "Which states grew the most?"]\n```')
    found = suggest_follow_ups("Which state has the most orders?", RESULT, model=reply)
    assert found == ["Orders by month for SP?", "Which states grew the most?"]


def test_follow_ups_without_the_ai():
    found = suggest_follow_ups("Which state has the most orders?", RESULT, model=Reply(RuntimeError("down")))
    assert found == simple_follow_ups("Which state has the most orders?", RESULT)
    assert 2 <= len(found) <= 4 and all(q.endswith("?") for q in found)
    assert suggest_follow_ups("x", {"status": "ERROR"}) == []
