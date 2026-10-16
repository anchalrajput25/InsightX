# Dashboard: a basic picture of the whole database built from the
# schema (no AI), and charts asked for in words, kept and re-run on the
# current data.

import uuid

import pytest
from sqlalchemy import text

from backend import dashboard

TABLES = "itest_dash_orders, itest_dash_customers"
SETUP = [
    f"DROP TABLE IF EXISTS {TABLES}",
    "CREATE TABLE itest_dash_customers (customer_id int PRIMARY KEY, customer_state text)",
    "INSERT INTO itest_dash_customers SELECT g, (ARRAY['SP','RJ','MG'])[1 + g % 3] FROM generate_series(1, 30) g",
    """CREATE TABLE itest_dash_orders (order_id int PRIMARY KEY,
           customer_id int REFERENCES itest_dash_customers (customer_id),
           order_date timestamp, payment_value numeric)""",
    """INSERT INTO itest_dash_orders
       SELECT g, 1 + g % 30, TIMESTAMP '2024-01-05' + ((g % 4) || ' month')::interval, 10 + g % 7
       FROM generate_series(1, 200) g""",
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
    schema = build_agent_schema(profile)
    schema["tables"] = [t for t in schema["tables"] if t["table_name"].startswith("itest_dash_")]
    yield schema
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {TABLES}"))


@pytest.fixture
def store(tmp_path):
    dashboard.set_store(dashboard.ChartStore(f"sqlite:///{(tmp_path / 'dash.db').as_posix()}"))
    yield
    dashboard.set_store(None)


def test_overview_charts_the_whole_database(engine, shop):
    data = dashboard.build_overview(engine, shop)
    assert data["tables"] == 2 and data["rows"] == 230
    titles = {chart["title"]: chart for chart in data["charts"]}
    assert "Itest dash orders per month" in titles
    per_month = titles["Itest dash orders per month"]["rows"]
    assert len(per_month) == 4 and sum(row["records"] for row in per_month) == 200
    # a category from the linked table, joined without double counting
    by_state = titles["Itest dash orders by customer state"]["rows"]
    assert sum(row["itest_dash_orders_count"] for row in by_state) == 200
    total = titles["Total payment value by customer state"]["rows"]
    assert float(sum(row["total_payment_value"] for row in total)) == sum(10 + g % 7 for g in range(1, 201))


def test_chart_type_words():
    assert dashboard.requested_kind("a pie chart of orders by payment type") == "pie"
    assert dashboard.requested_kind("Line graph of revenue by month") == "line"
    assert dashboard.requested_kind("orders by state") == ""


def test_dashboard_endpoints(engine, shop, store, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    def fake_run(question, thread_id, engine):
        assert thread_id.startswith("dashboard-")
        sql = ("SELECT c.customer_state, COUNT(*) AS orders FROM itest_dash_orders o "
               "JOIN itest_dash_customers c ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 2 DESC")
        return {"status": "SUCCESS", "generated_sql": sql, "answer": "SP leads.",
                "execution_result": {"columns": ["customer_state", "orders"], "rows": [{"customer_state": "SP", "orders": 1}]}}

    monkeypatch.setattr(main, "run_text_to_sql", fake_run)
    connection_id = f"dash-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": engine})
    client = TestClient(main.app)

    made = client.post("/dashboard/charts", json={"connection_id": connection_id,
                                                  "request": "pie chart of orders by state"}).json()
    assert made["kind"] == "pie" and made["id"]
    charts = client.get("/dashboard/charts", params={"connection_id": connection_id}).json()["charts"]
    assert len(charts) == 1 and charts[0]["request"] == "pie chart of orders by state"
    assert sum(row["orders"] for row in charts[0]["rows"]) == 200  # re-run on the live data

    with engine.begin() as connection:  # new data shows up without asking again
        connection.execute(text("INSERT INTO itest_dash_orders VALUES (999, 1, TIMESTAMP '2024-02-01', 5)"))
    again = client.get("/dashboard/charts", params={"connection_id": connection_id}).json()["charts"]
    assert sum(row["orders"] for row in again[0]["rows"]) == 201

    assert client.delete(f"/dashboard/charts/{made['id']}").json() == {"deleted": 1}
    assert client.get("/dashboard/charts", params={"connection_id": connection_id}).json()["charts"] == []

    monkeypatch.setattr(main, "run_text_to_sql", lambda **kwargs: {"status": "ERROR", "error": "no such data"})
    failed = client.post("/dashboard/charts", json={"connection_id": connection_id, "request": "graph of nothing"})
    assert failed.status_code == 422 and "no such data" in failed.json()["detail"]


def test_page_picks_the_asked_chart_type():
    from dashboard_view import chart_plan, tiles_html

    rows = [{"payment_type": "card", "orders": 70}, {"payment_type": "cash", "orders": 30}]
    _, plan = chart_plan(rows, ["payment_type", "orders"], "orders by payment type", "pie")
    assert plan.kind == "pie"
    _, plan = chart_plan(rows, ["payment_type", "orders"], "orders by payment type")
    assert plan.kind == "bar"
    assert "Tables" in tiles_html({"tables": 2, "rows": 230})
