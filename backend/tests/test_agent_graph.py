# End-to-end LangGraph behaviour with scripted LLMs and a real PostgreSQL:
# routing, bounded correction, graceful failures and the /query contract.

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.schema_profiler import get_database_schema
from backend.text_to_sql.agent import MAX_CORRECTION_ATTEMPTS, run_text_to_sql
from backend.text_to_sql.schema_context import cache_schema_from_profile

GOOD_SQL = (
    "SELECT customer_city, COUNT(*) AS customers FROM itest_customers "
    "GROUP BY customer_city ORDER BY customers DESC LIMIT 1"
)


def generator_output(sql: str) -> str:
    return f"STATUS: SUCCESS\nSQL:\n{sql}\nCONFIDENCE: High"


@pytest.fixture(autouse=True)
def all_candidates(monkeypatch):
    """These tests exercise the multi-candidate logic, so simple questions keep every candidate."""
    import dataclasses

    from backend.text_to_sql import agent

    monkeypatch.setattr(agent, "query_settings", dataclasses.replace(agent.query_settings, adaptive_candidates=0))


@pytest.fixture
def agent_engine(engine, keyword_retrieval):
    cache_schema_from_profile(engine, get_database_schema(engine))
    return engine


def ask(engine, question: str, thread_id: str | None = None):
    return run_text_to_sql(question=question, engine=engine, thread_id=thread_id or str(uuid.uuid4()))


def test_success_path(agent_engine, fake_llms):
    fake_llms(generator=[generator_output(GOOD_SQL)])
    result = ask(agent_engine, "Which city has the most customers?")
    assert result["status"] == "SUCCESS"
    assert result["execution_result"]["rows"][0]["customer_city"] == "sao paulo"
    assert result["correction_attempts"] == 0
    assert result["answer"] == "Grounded answer."


def test_invalid_sql_is_corrected_and_propagated(agent_engine, fake_llms):
    llms = fake_llms(
        generator=[generator_output("SELECT city, COUNT(*) FROM itest_customers GROUP BY 1")],
        corrector=[GOOD_SQL],
    )
    result = ask(agent_engine, "Which city has the most customers?")
    assert result["status"] == "SUCCESS"
    assert result["correction_attempts"] == 1
    assert result["generated_sql"] == GOOD_SQL
    assert result["corrected_sql"] == GOOD_SQL
    assert llms["corrector"].calls == 1


def test_unsafe_sql_goes_to_correction_before_touching_database(agent_engine, fake_llms):
    fake_llms(
        generator=[generator_output("SELECT pg_read_file('/etc/hostname')")],
        corrector=[GOOD_SQL],
    )
    result = ask(agent_engine, "Which city has the most customers?")
    assert result["status"] == "SUCCESS"
    assert result["correction_attempts"] == 1


def test_correction_is_bounded(agent_engine, fake_llms):
    bad = "SELECT nope FROM itest_customers"
    llms = fake_llms(
        generator=[generator_output(bad)],
        corrector=[bad] * (MAX_CORRECTION_ATTEMPTS + 3),
    )
    result = ask(agent_engine, "Show nope")
    assert result["status"] == "ERROR"
    assert result["correction_attempts"] == MAX_CORRECTION_ATTEMPTS
    assert llms["corrector"].calls == MAX_CORRECTION_ATTEMPTS
    assert "nope" in result["error"]
    assert result["conversation_history"][-1]["role"] == "assistant"


def test_clarification_does_not_produce_sql(agent_engine, fake_llms):
    fake_llms(generator=[
        "STATUS: NEEDS_CLARIFICATION\n**CLARIFICATION_REQUIRED:** What does 'best' mean?\nREASON: No metric defines best."
    ])
    result = ask(agent_engine, "Who are the best customers?")
    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["generated_sql"] == ""
    assert result["clarification"] == "What does 'best' mean?"
    assert result["execution_result"] == {}


def test_empty_generation_is_a_graceful_error(agent_engine, fake_llms):
    fake_llms(generator=["STATUS: SUCCESS\nSQL:\n"])
    result = ask(agent_engine, "Anything")
    assert result["status"] == "ERROR"
    assert result["error_stage"] == "generate_sql"
    assert result["conversation_history"][-1]["role"] == "assistant"


def test_generator_failed_status_is_a_graceful_error(agent_engine, fake_llms):
    fake_llms(generator=["STATUS: FAILED\nREASON: The schema has no revenue data."])
    result = ask(agent_engine, "Revenue?")
    assert result["status"] == "ERROR"
    assert result["error_stage"] == "generate_sql"
    assert "revenue" in result["error"].lower()


