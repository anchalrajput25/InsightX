# LLMOps: hallucination checks, difficulty, the capture middleware on
# /query, monitor numbers, smart routing, evaluations (gate, compare,
# feedback golden set) and the /llmops endpoints. No database server
# and no AI key needed: SQLite files and scripted fakes only.

import json
import time
import uuid

import pytest
from sqlalchemy import create_engine, text

from backend.llmops import difficulty, evals, hallucination, monitor, routing
from backend.llmops.store import LLMOpsStore, set_store


@pytest.fixture
def ops_store(tmp_path):
    store = LLMOpsStore(f"sqlite:///{(tmp_path / 'llmops.db').as_posix()}")
    set_store(store)
    routing._mode_cache.update(at=0.0, value="off")
    yield store
    set_store(None)
    routing._mode_cache.update(at=0.0, value="off")


@pytest.fixture
def shop(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'shop.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sellers (seller TEXT, sales REAL)"))
        connection.execute(text("INSERT INTO sellers VALUES ('alpha', 900), ('beta', 700), ('gamma', 400)"))
    return engine


def response(answer, rows=None, sql="SELECT seller, sales FROM sellers ORDER BY sales DESC", **extra):
    rows = [{"seller": "alpha", "sales": 900.0}, {"seller": "beta", "sales": 700.0},
            {"seller": "gamma", "sales": 400.0}] if rows is None else rows
    return {"status": "SUCCESS", "question": "Which seller has the highest sales?", "answer": answer,
            "sql": sql, "execution_result": {"success": True, "columns": ["seller", "sales"], "rows": rows,
                                             "row_count": len(rows)}, **extra}


# ---------------- hallucination ----------------

def test_grounded_answer_has_no_findings():
    checks = hallucination.check_answer(response("The highest sales are from **alpha** with 900."))
    assert checks["applicable"] and checks["risk"] == "none" and checks["score"] == 1.0
    assert not checks["hallucinated"]


def test_invented_number_and_value_are_hallucinations():
    checks = hallucination.check_answer(response("**delta** leads with 1,250 in sales."))
    kinds = {f["kind"] for f in checks["findings"]}
    assert {"unverified_number", "unverified_value"} <= kinds
    assert checks["hallucinated"] and checks["risk"] in ("medium", "high") and checks["score"] < 0.8


def test_empty_result_top_n_and_superlative():
    empty = hallucination.check_answer(response("Sales were 500 last month.", rows=[]))
    assert any(f["kind"] == "empty_result_claim" for f in empty["findings"])
    honest = hallucination.check_answer(response("There are no matching rows.", rows=[]))
    assert not any(f["kind"] == "empty_result_claim" for f in honest["findings"])

    top = hallucination.check_answer(response("Here are the top 10 sellers: alpha, beta and gamma."))
    assert any(f["kind"] == "top_n_mismatch" for f in top["findings"])

    wrong = hallucination.check_answer(response("The highest seller is beta, with 700."))
    assert any(f["kind"] == "superlative_mismatch" for f in wrong["findings"]) and wrong["hallucinated"]


def test_not_applicable_and_signals_from_trust_layer():
    assert not hallucination.check_answer({"status": "ERROR", "answer": "x"})["applicable"]
    checks = hallucination.check_answer(response("alpha has 900.", trust={"level": "low"},
                                                 sanity={"fanout": [{"message": "orders repeated per item"}]}))
    assert {"low_trust", "fanout"} <= {f["kind"] for f in checks["findings"]}


def test_judge_reply_is_parsed_and_merged():
    verdict = hallucination.parse_judge('ok {"faithful": false, "score": 0.4, '
                                        '"unsupported_claims": ["growth of 20%"], "explanation": "no trend data"}')
    assert verdict == {"faithful": False, "score": 0.4, "unsupported_claims": ["growth of 20%"],
                       "explanation": "no trend data"}
    with pytest.raises(ValueError):
        hallucination.parse_judge("not json")
    base = hallucination.check_answer(response("alpha has 900."))
    merged = hallucination.merge_judge(base, verdict)
    assert merged["hallucinated"] and any(f["kind"] == "judge_unsupported" for f in merged["findings"])
    assert merged["score"] < base["score"]
    assert "growth" not in hallucination.judge_prompt({"question": "q", "answer": "a", "preview": {}})


# ---------------- difficulty ----------------

def test_difficulty_levels():
    assert difficulty.level("How many customers are there?") == "easy"
    assert difficulty.level("Show monthly revenue in 2017 and the number of orders each month.") == "medium"
    assert difficulty.level("Compare average delivery time per state in 2017 vs 2018, rank states by growth "
                            "and exclude cancelled orders") == "hard"


# ---------------- capture middleware ----------------

def wait_for(check, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    return check()


def test_query_is_captured_without_changing_the_response(ops_store, shop, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    result = {"status": "SUCCESS", "question": "Which seller has the highest sales?",
              "answer": "**delta** leads with 1,250.", "generated_sql": "SELECT seller, sales FROM sellers",
              "execution_result": {"success": True, "columns": ["seller", "sales"],
                                   "rows": [{"seller": "alpha", "sales": 900}], "row_count": 1},
              "trust": {"level": "medium", "score": 62}}
    from backend.llmops import capture

    seen = []
    monkeypatch.setattr(main, "run_text_to_sql",
                        lambda **kw: seen.append(capture.current_difficulty.get()) or dict(result))
    monkeypatch.setattr(main, "suggest_follow_ups", lambda q, r: [])
    connection_id = f"ops-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": shop})
    client = TestClient(main.app)

    reply = client.post("/query", json={"connection_id": connection_id, "question": result["question"]},
                        headers={"X-InsightX-User": "ana"})
    assert reply.status_code == 200 and reply.json()["answer"] == result["answer"]
    assert seen == ["easy"]  # the pipeline (in a worker thread) sees the difficulty for routing

    rows = wait_for(lambda: ops_store.list_answers("ana"))
    assert len(rows) == 1
    row = rows[0]
    assert row["hallucinated"] == 1 and row["risk"] in ("medium", "high") and row["trust_score"] == 62
    assert row["difficulty"] == "easy" and row["preview"]["rows"][0]["seller"] == "alpha"
    assert ops_store.list_answers("someone-else") == []

    # a failed request is not captured; nothing breaks
    bad = client.post("/query", json={"connection_id": "missing", "question": "x"}, headers={"X-InsightX-User": "ana"})
    assert bad.status_code >= 400
    time.sleep(0.2)
    assert len(ops_store.list_answers("ana")) == 1


# ---------------- monitor ----------------

def test_monitor_kpis_slo_and_incidents():
    now = time.time()
    traces = [{"id": str(i), "ts": now - 60 * i, "kind": "question", "status": "SUCCESS" if i % 5 else "ERROR",
               "seconds": 2.0 + (i % 3), "question": f"q{i}", "error": ""} for i in range(20)]
    calls = [{"ts": now - 30 * i, "status": "error" if i == 0 else ("cache" if i % 4 == 0 else "ok"),
              "route": "groq/a", "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.0,
              "latency_ms": 800, "error_kind": "rate_limit"} for i in range(12)]
    answers = [{"ts": now, "faithfulness": 0.5, "hallucinated": 1, "rating": -1, "risk": "high"},
               {"ts": now, "faithfulness": 1.0, "hallucinated": 0, "rating": 1, "risk": "none"}]
    k = monitor.kpis(traces, calls, answers)
    assert k["questions"] == 20 and k["success_rate"] == 0.8 and k["hallucination_rate"] == 0.5
    assert k["ai_error_rate"] == 0.1 and k["tokens"] == 180 and k["thumbs_up_rate"] == 0.5
    slo = monitor.slo_status(k, monitor.DEFAULT_SLO, traces)
    assert slo[0]["met"] is False and slo[0]["failures"] == 4 and slo[0]["budget_used"] == 4.0
    points = [{"t": i, "p95_seconds": 2.0, "errors": 0, "ai_errors": 0, "flagged": 0} for i in range(10)]
    points[7]["p95_seconds"] = 40.0
    found = monitor.incidents(points)
    assert len(found) == 1 and found[0]["metric"] == "p95_seconds" and found[0]["t"] == 7
    assert monitor.percentile([1, 2, 3, 4], 0.5) == 2.5


def test_monitor_endpoint_reads_the_stores(ops_store):
    from fastapi.testclient import TestClient

    from backend import main, tracing
    from backend.ai_gateway.usage_store import get_store as usage

    tracing.get_store().save({"user_id": "ana", "question": "q", "status": "SUCCESS", "seconds": 3.0,
                              "detail": {"events": [{"kind": "step", "name": "generate_sql", "seconds": 1.5}]}})
    usage().record(user_id="ana", request_id="r1", job="answer", tier="light", route="groq/x", provider="groq",
                   status="ok", latency_ms=500)
    client = TestClient(main.app)
    data = client.get("/llmops/monitor", params={"window": "24h"}, headers={"X-InsightX-User": "ana"}).json()
    assert data["kpis"]["questions"] == 1 and data["kpis"]["ai_calls"] == 1
    assert data["stages"][0]["stage"] == "generate_sql"
    assert any(r["route"] == "groq/x" for r in data["routes"])
    assert client.get("/llmops/monitor", params={"window": "2y"}).status_code == 400
    assert client.put("/llmops/slo", json={"p95_seconds": 12}).json()["p95_seconds"] == 12


# ---------------- smart routing ----------------

class FakeHealth:
    def why_unavailable(self, route):
        return "daily free limit reached" if route.model == "blocked" else None

    def snapshot(self, route):
        return {"requests_today": 0}


def test_smart_routing_modes(ops_store, monkeypatch):
    from backend.ai_gateway import gateway as gateway_module
    from backend.ai_gateway.gateway import Gateway, get_gateway, set_gateway
    from backend.ai_gateway.routes import Route

    slow, fast, blocked = Route("groq", "slow"), Route("groq", "fast"), Route("groq", "blocked")
    original = Gateway(routes={"light": [blocked, slow, fast]}, health=FakeHealth())
    monkeypatch.setattr(Gateway, "key_configured", staticmethod(lambda route: True))
    set_gateway(original)
    monkeypatch.setattr(routing, "route_stats", lambda fresh=False: {
        "groq/slow": {"calls": 50, "success_rate": 0.7, "p50_seconds": 6.0},
        "groq/fast": {"calls": 50, "success_rate": 0.99, "p50_seconds": 1.0},
    })
    monkeypatch.setattr(routing, "_accuracy", lambda: {})
    try:
        routing.sync()
        assert get_gateway() is original  # off: untouched

        routing.set_mode("shadow")
        smart = get_gateway()
        assert isinstance(smart, routing.SmartGateway) and smart.health is original.health
        assert [r.model for r in smart.chain("light")] == ["blocked", "slow", "fast"]  # shadow: same order
        decision = routing.decisions()[0]
        assert decision["chosen"][:2] == ["groq/fast", "groq/slow"] and decision["changed"]

        routing.set_mode("on")
        assert [r.model for r in get_gateway().chain("light")] == ["fast", "slow", "blocked"]
        assert routing.summary()["installed"]

        routing.set_mode("off")
        assert gateway_module._gateway is original
    finally:
        set_gateway(None)


# ---------------- evaluations ----------------

def report(accuracy, cases):
    return {"golden_set": "shop", "run_at": "2026-10-10T10:00:00", "case_count": len(cases),
            "summary": {"execution_accuracy": accuracy, "answer_faithfulness": 0.9, "safety_pass_rate": 1.0,
                        "latency_p95_s": 10.0},
            "cases": cases}


def case(case_id, passed, route="groq/fast"):
    return {"id": case_id, "question": case_id, "passed": passed, "difficulty": "easy", "expect": "answer",
            "models": {route: 2}}


def test_reports_gate_compare_and_route_accuracy(ops_store, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(evals, "SHARED_REPORTS", shared)
    monkeypatch.setattr(evals, "OWN_ROOT", tmp_path / "own")
    evals._accuracy_cache.update(at=0.0, value={})
    (shared / "a.json").write_text(json.dumps(report(0.5, [case("c1", True), case("c2", False, "gemini/x")])))
    (shared / "b.json").write_text(json.dumps(report(0.9, [case("c1", False), case("c2", True, "gemini/x")])))

    listed = evals.list_reports("ana")
    assert {r["id"] for r in listed} == {"shared:a.json", "shared:b.json"}
    gates = {r["id"]: r["gate"]["passed"] for r in listed}
    assert gates == {"shared:a.json": False, "shared:b.json": True}

    diff = evals.compare("ana", "shared:a.json", "shared:b.json")
    accuracy = next(m for m in diff["metrics"] if m["key"] == "execution_accuracy")
    assert accuracy["delta"] == 0.4 and accuracy["improved"]
    assert [r["id"] for r in diff["regressions"]] == ["c1"] and [r["id"] for r in diff["fixes"]] == ["c2"]

    assert evals.route_accuracy() == {"groq/fast": {"cases": 2, "accuracy": 0.5},
                                      "gemini/x": {"cases": 2, "accuracy": 0.5}}
    with pytest.raises(FileNotFoundError):
        evals.read_report("ana", "shared:../../secret.json")
    assert evals.set_gate({"execution_accuracy": 0.95})["execution_accuracy"] == 0.95
    with pytest.raises(ValueError):
        evals.set_gate({"execution_accuracy": 3})


def test_feedback_golden_set_and_progress_log(tmp_path, monkeypatch):
    monkeypatch.setattr(evals, "OWN_ROOT", tmp_path / "own")
    first = evals.add_feedback_case("ana", "localhost/shop", "Top seller?", "SELECT 1", "fixed", "easy")
    again = evals.add_feedback_case("ana", "localhost/shop", "top seller?", "SELECT 2")
    assert first["golden_id"] == again["golden_id"] and again["updated"]
    golden = evals.read_golden("ana", first["golden_id"])
    assert len(golden["cases"]) == 1 and golden["cases"][0]["expected_sql"] == "SELECT 2"
    assert [g["id"] for g in evals.list_golden("ana") if g["scope"] == "mine"] == [first["golden_id"]]
    assert evals.list_golden("bob") and not [g for g in evals.list_golden("bob") if g["scope"] == "mine"]

    log = tmp_path / "run.log"
    log.write_text("shop: 3 cases x 2 repeat(s) x 1 variant(s) on x, 1 worker(s).\n"
                   "[baseline #1] PASS c1 (easy, answer) 1.2s\n[baseline #1] FAIL c2 (easy, answer) 3s -> wrong\n"
                   "Data:   /x/logs/llmops/evals/abc/shop-20261010-101010.json  (open the Benchmark page)\n")
    progress = evals._parse_log(log)
    assert progress["total"] == 6 and progress["done"] == 2 and progress["passed"] == 1
    assert progress["report_file"] == "shop-20261010-101010.json"


def test_eval_run_needs_a_server_database(tmp_path, monkeypatch):
    monkeypatch.setattr(evals, "OWN_ROOT", tmp_path / "own")
    with pytest.raises(ValueError):
        evals.start_run("ana", "sqlite:///x.db", "x", "builtin:olist.yaml")


# ---------------- endpoints ----------------

def test_feedback_judge_and_promote_endpoints(ops_store, shop, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.llmops import capture

    monkeypatch.setattr(evals, "OWN_ROOT", tmp_path / "own")
    row = capture.build_row("ana", {"question": "Which seller has the highest sales?"},
                            response("The highest seller is beta, with 700."), "easy", 1.0)
    answer_id = ops_store.add_answer(row)
    connection_id = f"ops-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": shop})
    client = TestClient(main.app)
    headers = {"X-InsightX-User": "ana"}

    listed = client.get("/llmops/answers", params={"only": "review"}, headers=headers).json()["answers"]
    assert [a["id"] for a in listed] == [answer_id] and "preview" not in listed[0]
    assert client.get(f"/llmops/answers/{answer_id}", headers={"X-InsightX-User": "bob"}).status_code == 404

    rated = client.post(f"/llmops/answers/{answer_id}/feedback", json={"rating": -1, "note": "wrong seller"},
                        headers=headers).json()
    assert rated["rating"] == -1 and rated["note"] == "wrong seller"

    monkeypatch.setattr(hallucination, "run_judge", lambda r: {
        "faithful": False, "score": 0.2, "unsupported_claims": ["beta is the highest"], "explanation": "alpha is",
        "at": time.time(), "seconds": 0.1})
    judged = client.post(f"/llmops/answers/{answer_id}/judge", headers=headers).json()
    assert judged["judge"]["faithful"] is False and judged["risk"] == "high"

    tested = client.post(f"/llmops/answers/{answer_id}/test-sql", headers=headers, json={
        "connection_id": connection_id, "sql": "SELECT seller, sales FROM sellers ORDER BY sales DESC LIMIT 1"}).json()
    assert tested["rows"][0]["seller"] == "alpha"
    unsafe = client.post(f"/llmops/answers/{answer_id}/test-sql", headers=headers,
                         json={"connection_id": connection_id, "sql": "DELETE FROM sellers"})
    assert unsafe.status_code == 400

    promoted = client.post(f"/llmops/answers/{answer_id}/promote", headers=headers, json={
        "connection_id": connection_id, "sql": "SELECT seller, sales FROM sellers ORDER BY sales DESC LIMIT 1",
        "note": "use the first row"}).json()
    assert promoted["answer"]["review"] == "promoted" and promoted["golden_id"].startswith("mine:")
    golden = client.get("/llmops/evals/golden/cases", params={"id": promoted["golden_id"]}, headers=headers).json()
    assert golden["cases"][0]["question"] == "Which seller has the highest sales?"

    hall = client.get("/llmops/hallucination", headers=headers).json()
    assert hall["checked"] == 1 and hall["hallucinated"] == 1 and hall["judged"] == 1
    quality = client.get("/llmops/quality", headers=headers).json()
    assert quality["down"] == 1 and quality["promoted"] == 1 and quality["suggestions"]

    assert client.put("/llmops/routing", json={"mode": "sideways"}, headers=headers).status_code == 422
    overview = client.get("/llmops/routing", headers=headers).json()
    assert overview["mode"] == "off" and set(overview["preview"]) == {"easy", "medium", "hard"}
    assert client.get("/llmops/routing/classify", params={"question": "How many orders?"},
                      headers=headers).json()["level"] == "easy"
