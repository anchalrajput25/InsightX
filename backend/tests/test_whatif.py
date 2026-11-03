# backend/tests/test_whatif.py
#
# What-if scenarios, sensitivity and forecasts on a small SQLite file:
# every scenario number is checked against the same arithmetic done in
# Python on the rows that were inserted. No LLM except a scripted fake.

import json
import math
import sqlite3
import uuid

import pytest

from backend import dialects
from backend.database import create_sqlite_engine
from backend.text_to_sql.sql_safety import check_sql_safety
from backend.whatif import forecast as fc
from backend.whatif.builder import scenario_expression, totals_sql
from backend.whatif.service import run_forecast, run_sweep, run_whatif
from backend.whatif.spec import WhatIfError, verify_scenario

PRODUCTS = [(1, "Toys", "Kite"), (2, "Toys", "Yo-yo"), (3, "Books", "Atlas")]
PRICES = {1: 10.0, 2: 4.0, 3: 25.0}
REGIONS = ["North", "South", "São Paulo"]
MONTHS = 30


def _month(index: int) -> str:
    year, month = divmod(index, 12)
    return f"{2022 + year}-{month + 1:02d}-15"


def _rows():
    rows, next_id = [], 1
    for m in range(MONTHS):
        for product, _, _ in PRODUCTS:
            for r, region in enumerate(REGIONS):
                qty = 1 + (m + product + r) % 4 + m // 10
                days = 3.0 + (product + r) % 3
                rows.append((next_id, product, region, PRICES[product], qty, _month(m), days))
                next_id += 1
    return rows