def test_llm_exception_becomes_stage_error(agent_engine, fake_llms):
    fake_llms(linker=[RuntimeError("LLM request failed | timeout")])
    result = ask(agent_engine, "Which city has the most customers?")
    assert result["status"] == "ERROR"
    assert result["error_stage"] == "schema_link"
    assert "timeout" in result["error"]
    assert "schema linking" in result["answer"]


def test_failed_turn_does_not_leak_into_next_turn(agent_engine, fake_llms):
    thread = str(uuid.uuid4())
    fake_llms(linker=[RuntimeError("boom")])
    first = ask(agent_engine, "First question", thread)
    assert first["status"] == "ERROR"

    fake_llms(generator=[generator_output(GOOD_SQL)])
    second = ask(agent_engine, "Which city has the most customers?", thread)
    assert second["status"] == "SUCCESS"
    assert second["error"] is None
    assert second["error_stage"] == ""
    roles = [message["role"] for message in second["conversation_history"]]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_query_endpoint_contract(agent_engine, fake_llms, monkeypatch):
    from backend import main

    connection_id = "test-connection"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": agent_engine})
    fake_llms(generator=[generator_output("SELECT * FROM itest_orders")])

    client = TestClient(main.app)
    response = client.post("/query", json={"connection_id": connection_id, "question": "Show all orders"})
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "SUCCESS"
    assert "schema" not in body
    assert "raw_sql_generation" not in body
    assert body["sql"].startswith("SELECT * FROM itest_orders")
    execution = body["execution_result"]
    assert execution["row_count"] == execution["max_rows"]
    assert execution["truncated"] is True
    # numeric(10,2) values are JSON numbers, not strings
    assert isinstance(execution["rows"][0]["price"], (int, float))


