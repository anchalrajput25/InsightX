# Tracing: every question is saved with a timeline of its steps, AI
# calls and database queries, visible only to the person who asked.

import pytest
from fastapi.testclient import TestClient

from backend import telemetry, tracing

GOOD_SQL = (
    "SELECT customer_city, COUNT(*) AS customers FROM itest_customers "
    "GROUP BY customer_city ORDER BY customers DESC LIMIT 1"
)


def test_timeline_events_have_start_and_duration():
    with telemetry.collect() as stats:
        telemetry.record_stage("rewrite", 0.01)
        telemetry.record_db("SELECT 1", 0.002, rows=1)
        telemetry.record_llm_failure("answer", "groq/x", 0.5, "rate_limit: slow down")
    kinds = [(event["kind"], event["name"], event["status"]) for event in stats.events]
    assert kinds == [("step", "rewrite", "ok"), ("db", "query", "ok"), ("llm", "answer", "error")]
    assert all(event["start"] >= 0 and event["seconds"] >= 0 for event in stats.events)
    trace = tracing.build(stats, "anchal", "q", result={"status": "SUCCESS"})
    assert trace["db_queries"] == 1 and trace["status"] == "SUCCESS"


def test_nothing_is_recorded_outside_a_question():
    telemetry.record_stage("rewrite", 0.01)  # no collector: silently ignored
    telemetry.record_db("SELECT 1", 0.01)


@pytest.fixture
def client_with_db(engine, keyword_retrieval):
    from backend import main
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql.schema_context import cache_schema_from_profile

    cache_schema_from_profile(engine, get_database_schema(engine))
    main.POSTGRES_SESSIONS["trace-test"] = {"engine": engine, "owner": None, "source_type": "postgresql"}
    with TestClient(main.app) as client:
        yield client
    main.POSTGRES_SESSIONS.pop("trace-test", None)


def test_query_saves_a_trace_with_steps_ai_calls_and_queries(client_with_db, fake_llms):
    fake_llms(generator=[f"STATUS: SUCCESS\nSQL:\n{GOOD_SQL}\nCONFIDENCE: High"])
    headers = {"X-InsightX-User": "anchal"}
    body = client_with_db.post("/query", json={"connection_id": "trace-test", "question": "Which city has the most customers?"},
                               headers=headers).json()
    assert body["status"] == "SUCCESS"
    summary = body["trace"]
    assert summary["id"] and summary["seconds"] > 0 and summary["db_queries"] >= 1

    listed = client_with_db.get("/traces", headers=headers).json()["traces"]
    assert [item["id"] for item in listed] == [summary["id"]]
    assert listed[0]["question"] == "Which city has the most customers?"

    trace = client_with_db.get(f"/traces/{summary['id']}", headers=headers).json()
    events = trace["detail"]["events"]
    steps = [event["name"] for event in events if event["kind"] == "step"]
    for step in ("rewrite", "generate_sql", "execute", "generate_answer", "chart"):
        assert step in steps
    assert any(event["kind"] == "db" and "itest_customers" in event["sql"] for event in events)
    assert trace["detail"]["sql"] == GOOD_SQL
    starts = [event["start"] for event in events]
    assert starts == sorted(starts)


def test_traces_are_private(client_with_db, fake_llms):
    fake_llms(generator=[f"STATUS: SUCCESS\nSQL:\n{GOOD_SQL}\nCONFIDENCE: High"])
    body = client_with_db.post("/query", json={"connection_id": "trace-test", "question": "Which city has the most customers?"},
                               headers={"X-InsightX-User": "anchal"}).json()
    other = {"X-InsightX-User": "someone-else"}
    assert client_with_db.get("/traces", headers=other).json()["traces"] == []
    assert client_with_db.get(f"/traces/{body['trace']['id']}", headers=other).status_code == 404


def test_failed_step_is_marked_in_the_trace(client_with_db, fake_llms):
    fake_llms(generator=[RuntimeError("model exploded")])
    headers = {"X-InsightX-User": "anchal"}
    body = client_with_db.post("/query", json={"connection_id": "trace-test", "question": "Which city has the most customers?"},
                               headers=headers).json()
    assert body["status"] == "ERROR"
    trace = client_with_db.get(f"/traces/{body['trace']['id']}", headers=headers).json()
    failed = [event for event in trace["detail"]["events"] if event.get("status") == "error"]
    assert failed and failed[0]["name"] == "generate_sql" and "model exploded" in failed[0]["error"]
    assert trace["status"] == "ERROR"


def test_tracing_can_be_switched_off(client_with_db, fake_llms, monkeypatch):
    monkeypatch.setenv("INSIGHTX_TRACING", "0")
    fake_llms(generator=[f"STATUS: SUCCESS\nSQL:\n{GOOD_SQL}\nCONFIDENCE: High"])
    body = client_with_db.post("/query", json={"connection_id": "trace-test", "question": "Which city has the most customers?"}).json()
    assert body["status"] == "SUCCESS" and body["trace"] == {}
