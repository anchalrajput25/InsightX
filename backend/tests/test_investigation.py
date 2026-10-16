# Investigation mode: a synthetic shop where the cause of a drop is
# planted (one region + one category collapse in the last month, with
# slower shipping and more cancellations there). The engine must find
# exactly that, never double count, and the narrative must stay
# within the computed facts.

import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import text

from backend.investigation.engine import Investigation, InvestigationError, period_label
from backend.investigation.narrative import facts_summary, narrate
from backend.investigation.planner import looks_like_investigation, verify_plan
from backend.investigation.service import run_investigation

SETUP = [
    "DROP TABLE IF EXISTS itest_inv_sales, itest_inv_regions, itest_inv_products",
    "CREATE TABLE itest_inv_regions AS SELECT g AS region_id, (ARRAY['north','south','east','west'])[g] AS region_name "
    "FROM generate_series(1, 4) g",
    "CREATE TABLE itest_inv_products AS SELECT g AS product_id, (ARRAY['toys','books','garden'])[1 + g % 3] AS category "
    "FROM generate_series(1, 30) g",
    # 6 months, 40 sales per region/category/month; amount 10.
    # Last month (2024-06): north x toys sells 2 instead of 40 and ships slowly.
    """
    CREATE TABLE itest_inv_sales AS
    SELECT row_number() OVER () AS sale_id, r AS region_id, p AS product_id,
           10.0 + (s % 3) AS amount,
           (DATE '2024-01-01' + (m || ' month')::interval + ((s % 25) || ' day')::interval) AS sold_at,
           CASE WHEN m = 5 AND r = 1 THEN 9.0 ELSE 3.0 + (s % 2) END AS ship_days,
           CASE WHEN m = 5 AND r = 1 AND s % 2 = 0 THEN 'canceled' ELSE 'delivered' END AS status
    FROM generate_series(0, 5) m, generate_series(1, 4) r, generate_series(1, 3) c,
         generate_series(1, 40) s,
         LATERAL (SELECT CASE c WHEN 1 THEN 3 WHEN 2 THEN 1 ELSE 2 END AS p) AS prod
    WHERE NOT (m = 5 AND r = 1 AND c = 1 AND s > 2)
    """,
]


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
        connection.execute(text("DROP TABLE IF EXISTS itest_inv_sales, itest_inv_regions, itest_inv_products"))


RAW_PLAN = {
    "metric_name": "revenue",
    "metric_expression": "SUM(itest_inv_sales.amount)",
    "fact_table": "itest_inv_sales",
    "time_column": "itest_inv_sales.sold_at",
    "grain": "month",
    "direction": "decrease",
    "dimensions": ["itest_inv_regions.region_name", "itest_inv_products.category"],
    "filters": None,
}


def test_routing_hint():
    assert looks_like_investigation("Why did revenue drop in June?")
    assert looks_like_investigation("What caused the decline in orders?")
    assert not looks_like_investigation("What is the total revenue?")
    assert not looks_like_investigation("Why is the sky blue?")
    # other ways people ask "why" or "what to do"
    for question in ("Why are sales low in 2018?", "How can I improve sales?", "How to increase revenue in Delhi",
                     "What is the reason for fewer orders?", "sales kyu gire", "sales kaise badhaye",
                     "Why do customers leave?"):
        assert looks_like_investigation(question), question
    for question in ("Which genre earned the most money?", "Show sales by month", "How can I see the orders table?"):
        assert not looks_like_investigation(question), question


def test_period_labels():
    assert period_label("2024-06-01", "month") == "2024-06"
    assert period_label("2024-04-01", "quarter") == "2024-Q2"
    assert period_label("2024-01-01", "year") == "2024"


# ============================================================
# PLAN VERIFICATION
# ============================================================