ROWS = _rows()


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    path = tmp_path_factory.mktemp("whatif") / f"shop_{uuid.uuid4().hex}.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE products (product_id INTEGER PRIMARY KEY, category TEXT, name TEXT);
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            product_id INTEGER REFERENCES products(product_id),
            region TEXT, price REAL, qty INTEGER, sold_at DATE, days REAL);
    """)
    connection.executemany("INSERT INTO products VALUES (?, ?, ?)", PRODUCTS)
    connection.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?)", ROWS)
    connection.commit()
    connection.close()
    engine = create_sqlite_engine(str(path))
    yield engine
    engine.dispose()


REVENUE = {"metric_name": "revenue", "metric_expression": "SUM(sales.price * sales.qty)", "fact_table": "sales"}


def spec(*levers, **extra):
    return {**REVENUE, "levers": list(levers), **extra}


def revenue(rows=ROWS, price=lambda row: row[3], weight=lambda row: 1.0):
    return sum(price(row) * row[4] * weight(row) for row in rows)


def category(row):
    return {p: c for p, c, _ in PRODUCTS}[row[1]]


# ------------------------------------------------------------ levers

def test_a_scenario_without_changes_equals_the_baseline(shop):
    with dialects.using(shop):
        from backend.text_to_sql.schema_context import get_agent_schema

        for metric in ("SUM(sales.price * sales.qty)", "COUNT(*)", "COUNT(sales.qty)", "AVG(sales.price)",
                       "COUNT(DISTINCT sales.region)"):
            scenario = verify_scenario({**REVENUE, "metric_expression": metric, "levers": []}, get_agent_schema(shop), shop)
            assert scenario_expression(scenario) == scenario_expression(scenario, [])


def test_price_rise_scales_revenue(shop):
    report = run_whatif(engine=shop, spec=spec({"type": "scale", "column": "sales.price", "percent": 10}))
    assert report["baseline"] == pytest.approx(revenue())
    assert report["scenario"] == pytest.approx(revenue() * 1.1)
    assert report["delta_pct"] == pytest.approx(10.0)
    assert report["rows"] == len(ROWS)


def test_a_lever_only_changes_its_segment(shop):
    report = run_whatif(engine=shop, spec=spec(
        {"type": "scale", "column": "sales.price", "percent": 10,
         "where": {"column": "products.category", "op": "=", "values": ["toys"]}}))
    expected = revenue(price=lambda row: row[3] * (1.1 if category(row) == "Toys" else 1.0))
    assert report["scenario"] == pytest.approx(expected)
    assert any("'Toys'" in note for note in report["notes"])          # stored spelling
    rows = {row["segment"]: row for row in report["breakdown"]["rows"]}
    assert report["breakdown"]["dimension"] == "products.category"
    assert rows["Books"]["delta"] == pytest.approx(0)
    assert rows["Toys"]["share_of_change_pct"] == pytest.approx(100)


def test_elasticity_changes_volume(shop):
    report = run_whatif(engine=shop, spec=spec(
        {"type": "scale", "column": "sales.price", "percent": 10, "elasticity": -1.5}))
    assert report["scenario"] == pytest.approx(revenue() * 1.1 * (1 - 0.15))
    assert any("elasticity -1.5" in sentence for sentence in report["assumptions"])


def test_excluding_the_worst_product_by_rank(shop):
    by_product = {p: revenue([row for row in ROWS if row[1] == p]) for p, _, _ in PRODUCTS}
    worst = min(by_product, key=by_product.get)
    report = run_whatif(engine=shop, spec=spec(
        {"type": "exclude", "where": {"column": "sales.product_id", "rank": "bottom", "n": 1}}))
    assert report["scenario"] == pytest.approx(revenue() - by_product[worst])
    assert report["spec"]["levers"][0]["where"] == {"column": "sales.product_id", "rank": "bottom", "n": 1}
    assert str(worst) in report["levers"][0]["label"]


def test_accents_and_case_match_the_stored_value(shop):
    report = run_whatif(engine=shop, spec=spec(
        {"type": "exclude", "where": {"column": "sales.region", "op": "=", "values": ["sao paulo"]}}))
    assert report["scenario"] == pytest.approx(revenue([row for row in ROWS if row[2] != "São Paulo"]))


def test_unknown_value_is_an_error_with_suggestions(shop):
    with pytest.raises(WhatIfError, match="Closest stored values: 'North'"):
        run_whatif(engine=shop, spec=spec(
            {"type": "exclude", "where": {"column": "sales.region", "op": "=", "values": ["Nort"]}}))


def test_volume_on_counts_and_averages(shop):
    count = run_whatif(engine=shop, spec={**REVENUE, "metric_expression": "COUNT(*)",
                                          "levers": [{"type": "volume", "percent": 20}]})
    assert count["scenario"] == pytest.approx(len(ROWS) * 1.2)
    average = run_whatif(engine=shop, spec={**REVENUE, "metric_expression": "AVG(sales.price)", "levers": [
        {"type": "exclude", "where": {"column": "sales.product_id", "op": "=", "values": [3]}}]})
    kept = [row[3] for row in ROWS if row[1] != 3]
    assert average["scenario"] == pytest.approx(sum(kept) / len(kept))   # no integer division


def test_value_shift_on_an_average(shop):
    report = run_whatif(engine=shop, spec={**REVENUE, "metric_name": "delivery days",
                                           "metric_expression": "AVG(sales.days)",
                                           "levers": [{"type": "shift", "column": None, "amount": -2}]})
    assert report["scenario"] == pytest.approx(sum(row[6] for row in ROWS) / len(ROWS) - 2)


def test_cap_set_and_floor(shop):
    capped = run_whatif(engine=shop, spec=spec({"type": "cap", "column": "sales.price", "limit": 8}))
    assert capped["scenario"] == pytest.approx(revenue(price=lambda row: min(row[3], 8)))
    floored = run_whatif(engine=shop, spec=spec({"type": "floor", "column": "sales.price", "limit": 8}))
    assert floored["scenario"] == pytest.approx(revenue(price=lambda row: max(row[3], 8)))
    fixed = run_whatif(engine=shop, spec=spec({"type": "set", "column": "sales.price", "value": 5}))
    assert fixed["scenario"] == pytest.approx(revenue(price=lambda row: 5))


def test_stacked_levers_report_each_effect_and_the_interaction(shop):
    report = run_whatif(engine=shop, spec=spec(
        {"type": "scale", "column": "sales.price", "percent": 10},
        {"type": "exclude", "where": {"column": "sales.region", "op": "=", "values": ["North"]}}))
    expected = revenue(price=lambda row: row[3] * 1.1, weight=lambda row: 0.0 if row[2] == "North" else 1.0)
    assert report["scenario"] == pytest.approx(expected)
    effects = [effect["delta"] for effect in report["lever_effects"]]
    assert sum(effects) + report["interaction"] == pytest.approx(report["delta"])


def test_ratio_metrics_take_exclusions_but_not_volume(shop):
    report = run_whatif(engine=shop, spec={**REVENUE, "metric_expression": "COUNT(DISTINCT sales.region)", "levers": [
        {"type": "exclude", "where": {"column": "sales.region", "op": "=", "values": ["North"]}}]})
    assert report["baseline"] == 3 and report["scenario"] == 2
    with pytest.raises(WhatIfError, match="ratio or a distinct count"):
        run_whatif(engine=shop, spec={**REVENUE, "metric_expression": "COUNT(DISTINCT sales.region)",
                                      "levers": [{"type": "volume", "percent": 10}]})


# ------------------------------------------------------------ sensitivity

def test_sweep_matches_single_runs(shop):
    base = spec({"type": "scale", "column": "sales.price", "percent": 10})
    sweep = run_sweep(base, shop, 0, [-20, 0, 20])
    assert [point["delta_pct"] for point in sweep["points"]] == pytest.approx([-20, 0, 20])
    with_elasticity = run_sweep(base, shop, 0, [10], elasticity=-1.0)
    assert with_elasticity["points"][0]["scenario"] == pytest.approx(revenue() * 1.1 * 0.9)
    report = run_whatif(engine=shop, spec=base)
    assert len(report["sensitivity"]["points"]) >= 9


# ------------------------------------------------------------ forecast

def test_holt_winters_learns_trend_and_season():
    season = [10, 12, 15, 20, 26, 30, 28, 24, 18, 14, 11, 9]
    y = [100 + 2 * t + season[t % 12] for t in range(48)]
    result = fc.forecast(y, 6, 12)
    assert result["model"].startswith("Holt-Winters")
    expected = [100 + 2 * t + season[t % 12] for t in range(48, 54)]
    for point, actual in zip(result["points"], expected):
        assert point["yhat"] == pytest.approx(actual, rel=0.03)
        assert point["lower"] <= point["yhat"] <= point["upper"]
    assert result["backtest"]["mape"] < result["backtest"]["naive_mape"]


def test_short_histories_fall_back_to_simpler_models():
    assert fc.fit_series([5, 6, 7, 8, 9], 12).model.startswith("Holt (")
    assert fc.fit_series([5, 6, 7], 12).model == "Exponential smoothing"
    flat = fc.forecast([3.0, 3.0, 3.0, 3.0, 3.0], 2)
    assert all(point["lower"] >= 0 for point in flat["points"])


def test_forecast_pipeline_with_a_scenario(shop):
    report = run_forecast(engine=shop, spec=spec({"type": "scale", "column": "sales.price", "percent": 10},
                                                 time_column="sales.sold_at"), horizon=3)
    assert len(report["history"]) == MONTHS and len(report["forecast"]) == 3
    assert report["forecast"][0]["period"] == "2024-07-01"
    assert report["scenario_ratio"] == pytest.approx(1.1)
    assert report["summary"]["delta_pct"] == pytest.approx(10.0, abs=0.01)
    first = report["history"][0]
    assert first["baseline"] == pytest.approx(revenue([row for row in ROWS if row[5] == _month(0)]))
    assert any("trend and seasonality" in sentence for sentence in report["assumptions"])


def test_a_question_asking_for_a_forecast_runs_one(shop):
    report = run_whatif(engine=shop, spec=spec({"type": "scale", "column": "sales.price", "percent": 5},
                                               time_column="sales.sold_at", forecast_periods=3))
    assert report["forecast"]["horizon"] == 3


# ------------------------------------------------------------ safety

def _row_count(engine) -> int:
    with engine.connect() as connection:
        return connection.exec_driver_sql("SELECT COUNT(*) FROM sales").scalar()


@pytest.mark.parametrize("bad, message", [
    ({**REVENUE, "metric_expression": "SUM(sales.qty); DROP TABLE sales"}, "cannot reference a table"),
    ({**REVENUE, "metric_expression": "SUM((SELECT qty FROM sales))"}, "subquery"),
    ({**REVENUE, "metric_expression": "sales.qty"}, "must aggregate"),
    ({**REVENUE, "levers": [{"type": "scale", "column": "sales.region", "percent": 5}]}, "not a number"),
    ({**REVENUE, "levers": [{"type": "scale", "column": "sales.price", "percent": 5000}]}, "between"),
    ({**REVENUE, "levers": [{"type": "delete", "where": {}}]}, "Unknown lever type"),
    ({**REVENUE, "filters": "1 = 1; DELETE FROM sales"}, "cannot reference a table"),
    ({**REVENUE, "levers": [{"type": "exclude", "where": {
        "column": "sales.region", "op": "=", "values": ["x'); DELETE FROM sales; --"]}}]}, "does not occur"),
    ({**REVENUE, "levers": [{"type": "exclude", "where": {
        "column": "sales.region", "op": "; DROP", "values": ["North"]}}]}, "operator"),
])
def test_a_scenario_cannot_write_or_inject(shop, bad, message):
    before = _row_count(shop)
    with pytest.raises(WhatIfError, match=message):
        run_whatif(engine=shop, spec={"levers": [{"type": "volume", "percent": 1}], **bad})
    assert _row_count(shop) == before


def test_generated_sql_passes_the_safety_gate(shop):
    with dialects.using(shop):
        from backend.text_to_sql.schema_context import get_agent_schema

        scenario = verify_scenario(spec(
            {"type": "scale", "column": "sales.price", "percent": 10, "elasticity": -1,
             "where": {"column": "sales.region", "op": "in", "values": ["North", "South"]}},
            {"type": "exclude", "where": {"column": "products.name", "op": "=", "values": ["Atlas"]}}),
            get_agent_schema(shop), shop)
        sql = totals_sql(scenario, [scenario_expression(scenario)])
    assert check_sql_safety(sql)["safe"]


# ------------------------------------------------------------ planner and API

class Scripted:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def test_question_is_planned_then_verified(shop):
    plan = spec({"type": "scale", "column": "sales.price", "percent": 10})
    model = Scripted("Sure:\n```json\n" + json.dumps(plan) + "\n```")
    report = run_whatif("What if prices rise 10%?", shop, model=model)
    assert report["delta_pct"] == pytest.approx(10.0)
    assert "What if prices rise 10%?" in model.prompts[0] and "sales" in model.prompts[0]
    with pytest.raises(WhatIfError, match="Could not turn the question"):
        run_whatif("What if?", shop, model=Scripted("no idea"))


def test_structured_filters_and_aliases_from_small_models(shop):
    # What a 3B local model really returned: "t." aliases and a filter written like a segment.
    plan = {"metric_name": "revenue", "metric_expression": "SUM(t.price * t.qty)", "fact_table": "sales",
            "filters": {"column": "t.region", "op": "=", "values": ["north"]},
            "levers": [{"type": "scale", "column": "t.price", "percent": 10}], "forecast_periods": 3}
    report = run_whatif("What if prices rise 10%?", shop, model=Scripted(json.dumps(plan)))
    north = [row for row in ROWS if row[2] == "North"]
    assert report["baseline"] == pytest.approx(revenue(north))
    assert report["scenario"] == pytest.approx(revenue(north) * 1.1)
    assert "forecast" not in report and report["spec"]["forecast_periods"] == 0   # not asked for
    asked = run_whatif("Forecast the next 3 months if prices rise 10%", shop,
                       model=Scripted(json.dumps({**plan, "time_column": "sales.sold_at"})))
    assert asked["forecast"]["horizon"] == 3


def test_wrong_fact_table_falls_back_to_the_metrics_table(shop):
    # A real model named an unrelated table as the fact table; products cannot reach sales rows.
    report = run_whatif(engine=shop, spec={**spec({"type": "scale", "column": "sales.price", "percent": 10}),
                                           "fact_table": "products"})
    assert report["scenario"] == pytest.approx(revenue() * 1.1)
    assert any("using sales as the main table" in note for note in report["notes"])


def test_whatif_endpoints(shop, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.whatif import planner

    monkeypatch.setattr(planner, "llm", Scripted(json.dumps(spec({"type": "volume", "percent": 10}))))
    connection_id = f"whatif-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": shop})
    client = TestClient(main.app)

    asked = client.post("/whatif", json={"connection_id": connection_id, "question": "What if we sell 10% more?"})
    assert asked.status_code == 200 and asked.json()["delta_pct"] == pytest.approx(10.0)
    edited = asked.json()["spec"]
    edited["levers"][0]["percent"] = 30
    rerun = client.post("/whatif", json={"connection_id": connection_id, "spec": edited})
    assert rerun.json()["delta_pct"] == pytest.approx(30.0)

    sweep = client.post("/whatif/sweep", json={"connection_id": connection_id, "spec": edited, "values": [0, 50]})
    assert [p["delta_pct"] for p in sweep.json()["points"]] == pytest.approx([0, 50])
    forecast = client.post("/forecast", json={"connection_id": connection_id, "horizon": 2,
                                              "spec": {**edited, "time_column": "sales.sold_at"}})
    assert forecast.status_code == 200 and len(forecast.json()["forecast"]) == 2

    bad = client.post("/whatif", json={"connection_id": connection_id, "spec": {**edited, "metric_expression": "x"}})
    assert bad.status_code == 422
    assert client.post("/whatif", json={"connection_id": connection_id}).status_code == 422
    assert client.post("/whatif", json={"connection_id": "someone-else", "spec": edited}).status_code == 404
    assert client.get("/whatif/route", params={"question": "what if prices rise 10%"}).json()["whatif"]
    assert not client.get("/whatif/route", params={"question": "how many orders"}).json()["whatif"]
    assert math.isfinite(rerun.json()["baseline"])
