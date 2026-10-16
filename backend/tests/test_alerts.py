# Alerts: the maths that decides "unusual" (no database), turning query
# results into a series, and the /alerts endpoints with the background
# checker on a real PostgreSQL table.

import random
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from backend.alerting import detect, metrics, service
from backend.alerting.store import AlertStore, set_store


def daily(days=120, seed=1, monday_boost=30.0, noise=5.0):
    random.seed(seed)
    start = datetime(2024, 1, 1)
    return [{"t": start + timedelta(days=i),
             "v": 100 + i * 0.5 + (monday_boost if (start + timedelta(days=i)).weekday() == 0 else 0)
             + random.gauss(0, noise)} for i in range(days)]


# ---------------- detect ----------------

def test_smart_learns_trend_and_weekday_habits():
    points = daily()
    # a normal busy Monday is not an alert
    assert detect.evaluate({"type": "smart"}, points, "Orders")["status"] == "ok"
    crash = points + [{"t": points[-1]["t"] + timedelta(days=1), "v": 20.0}]
    result = detect.evaluate({"type": "smart"}, crash, "Orders")
    assert result["fired"] and result["severity"] == "critical" and result["score"] < 0
    assert "lower than the expected" in result["message"] and "Normal range" in result["message"]
    # only rises wanted: a drop does not fire
    assert not detect.evaluate({"type": "smart", "direction": "up"}, crash, "Orders")["fired"]


def test_smart_is_quiet_on_normal_data():
    for seed in range(3):
        quiet = detect.backtest({"type": "smart", "sensitivity": "low"}, daily(200, seed), "Orders")
        assert quiet["fired_count"] <= 2
    assert detect.evaluate({"type": "smart"}, daily(5), "Orders")["status"] == "not_enough_data"


def test_limit_change_trend_and_stale():
    points = daily(60)
    last = points[-1]["v"]
    below = detect.evaluate({"type": "limit", "op": "below", "value": last + 50}, points, "Orders")
    assert below["fired"] and "below your limit" in below["message"]
    assert not detect.evaluate({"type": "limit", "op": "above", "value": last + 50}, points, "Orders")["fired"]

    jump = points + [{"t": points[-1]["t"] + timedelta(days=1), "v": last * 2}]
    change = detect.evaluate({"type": "change", "percent": 50}, jump, "Orders")
    assert change["fired"] and round(change["change_pct"]) == 100 and change["severity"] == "critical"
    week = detect.evaluate({"type": "change", "compare_to": "week", "percent": 50}, jump, "Orders")
    assert week["fired"] and "same day last week" in week["message"]

    falling = [{"t": datetime(2024, 1, 1) + timedelta(days=i), "v": v} for i, v in enumerate([5, 9, 8, 7, 6])]
    trend = detect.evaluate({"type": "trend", "periods": 3, "direction": "down"}, falling, "Sales")
    assert trend["fired"] and "fell 3 periods in a row" in trend["message"]

    stale = detect.evaluate({"type": "stale", "periods": 2}, points, "Orders", now=points[-1]["t"] + timedelta(days=5))
    assert stale["fired"] and "5 days" in stale["message"]
    assert not detect.evaluate({"type": "stale", "periods": 2}, points, "Orders",
                               now=points[-1]["t"] + timedelta(hours=20))["fired"]


def test_unfinished_period_and_gaps():
    months = [{"t": datetime(2024, m, 1), "v": 100.0} for m in range(1, 13)]
    today = datetime(2024, 12, 15)  # December is not over: its low number is ignored
    months[-1]["v"] = 10.0
    assert detect.evaluate({"type": "smart"}, months, "Sales", now=today)["period"] == "Nov 2024"
    assert detect.evaluate({"type": "smart", "skip_unfinished": False}, months, "Sales", now=today)["fired"]
    gappy = [{"t": datetime(2024, 1, 1), "v": 3.0}, {"t": datetime(2024, 1, 4), "v": 2.0}]
    filled = detect.fill_gaps(gappy, "day")
    assert [p["v"] for p in filled] == [3.0, 0.0, 0.0, 2.0]


# ---------------- metrics ----------------

def test_query_results_become_points():
    rows = [{"day": "2024-01-02", "orders": 5}, {"day": "2024-01-01", "orders": "3"}]
    points, single = metrics.to_points(["day", "orders"], rows)
    assert not single and [p["v"] for p in points] == [3.0, 5.0] and points[0]["t"] == datetime(2024, 1, 1)
    points, single = metrics.to_points(["waiting"], [{"waiting": 42}])
    assert single and points == [{"t": None, "v": 42.0}]
    with pytest.raises(metrics.MetricError):
        metrics.to_points(["state", "orders"], [{"state": "SP", "orders": 1}, {"state": "RJ", "orders": 2}])


# ---------------- endpoints + background checker ----------------

TABLE = "itest_alert_orders"


@pytest.fixture
def shop(engine):
    start = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=60)
    rows = []
    for day in range(60):
        for n in range(20 + (day % 7)):
            rows.append(f"({len(rows) + 1}, TIMESTAMP '{(start + timedelta(days=day)).isoformat(' ')}', {10 + n % 5})")
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        connection.execute(text(f"CREATE TABLE {TABLE} (order_id int PRIMARY KEY, order_date timestamp, "
                                f"payment_value numeric)"))
        connection.execute(text(f"INSERT INTO {TABLE} VALUES {', '.join(rows)}"))
    yield start
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))