def test_plan_is_verified_and_completed(engine, shop):
    plan = verify_plan(RAW_PLAN, shop, "why", engine)
    assert plan.metric.kind == "additive"
    assert plan.metric.expression == 'SUM("itest_inv_sales"."amount")'
    assert plan.dimensions[:2] == [("itest_inv_regions", "region_name"), ("itest_inv_products", "category")]
    # status was added automatically; ids, metric columns and time columns never are
    assert ("itest_inv_sales", "status") in plan.dimensions
    assert all(not column.endswith("_id") and column != "amount" for _, column in plan.dimensions)


@pytest.mark.parametrize("change, message", [
    ({"metric_expression": "SUM(itest_inv_sales.nope)"}, "does not exist"),
    ({"metric_expression": "itest_inv_sales.amount * 2"}, "must aggregate"),
    ({"metric_expression": "SUM((SELECT 1))"}, "subquery"),
    ({"metric_expression": None}, "Could not identify"),
    ({"filters": "itest_inv_regions.region_name = 'North'"}, "not valid"),
])
def test_bad_plans_are_rejected(engine, shop, change, message):
    with pytest.raises(InvestigationError, match=message):
        verify_plan({**RAW_PLAN, **change}, shop, "why", engine)


def test_unknown_time_column_falls_back_to_a_real_one(engine, shop):
    plan = verify_plan({**RAW_PLAN, "time_column": "itest_inv_sales.when"}, shop, "why", engine)
    assert (plan.time_table, plan.time_column) == ("itest_inv_sales", "sold_at")
    assert plan.notes


# ============================================================
# ANALYSIS
# ============================================================

@pytest.fixture(scope="module")
def report(engine, shop):
    plan = verify_plan(RAW_PLAN, shop, "Why did revenue drop?", engine)
    return Investigation(plan, shop, engine).run()


def test_headline_compares_the_last_two_complete_months(report):
    h = report["headline"]
    assert (h["baseline_label"], h["current_label"]) == ("2024-05", "2024-06")
    assert h["direction"] == "decrease" and h["change"] == pytest.approx(-417.0, abs=0.01)
    assert h["unusual"] == "unusual"


def test_planted_drivers_are_found_and_contributions_add_up(report):
    by_dimension = {driver["dimension"]: driver for driver in report["drivers"]}
    region = by_dimension["itest_inv_regions.region_name"]
    category = by_dimension["itest_inv_products.category"]
    assert region["leading_segment"] == "north"
    assert category["leading_segment"] == "toys"
    assert region["top"][0]["share_of_change_pct"] == pytest.approx(100.0)
    # additive: segment contributions sum exactly to the total change
    total = sum(item["contribution"] for item in region["top"]) + ((region["other"] or {}).get("contribution") or 0)
    assert total == pytest.approx(report["headline"]["change"], abs=0.01)
    # the top dimension is one of the planted ones (cancellations were
    # planted in the same cell), never one that changed proportionally
    assert report["drivers"][0]["dimension"] in (
        "itest_inv_regions.region_name", "itest_inv_products.category", "itest_inv_sales.status")


def test_volume_value_split(report):
    vv = report["volume_value"]
    assert vv["main_factor"] == "records" and vv["records_change_pct"] < 0


def test_drilldown_breaks_the_top_driver_down(report):
    drill = report["drilldown"]
    assert drill["rows_dimension"] == report["drivers"][0]["dimension"]
    assert drill["top_segment"] == report["drivers"][0]["leading_segment"]
    assert drill["cells"] and drill["top_inside"]
    # inside the planted cell, the other planted dimension is the biggest mover
    if drill["top_segment"] in ("north", "toys"):
        assert drill["top_inside"][0]["column"] in ("north", "toys")


def test_signals_point_at_shipping_and_cancellations(report):
    names = [signal["signal"] for signal in report["signals"]]
    assert "avg ship_days" in names
    ship = next(signal for signal in report["signals"] if signal["signal"] == "avg ship_days")
    assert ship["overall_change"] > 0 and ship["segment_change"] > 0
    assert any(name == "share status = canceled" for name in names)


