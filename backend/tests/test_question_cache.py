# Question cache: same / similar questions reuse a trusted SQL (and,
# when the data has not changed, the answer) without asking the AI.

import uuid

import pytest
from sqlalchemy import text

from backend.ai_gateway.context import ai_request
from backend.text_to_sql.question_cache import get_cache, similar_text

GOOD_SQL = (
    "SELECT customer_city, COUNT(*) AS customers FROM itest_customers "
    "GROUP BY customer_city ORDER BY customers DESC LIMIT 1"
)


def generator_output(sql: str) -> str:
    return f"STATUS: SUCCESS\nSQL:\n{sql}\nCONFIDENCE: High"


@pytest.mark.parametrize("first, second", [
    ("Which city has the most customers?", "which city has the MOST customers"),
    ("Which city has the most customers?", "What city has the highest number of customers?"),
    ("Show me the total revenue please", "What is the total revenue?"),
    ("How many orders were delivered?", "number of orders delivered"),
    ("Which film category earns the most rental payments?", "which film categories earn the highest rental payment"),
])
def test_same_meaning_matches(first, second):
    assert similar_text(first) == similar_text(second)


@pytest.mark.parametrize("first, second", [
    ("Top 5 customers by revenue", "Top 10 customers by revenue"),
    ("Total revenue in 2017", "Total revenue in 2018"),
    ("Which city has the most customers?", "Which state has the most customers?"),
    ("How many orders were delivered?", "How many orders were canceled?"),
    ("Orders from Delhi to Mumbai", "Orders from Mumbai to Delhi"),
    ("Which city has the most customers?", "Which city has the least customers?"),
    ("Customers with orders", "Customers without orders"),
])
def test_different_meaning_never_matches(first, second):
    assert similar_text(first) != similar_text(second)


@pytest.fixture
def agent_engine(engine, keyword_retrieval):
    from backend.schema_profiler import get_database_schema
    from backend.text_to_sql.schema_context import cache_schema_from_profile

    cache_schema_from_profile(engine, get_database_schema(engine))
    return engine


def ask(engine, question, user="anchal", thread_id=None):
    from backend.text_to_sql.agent import run_text_to_sql

    with ai_request(user_id=user):
        return run_text_to_sql(question=question, engine=engine, thread_id=thread_id or str(uuid.uuid4()))


def test_repeat_question_uses_no_ai(agent_engine, fake_llms):
    llms = fake_llms(generator=[generator_output(GOOD_SQL)])
    first = ask(agent_engine, "Which city has the most customers?")
    assert first["status"] == "SUCCESS" and not first["cache"]
    assert first["trust"]["score"] >= 80

    calls_before = {name: fake.calls for name, fake in llms.items()}
    second = ask(agent_engine, "What city has the highest number of customers?")
    assert second["status"] == "SUCCESS"
    assert second["cache"]["hit"] and second["cache"]["match"] == "similar" and second["cache"]["answer_reused"]
    assert second["answer"] == first["answer"]
    assert second["generated_sql"] == GOOD_SQL
    assert {name: fake.calls for name, fake in llms.items()} == calls_before  # no AI call at all
    assert any(reason["signal"] == "cache" for reason in second["trust"]["reasons"])


def test_changed_data_rewrites_only_the_answer(agent_engine, fake_llms):
    llms = fake_llms(generator=[generator_output(GOOD_SQL)])
    ask(agent_engine, "Which city has the most customers?")
    with agent_engine.begin() as connection:
        connection.execute(text("INSERT INTO itest_customers VALUES ('extra-1', 'sao paulo')"))
    try:
        answers_before, generator_before = llms["answer"].calls, llms["generator"].calls
        again = ask(agent_engine, "Which city has the most customers?")
        assert again["cache"]["hit"] and not again["cache"]["answer_reused"]
        assert llms["generator"].calls == generator_before  # SQL reused
        assert llms["answer"].calls == answers_before + 1  # answer written again for the new data
        assert again["execution_result"]["rows"][0]["customers"] == 901
    finally:
        with agent_engine.begin() as connection:
            connection.execute(text("DELETE FROM itest_customers WHERE customer_id = 'extra-1'"))


