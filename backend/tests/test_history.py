# Question history: every question and "why" investigation is saved
# per person with its answer, can be searched, deleted and cleared,
# and is shown on the History page.

import time
import uuid

from backend import history
from history_view import activity_rows, investigation_html, status_badge, tiles_html


def test_store_saves_searches_and_deletes(tmp_path):
    store = history.HistoryStore(f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    first = store.add("local", "question", "Which state has the most orders?", "SUCCESS",
                      answer="SP has the most orders.", sql="SELECT 1", database="shop (MySQL)")
    store.add("local", "investigation", "Why did orders drop in December 2017?", "SUCCESS",
              answer="November was a one-off spike.", detail={"reasons": ["spike"]})
    store.add("someone-else", "question", "Secret question", "SUCCESS")

    data = store.list("local")
    assert data["total"] == 2 and len(data["activity"]) == 2
    assert data["items"][0]["kind"] == "investigation"  # newest first
    assert data["items"][0]["detail"] == {"reasons": ["spike"]}
    assert "owner" not in data["items"][0]
    assert store.list("local", search="SP has")["total"] == 1  # matches the answer too
    assert store.list("local", search="state orders")["total"] == 1
    assert store.list("local", kind="investigation")["items"][0]["question"].startswith("Why")
    assert store.list("local", search="secret")["total"] == 0  # never another person's

    assert store.delete("local", first) and not store.delete("local", first)
    assert store.list("local")["total"] == 1
    assert store.clear("local") == 1 and store.list("someone-else")["total"] == 1


def test_only_the_newest_entries_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "KEEP_PER_OWNER", 3)
    store = history.HistoryStore(f"sqlite:///{(tmp_path / 'h.db').as_posix()}")
    for number in range(5):
        store.add("local", "question", f"question {number}", "SUCCESS")
        time.sleep(0.01)
    assert [item["question"] for item in store.list("local")["items"]] == ["question 4", "question 3", "question 2"]


def test_history_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("INSIGHTX_HISTORY", "0")
    assert history.record("local", "question", "anything", "SUCCESS") is None


def test_investigation_summary_keeps_reason_and_actions():
    report = {
        "headline": {"metric": "orders", "baseline_label": "2017-11", "current_label": "2017-12", "change_pct": -24.8},
        "narrative": {"real_reason": "November was a spike.", "summary": "Orders fell.",
                      "actions": [{"action": "Plan for after sales", "why": "x"}]},
        "reasons": [{"title": "2017-11 was unusually high"}],
    }
    summary = history.investigation_summary(report)
    assert summary["answer"] == "November was a spike.\n\nOrders fell."
    assert summary["detail"]["reasons"] == ["2017-11 was unusually high"]
    assert summary["detail"]["actions"] == ["Plan for after sales"]
    page = investigation_html(summary["detail"])
    assert "24.8%" in page and "Plan for after sales" in page


def test_page_pieces():
    now = time.time()
    data = {"activity": [{"ts": now, "kind": "question"}, {"ts": now, "kind": "investigation"},
                         {"ts": now - 40 * 86400, "kind": "question"}]}
    assert "Questions asked" in tiles_html(data) and ">3<" in tiles_html(data)
    rows = activity_rows(data["activity"])
    assert sum(row["count"] for row in rows) == 2  # only the last 30 days
    assert "Answered" in status_badge("SUCCESS") and "Failed" in status_badge("ERROR")


def test_query_and_investigate_endpoints_save_history(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    def fake_run(question, thread_id, engine):
        return {"status": "SUCCESS", "question": question, "answer": "There are 3 orders.",
                "generated_sql": "SELECT COUNT(*) FROM orders", "execution_result": {}}

    monkeypatch.setattr(main, "run_text_to_sql", fake_run)
    monkeypatch.setattr(main, "suggest_follow_ups", lambda question, result: ["How many by month?"])
    monkeypatch.setattr(main, "run_investigation", lambda question, engine: {
        "status": "SUCCESS", "headline": {"metric": "orders", "change_pct": -10.0},
        "narrative": {"real_reason": "A one-off spike.", "summary": "Orders fell."}, "reasons": []})
    connection_id = f"hist-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id,
                        {"engine": None, "database": "shop", "source_type": "mysql"})
    client = TestClient(main.app)

    body = client.post("/query", json={"connection_id": connection_id, "question": "How many orders?"}).json()
    assert body["follow_ups"] == ["How many by month?"]
    assert client.post("/investigate", json={"connection_id": connection_id,
                                             "question": "Why did orders drop?"}).status_code == 200

    items = client.get("/history").json()["items"]
    assert [item["kind"] for item in items] == ["investigation", "question"]
    assert items[1]["answer"] == "There are 3 orders." and items[1]["sql"] == "SELECT COUNT(*) FROM orders"
    assert items[1]["database"] == "shop (MySQL)" and items[1]["thread_id"]
    assert items[0]["answer"].startswith("A one-off spike.")
    assert client.get("/history", params={"kind": "question"}).json()["total"] == 1

    assert client.delete(f"/history/{items[0]['id']}").json() == {"deleted": 1}
    assert client.delete(f"/history/{items[0]['id']}").status_code == 404
    assert client.delete("/history").json() == {"deleted": 1}
    assert client.get("/history").json()["total"] == 0


def test_why_questions_join_the_chat_conversation(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.text_to_sql.agent import add_to_thread, get_thread_state

    thread_id = f"itest-{uuid.uuid4()}"
    add_to_thread(engine, thread_id, [{"role": "user", "content": "Which state has the most orders?"},
                                      {"role": "assistant", "content": "SP."}])
    monkeypatch.setattr(main, "run_investigation", lambda question, engine: {
        "status": "SUCCESS", "headline": {"metric": "orders"},
        "narrative": {"real_reason": "November was a one-off spike.", "summary": "Orders fell."}, "reasons": []})
    connection_id = f"hist-{uuid.uuid4()}"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": engine})
    client = TestClient(main.app)

    body = client.post("/investigate", json={"connection_id": connection_id, "question": "Why did orders drop?",
                                             "thread_id": thread_id}).json()
    messages = [m["content"] for m in body["conversation_history"]]
    assert messages[:3] == ["Which state has the most orders?", "SP.", "Why did orders drop?"]
    assert messages[3].startswith("🔎 November was a one-off spike.")
    assert get_thread_state(engine, thread_id)["conversation_history"] == body["conversation_history"]
    assert client.get("/history").json()["items"][0]["thread_id"] == thread_id
    # without a chat id nothing is added anywhere
    other = client.post("/investigate", json={"connection_id": connection_id, "question": "Why?"}).json()
    assert "conversation_history" not in other