def test_breakdowns_never_double_count(engine, shop, report):
    # every breakdown query keeps fact rows 1:1 (N:1 joins only)
    for query in report["queries"]:
        assert "itest_inv_sales" in query["sql"]
        assert "JOIN \"itest_inv_sales\"" not in query["sql"]


def test_average_metrics_get_mix_and_rate_effects(engine, shop):
    plan = verify_plan({**RAW_PLAN, "metric_name": "avg ship days", "metric_expression": "AVG(itest_inv_sales.ship_days)",
                        "direction": "increase"}, shop, "why", engine)
    result = Investigation(plan, shop, engine).run()
    region = next(d for d in result["drivers"] if d["dimension"] == "itest_inv_regions.region_name")
    assert region["leading_segment"] == "north"
    assert region["mix_effect"] + region["rate_effect"] == pytest.approx(result["headline"]["change"], abs=1e-3)


def test_facts_summary_is_deterministic_text(report):
    facts = facts_summary(report)
    assert facts[0].startswith("revenue fell from")
    assert any("'north' accounts for 100% of the change" in fact for fact in facts)


# ============================================================
# NARRATIVE AND SERVICE
# ============================================================

class Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return AIMessage(content=self.responses.pop(0))


def test_narrative_numbers_are_checked_and_regenerated(report):
    bad = json.dumps({"summary": "Revenue dropped 999 because of north.", "drivers": [], "hypotheses": [], "next_steps": []})
    good = json.dumps({"summary": "Revenue fell 417 because north stopped selling toys.", "drivers": ["north explains 100% of the change"],
                       "hypotheses": [{"hypothesis": "Check stock of toys in north", "evidence": "ship_days rose"}],
                       "next_steps": ["Compare north toys inventory"]})
    model = Scripted(bad, good)
    story = narrate(report, "Why did revenue drop?", model=model)
    assert len(model.prompts) == 2 and "999" in model.prompts[1]
    assert story["claim_check"]["status"] == "verified"
    assert story["narrative"]["summary"].startswith("Revenue fell 417")


def test_run_investigation_end_to_end(engine, shop, keyword_retrieval):
    planner = Scripted("```json\n" + json.dumps(RAW_PLAN) + "\n```")
    narrator = Scripted(json.dumps({"summary": "Revenue fell 417.", "drivers": [], "hypotheses": [], "next_steps": []}))
    result = run_investigation("Why did revenue drop?", engine, planner_model=planner, narrative_model=narrator)
    assert result["status"] == "SUCCESS"
    assert result["trust"]["level"] == "high"
    assert result["facts"] and result["queries"]
    assert "itest_inv_sales" in planner.prompts[0]


def test_unusable_plan_is_a_clear_error(engine, shop, keyword_retrieval):
    with pytest.raises(InvestigationError, match="Could not identify"):
        run_investigation("Why?", engine, planner_model=Scripted("I am not sure."), narrative_model=Scripted())


def test_investigate_endpoint(engine, shop, keyword_retrieval, monkeypatch):
    import uuid
    from fastapi.testclient import TestClient

    from backend import main
    from backend.investigation import narrative, planner

    monkeypatch.setattr(planner, "llm", Scripted(json.dumps(RAW_PLAN), "no plan here"))
    monkeypatch.setattr(narrative, "llm", Scripted(json.dumps({"summary": "Revenue fell 417.", "drivers": [],
                                                                  "hypotheses": [], "next_steps": []})))
    connection_id = f"inv-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": engine})
    client = TestClient(main.app)

    response = client.post("/investigate", json={"connection_id": connection_id, "question": "Why did revenue drop?"})
    assert response.status_code == 200
    body = response.json()
    assert body["headline"]["current_label"] == "2024-06" and body["trust"]["level"]
    failed = client.post("/investigate", json={"connection_id": connection_id, "question": "Why?"})
    assert failed.status_code == 422 and "Could not identify" in failed.json()["detail"]
    assert client.get("/investigate/route", params={"question": "Why did sales fall?"}).json()["investigation"]