def test_query_endpoint_agent_failure_is_not_http_500(agent_engine, fake_llms, monkeypatch):
    from backend import main

    connection_id = "test-connection-failure"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": agent_engine})
    fake_llms(decomposer=[RuntimeError("provider down")])

    client = TestClient(main.app)
    response = client.post("/query", json={"connection_id": connection_id, "question": "Anything"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ERROR"
    assert body["error_stage"] == "decompose"


# ============================================================
# VALUE GROUNDING (regression: 'São Paulo' vs stored 'sao paulo')
# ============================================================

def test_wrong_filter_value_is_corrected_using_stored_value(agent_engine, fake_llms):
    seen_prompts = []

    def corrector(prompt):
        seen_prompts.append(str(prompt))
        return (
            "SELECT COUNT(*) AS orders FROM itest_orders o JOIN itest_customers c "
            "ON o.customer_id = c.customer_id WHERE c.customer_city = 'sao paulo'"
        )

    fake_llms(
        generator=[generator_output(
            "SELECT COUNT(*) AS orders FROM itest_orders o JOIN itest_customers c "
            "ON o.customer_id = c.customer_id WHERE c.customer_city = 'São Paulo'"
        )],
        corrector=corrector,
    )
    result = ask(agent_engine, "How many orders came from São Paulo?")

    assert result["status"] == "SUCCESS"
    assert result["correction_attempts"] == 1
    assert "'sao paulo'" in seen_prompts[0]          # hint reached the corrector
    assert result["execution_result"]["rows"][0]["orders"] > 0


def test_nonexistent_filter_value_is_not_forced_to_correction(agent_engine, fake_llms):
    fake_llms(generator=[generator_output(
        "SELECT COUNT(*) AS customers FROM itest_customers WHERE customer_city = 'Atlantis'"
    )])
    result = ask(agent_engine, "How many customers are in Atlantis?")
    assert result["status"] == "SUCCESS"
    assert result["correction_attempts"] == 0
    assert result["execution_result"]["rows"][0]["customers"] == 0
    assert any("Atlantis" in w for w in result["validation_result"]["warnings"])


def test_follow_up_sees_exact_stored_values(agent_engine, fake_llms):
    thread = str(uuid.uuid4())
    fake_llms(
        generator=[generator_output(GOOD_SQL)],
        answer=lambda prompt: "São Paulo has the most customers.",   # prettified
    )
    first = ask(agent_engine, "Which city has the most customers?", thread)
    assistant = first["conversation_history"][-1]
    assert "customer_city=sao paulo" in assistant["evidence"]

    contexts = []

    def rewriter(prompt):
        contexts.append(str(prompt))
        return "REWRITE: Count orders from customers in sao paulo.\nCANONICAL: no"

    fake_llms(rewriter=rewriter, generator=[generator_output(GOOD_SQL)])
    ask(agent_engine, "How many orders came from that city?", thread)
    assert "DATABASE RESULT (exact stored values): customer_city=sao paulo" in contexts[0]


# ============================================================
# MULTI-QUERY REWRITING
# ============================================================

def test_query_variants_reach_retrieval_and_response(agent_engine, fake_llms, monkeypatch):
    from backend.text_to_sql import schema_focus

    seen_queries = []
    original = schema_focus.retrieve_for_queries

    def spy(schema, queries, top_k):
        seen_queries.append(list(queries))
        return original(schema=schema, queries=queries, top_k=top_k)

    monkeypatch.setattr(schema_focus, "retrieve_for_queries", spy)
    fake_llms(
        rewriter=lambda prompt: (
            "CANONICAL: no\n"
            "REWRITTEN_QUERY: Find the city with the highest number of customers.\n"
            "ALTERNATIVE_QUERIES:\n"
            "- Which town has the most clients?\n"
            "- Identify the location with the largest customer count.\n"
            "- Find the city with the highest number of customers.\n"
            "- List the city where the most buyers live."
        ),
        generator=[generator_output(GOOD_SQL)],
    )
    result = ask(agent_engine, "Which city has the most customers?")

    assert result["status"] == "SUCCESS"
    assert result["rewritten_query"] == "Find the city with the highest number of customers."
    assert result["query_variants"] == [
        "Find the city with the highest number of customers.",
        "Which town has the most clients?",
        "Identify the location with the largest customer count.",
        "List the city where the most buyers live.",
    ]
    # all variants, then the user's own question
    assert seen_queries == [result["query_variants"] + ["Which city has the most customers?"]]


def test_thread_history_endpoint(agent_engine, fake_llms, monkeypatch):
    from backend import main

    connection_id = "test-history"
    monkeypatch.setitem(main.POSTGRES_SESSIONS, connection_id, {"engine": agent_engine})
    thread = str(uuid.uuid4())
    fake_llms(generator=[generator_output(GOOD_SQL)])
    ask(agent_engine, "Which city has the most customers?", thread)

    client = TestClient(main.app)
    body = client.get(f"/threads/{thread}/history", params={"connection_id": connection_id}).json()
    assert [m["role"] for m in body["conversation_history"]] == ["user", "assistant"]

    empty = client.get(f"/threads/{uuid.uuid4()}/history", params={"connection_id": connection_id}).json()
    assert empty["conversation_history"] == []

    missing = client.get(f"/threads/{thread}/history", params={"connection_id": "nope"})
    assert missing.status_code == 404


# ============================================================
# FOCUSED SCHEMA (retrieval that scales)
# ============================================================

def test_linker_and_generator_get_focused_schema_join_and_value_facts(agent_engine, fake_llms):
    prompts = {}

    def content(prompt):
        return "\n".join(getattr(message, "content", str(message)) for message in prompt)

    def linker(prompt):
        prompts["linker"] = content(prompt)
        return "Final Relevant Schema:\nitest_orders, itest_customers"

    def generator(prompt):
        prompts["generator"] = content(prompt)
        return generator_output(
            "SELECT COUNT(*) FROM itest_orders o JOIN itest_customers c "
            "ON c.customer_id = o.customer_id WHERE c.customer_city = 'sao paulo'"
        )

    fake_llms(linker=linker, generator=generator)
    result = ask(agent_engine, "How many orders came from customers in São Paulo?")

    assert result["status"] == "SUCCESS"
    context = result["graph_context"]
    assert {"itest_orders", "itest_customers"} <= set(context["relevant_tables"])
    assert any(
        hint["table"] == "itest_customers" and hint["value"] == "sao paulo"
        for hint in context["value_hints"]
    )
    # the linker sees the focused slice, with stored values, not every table
    assert prompts["linker"].count('"table_name"') == len(context["relevant_tables"])
    assert "sao paulo" in prompts["linker"]
    # deterministic facts reach the generator
    assert "itest_customers.customer_city = 'sao paulo'" in prompts["generator"]
    assert "JOIN CONDITIONS" in prompts["generator"]


# ============================================================
# STRUCTURED SCHEMA LINKING + SQL CANDIDATES
# ============================================================

CLARIFY = "STATUS: NEEDS_CLARIFICATION\nCLARIFICATION_REQUIRED: Which metric?\nREASON: ambiguous."
WRONG_SQL = "SELECT customer_city, COUNT(*) FROM itest_customers GROUP BY 1 ORDER BY 2 ASC LIMIT 1"


def prompt_text(prompt):
    return "\n".join(getattr(message, "content", str(message)) for message in prompt)


def test_linker_json_is_verified_and_corrected_before_generation(agent_engine, fake_llms):
    seen = {}

    def generator(prompt):
        seen["generator"] = prompt_text(prompt)
        return generator_output(GOOD_SQL)

    fake_llms(
        linker=lambda prompt: json.dumps({
            "tables": ["itest_customers", "itest_invoices"],
            "columns": [{"table": "itest_customers", "column": "customer_city", "role": "dimension"}],
            "filters": [{"table": "itest_customers", "column": "customer_city", "value": "Sao Paulo"}],
        }),
        generator=generator,
    )
    result = ask(agent_engine, "Which city has the most customers?")

    assert result["status"] == "SUCCESS"
    linked = result["linked_schema"]
    assert linked["status"] == "verified"
    assert linked["tables"] == ["itest_customers"]
    assert any("itest_invoices" in issue for issue in linked["issues"])
    assert linked["filters"][0]["value"] == "sao paulo"
    assert "itest_customers.customer_city = 'sao paulo'" in seen["generator"]
    assert "itest_invoices" not in seen["generator"]


def test_majority_of_candidates_overrides_a_wrong_primary(agent_engine, fake_llms, monkeypatch):
    from backend.text_to_sql import agent

    direct_executions = []
    original = agent.execute_query
    monkeypatch.setattr(agent, "execute_query", lambda **kwargs: direct_executions.append(1) or original(**kwargs))

    fake_llms(
        generator=[generator_output(WRONG_SQL)],
        candidates=[generator_output(GOOD_SQL), generator_output(GOOD_SQL.replace("customers DESC", "2 DESC"))],
    )
    result = ask(agent_engine, "Which city has the most customers?")

    assert result["status"] == "SUCCESS"
    assert result["generated_sql"].startswith("SELECT customer_city, COUNT(*) AS customers")
    assert result["execution_result"]["rows"][0]["customer_city"] == "sao paulo"
    selection = result["sql_selection"]
    assert selection["winner"] == 1 and selection["clusters"] == [2, 1]
    assert [item["selected"] for item in result["sql_candidates"]] == [False, True, False]
    # the winner's result from selection is reused, not executed again
    assert direct_executions == []


def test_candidate_votes_decide_between_sql_and_clarification(agent_engine, fake_llms, monkeypatch):
    monkeypatch.setenv("INSIGHTX_QUESTION_CACHE", "0")  # the same question is asked twice
    fake_llms(generator=[CLARIFY], candidates=[generator_output(GOOD_SQL), generator_output(GOOD_SQL)])
    assert ask(agent_engine, "Which city has the most customers?")["status"] == "SUCCESS"

    fake_llms(generator=[generator_output(GOOD_SQL)], candidates=[CLARIFY, CLARIFY])
    result = ask(agent_engine, "Which city has the most customers?")
    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["clarification"] == "Which metric?"


def test_failing_extra_candidates_do_not_fail_the_question(agent_engine, fake_llms):
    fake_llms(
        generator=[generator_output(GOOD_SQL)],
        candidates=[RuntimeError("429 rate limited"), "no sql here"],
    )
    result = ask(agent_engine, "Which city has the most customers?")
    assert result["status"] == "SUCCESS"
    statuses = [item["status"] for item in result["sql_candidates"]]
    assert statuses[0] == "SUCCESS" and statuses[1] == "ERROR"


def test_stage_timings_are_collected(agent_engine, fake_llms):
    from backend import telemetry

    fake_llms(generator=[generator_output(GOOD_SQL)])
    with telemetry.collect() as stats:
        state = ask(agent_engine, "Which city has the most customers?")
    assert state["status"] == "SUCCESS"
    stages = stats.summary()["stages"]
    for stage in ("rewrite", "retrieve", "generate_sql", "select_sql", "execute", "generate_answer"):
        assert stage in stages and stages[stage] >= 0


def test_adaptive_candidates_use_one_for_simple_questions(monkeypatch):
    import dataclasses

    from backend.text_to_sql import agent

    monkeypatch.setattr(agent, "query_settings",
                        dataclasses.replace(agent.query_settings, adaptive_candidates=1, sql_candidates=3))
    simple = {"question": "Which city has the most customers?", "linked_schema": {"tables": ["customers"]}}
    joined = {"question": "Which city has the most orders?", "linked_schema": {"tables": ["customers", "orders"]}}
    ratio = {"question": "What percentage of customers are in SP?", "linked_schema": {"tables": ["customers"]}}
    assert agent._candidates_for(simple) == 1
    assert agent._candidates_for(joined) == 3
    assert agent._candidates_for(ratio) == 3
    monkeypatch.setattr(agent, "query_settings", dataclasses.replace(agent.query_settings, adaptive_candidates=0))
    assert agent._candidates_for(simple) == 3