def test_cache_is_private_per_person(agent_engine, fake_llms):
    fake_llms(generator=[generator_output(GOOD_SQL), generator_output(GOOD_SQL)])
    ask(agent_engine, "Which city has the most customers?", user="anchal")
    other = ask(agent_engine, "Which city has the most customers?", user="someone-else")
    assert not other["cache"]


def test_different_question_is_not_served_from_cache(agent_engine, fake_llms):
    state_sql = GOOD_SQL.replace("ORDER BY customers DESC", "ORDER BY customers ASC")
    fake_llms(generator=[generator_output(GOOD_SQL), generator_output(state_sql)])
    ask(agent_engine, "Which city has the most customers?")
    least = ask(agent_engine, "Which city has the least customers?")
    assert not least["cache"] and least["generated_sql"] == state_sql


def test_follow_up_is_matched_by_its_standalone_form(agent_engine, fake_llms):
    def rewriter(prompt):
        if "most of them" in str(prompt):
            return "REWRITE: Which city has the most customers?\nCANONICAL: true"
        return "REWRITE: same\nCANONICAL: true"

    count_sql = "SELECT COUNT(*) AS n FROM itest_customers"
    fake_llms(generator=[generator_output(GOOD_SQL), generator_output(count_sql)], rewriter=rewriter)
    assert ask(agent_engine, "Which city has the most customers?")["status"] == "SUCCESS"

    # In another chat, a follow-up that rewrites to the same standalone
    # question is answered from the cache.
    thread = str(uuid.uuid4())
    assert ask(agent_engine, "How many customers are there?", thread_id=thread)["generated_sql"] == count_sql
    follow_up = ask(agent_engine, "and which city has most of them?", thread_id=thread)
    assert follow_up["cache"]["hit"] and follow_up["generated_sql"] == GOOD_SQL


def test_low_trust_answers_are_not_stored(agent_engine, fake_llms, monkeypatch):
    from backend.text_to_sql import question_cache

    monkeypatch.setattr(question_cache, "MIN_TRUST_SCORE", 101)
    fake_llms(generator=[generator_output(GOOD_SQL), generator_output(GOOD_SQL)])
    ask(agent_engine, "Which city has the most customers?")
    assert not ask(agent_engine, "Which city has the most customers?")["cache"]
    assert get_cache().entries("anchal") == []


def test_cache_can_be_switched_off(agent_engine, fake_llms, monkeypatch):
    monkeypatch.setenv("INSIGHTX_QUESTION_CACHE", "0")
    fake_llms(generator=[generator_output(GOOD_SQL), generator_output(GOOD_SQL)])
    ask(agent_engine, "Which city has the most customers?")
    assert not ask(agent_engine, "Which city has the most customers?")["cache"]


def test_fresh_answer_skips_the_cache_but_refreshes_it(agent_engine, fake_llms):
    from backend.text_to_sql.question_cache import fresh_answer

    llms = fake_llms(generator=[generator_output(GOOD_SQL), generator_output(GOOD_SQL)])
    ask(agent_engine, "Which city has the most customers?")
    with fresh_answer():
        again = ask(agent_engine, "Which city has the most customers?")
    assert not again["cache"] and llms["generator"].calls == 2
    assert ask(agent_engine, "Which city has the most customers?")["cache"]["answer_reused"]


def test_query_endpoint_reports_the_cache(agent_engine, fake_llms):
    from fastapi.testclient import TestClient

    from backend import main

    fake_llms(generator=[generator_output(GOOD_SQL), generator_output(GOOD_SQL)])
    connection_id = "cache-test"
    main.POSTGRES_SESSIONS[connection_id] = {"engine": agent_engine, "owner": None, "source_type": "postgresql"}
    try:
        with TestClient(main.app) as client:
            body = {"connection_id": connection_id, "question": "Which city has the most customers?"}
            first = client.post("/query", json=body).json()
            second = client.post("/query", json=body).json()
            fresh = client.post("/query", json={**body, "use_cache": False}).json()
        assert first["cache"] == {} and fresh["cache"] == {}
        assert second["cache"]["hit"] and second["cache"]["answer_reused"]
        assert "answer" not in second["cache"]  # only what the UI needs is returned
    finally:
        main.POSTGRES_SESSIONS.pop(connection_id, None)