def test_every_chart_builds_from_a_report(report):
    import investigation_view as view

    charts = [view.trend_chart(report)] + [view.signals_chart(report, kind) for kind in ("average", "time_gap", "share")]
    for driver in report["drivers"]:
        if driver.get("total_change") is not None:
            charts += [view.waterfall_chart(report, driver), view.contribution_chart(driver, "revenue")]
    if report["drilldown"]:
        charts.append(view.heatmap_chart(report["drilldown"], "revenue"))
    specs = [chart.to_dict() for chart in charts if chart is not None]
    assert len(specs) >= 5
    html = view.hero_html({**report, "headline": {**report["headline"]}})
    assert "2024-06" in html and "font-family:system-ui, -apple-system, 'Segoe UI'" in html


def test_dark_theme_charts_use_light_text(report):
    import investigation_view as view

    dark = view.theme_for(True)
    spec = view.trend_chart(report, t=dark).to_dict()
    assert dark.ink.lower() in str(spec).lower()
    assert view.LIGHT.ink.lower() not in str(spec).lower()
    assert dark.ink.lower() in view.hero_html(report, dark).lower()


class Failing:
    def invoke(self, prompt):
        raise TimeoutError("model timed out")


def test_why_answer_survives_a_failed_explanation(engine, shop, keyword_retrieval):
    planner = Scripted(json.dumps(RAW_PLAN))
    result = run_investigation("Why did revenue drop?", engine, planner_model=planner, narrative_model=Failing())
    assert result["narrative_source"] == "computed" and "TimeoutError" in result["narrative_error"]
    assert "fell" in result["narrative"]["summary"]
    assert any(line.startswith("By ") for line in result["narrative"]["drivers"])


def test_prose_reply_is_used_as_the_summary(report):
    story = narrate(report, "Why?", model=Scripted("Revenue fell 417 mainly because the north region stopped selling."))
    assert story["narrative"]["summary"].startswith("Revenue fell 417")


def test_timestamp_differences_become_days(engine, shop):
    from datetime import timedelta
    from backend.investigation.engine import _num

    plan = verify_plan({**RAW_PLAN, "metric_name": "gap",
                        "metric_expression": "AVG(itest_inv_sales.sold_at - itest_inv_sales.sold_at)"}, shop, "why", engine)
    assert "EXTRACT(EPOCH FROM" in plan.metric.expression and "86400" in plan.metric.expression
    assert _num(timedelta(days=2, hours=12)) == 2.5


def test_metric_without_values_is_a_clear_error(engine, shop):
    plan = verify_plan({**RAW_PLAN, "metric_name": "nothing",
                        "metric_expression": "AVG(CASE WHEN itest_inv_sales.status = 'never' THEN itest_inv_sales.amount END)",
                        "current_period": "2024-06-01", "baseline_period": "2024-05-01"},
                       shop, "why", engine)
    with pytest.raises(InvestigationError, match="no numeric value"):
        Investigation(plan, shop, engine).run()


def test_how_to_improve_without_the_ai(report):
    from backend.investigation.narrative import fallback_narrative

    actions = fallback_narrative(report)["actions"]
    top = report["drivers"][0]
    assert actions and f"'{top['leading_segment']}'" in actions[0]["action"] and "Focus first" in actions[0]["action"]
    assert actions[0]["why"].startswith("It explains the largest part of the change in revenue")


def test_how_to_improve_numbers_are_checked(report):
    good = json.dumps({"summary": "Revenue fell 417.", "drivers": [], "hypotheses": [], "next_steps": [],
                       "actions": [{"action": "Restock toys in north", "why": "north explains 100% of the change"}]})
    bad = json.dumps({"summary": "Revenue fell 417.", "drivers": [], "hypotheses": [], "next_steps": [],
                      "actions": [{"action": "Cut prices by 999 in north", "why": "north explains 100% of the change"}]})
    model = Scripted(bad, good)
    story = narrate(report, "How can we improve revenue?", model=model)
    assert len(model.prompts) == 2 and "999" in model.prompts[1]
    assert story["narrative"]["actions"][0]["action"] == "Restock toys in north"