@pytest.fixture
def store(tmp_path):
    alert_store = AlertStore(f"sqlite:///{(tmp_path / 'alerts.db').as_posix()}")
    set_store(alert_store)
    yield alert_store
    set_store(None)


SQL = (f"SELECT date_trunc('day', order_date) AS period, COUNT(*) AS value FROM {TABLE} "
       f"GROUP BY 1 ORDER BY 1")


def test_alert_endpoints(engine, shop, store, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.alerting import notify

    sent = []
    monkeypatch.setattr(notify, "send_webhook", lambda url, message: sent.append((url, message)))
    monkeypatch.setattr(main, "run_text_to_sql", lambda **kw: {"status": "SUCCESS", "generated_sql": SQL})
    connection_id = f"alert-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": engine})
    client = TestClient(main.app)

    # preview from words (the AI part is faked): verdict + back-test band
    preview = client.post("/alerts/preview", json={"connection_id": connection_id, "metric_text": "orders per day",
                                                   "fill_gaps": True, "rule": {"type": "smart"}}).json()
    assert preview["used_ai"] and preview["grain"] == "day" and preview["name"] == "Orders per day"
    assert preview["evaluation"]["status"] == "ok"
    assert preview["backtest"]["checked"] > 40 and preview["backtest"]["series"][-1]["expected"] is not None

    made = client.post("/alerts", json={"connection_id": connection_id, "name": "Orders per day", "sql": SQL,
                                        "fill_gaps": True, "rule": {"type": "smart"}, "check_minutes": 60,
                                        "webhook": "https://hooks.example.com/x"}).json()
    alert = made["alert"]
    assert made["first_check"]["status"] == "ok" and alert["state"] == "ok" and not sent

    # yesterday's orders crash -> the background checker raises a critical alert and sends it
    with engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {TABLE} WHERE order_date >= :day AND order_id % 10 <> 0"),
                           {"day": shop + timedelta(days=59)})
    store.update_alert(alert["id"], next_check=0)
    assert service.tick(store, open_engine=lambda a, s: engine) == 1
    events = client.get("/alerts/events").json()["events"]
    assert len(events) == 1 and events[0]["severity"] == "critical" and "unusually low" in events[0]["title"]
    assert len(sent) == 1 and "CRITICAL" in sent[0][1]
    assert client.get("/alerts/summary").json()["critical"] == 1

    # checking again does not raise (or send) the same alert twice
    client.post(f"/alerts/{alert['id']}/check")
    assert len(client.get("/alerts/events").json()["events"]) == 1 and len(sent) == 1

    chart = client.get(f"/alerts/{alert['id']}/chart").json()
    assert chart["backtest"]["series"][-1]["fired"]

    event_id = events[0]["id"]
    assert client.post(f"/alerts/events/{event_id}", json={"action": "acknowledge"}).json()["status"] == "acknowledged"
    paused = client.patch(f"/alerts/{alert['id']}", json={"status": "paused", "snooze_hours": 24}).json()
    assert paused["status"] == "paused" and paused["snooze_until"] > 0
    assert service.tick(store, open_engine=lambda a, s: engine, now=10 ** 12) == 0  # paused: not checked

    assert client.delete(f"/alerts/{alert['id']}").json() == {"deleted": 1}
    assert client.get("/alerts", params={"connection_id": connection_id}).json()["alerts"] == []
    assert client.get("/alerts/events", params={"status": "all"}).json()["events"] == []


def test_one_number_alert_learns_from_its_readings(engine, shop, store, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    connection_id = f"alert-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": engine})
    client = TestClient(main.app)
    made = client.post("/alerts", json={"connection_id": connection_id, "name": "All orders",
                                        "sql": f"SELECT COUNT(*) AS n FROM {TABLE}",
                                        "rule": {"type": "limit", "op": "above", "value": 100}}).json()
    assert made["alert"]["single"] and made["first_check"]["fired"]
    assert len(store.readings(made["alert"]["id"])) == 1
    client.post(f"/alerts/{made['alert']['id']}/check")  # still above: the same open alert, not a new one
    assert len(store.readings(made["alert"]["id"])) == 2
    assert len(client.get("/alerts/events").json()["events"]) == 1
    bad = client.post("/alerts", json={"connection_id": connection_id, "sql": f"SELECT COUNT(*) FROM {TABLE}",
                                       "rule": {"type": "stale"}})
    assert bad.status_code == 422


def test_suggested_numbers(engine, shop):
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql.schema_context import build_agent_schema, cache_schema_from_profile

    profile = get_database_schema(engine)
    cache_schema_from_profile(engine, profile)
    schema = build_agent_schema(profile)
    schema["tables"] = [t for t in schema["tables"] if t["table_name"] == TABLE]
    found = {s["name"]: s for s in metrics.suggestions(engine, schema, "day")}
    assert "Itest alert orders per day" in found and "Total payment value per day" in found
    series = metrics.read_series(found["Itest alert orders per day"]["sql"], engine, fill=True)
    assert len(series["points"]) == 60 and series["grain"] == "day"
    assert sum(p["v"] for p in series["points"]) == sum(20 + d % 7 for d in range(60))
