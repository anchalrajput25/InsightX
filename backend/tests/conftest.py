# backend/tests/conftest.py
#
# Shared fixtures.
#
# Database tests need a disposable PostgreSQL database:
#     INSIGHTX_TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/insightx_test
# They create and drop their own itest_* tables and are skipped when the
# variable is not set. No LLM/API key is needed: LLM calls are replaced
# by scripted fakes.

import dataclasses
import os
from typing import Callable, Dict, List

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, text

TEST_DB_ENV = "INSIGHTX_TEST_DATABASE_URL"

# No provider model-list lookups from the app started by API tests.
os.environ.setdefault("INSIGHTX_ROUTE_CHECKS", "0")
# Sign-in is off by default in tests; test_auth.py turns it on.
os.environ.setdefault("INSIGHTX_AUTH_REQUIRED", "0")


@pytest.fixture(autouse=True)
def isolated_usage_store(tmp_path):
    """AI usage, cache and accounts go to throwaway files, never logs/."""
    from backend.ai_gateway.usage_store import UsageStore, set_store

    from backend.auth.store import UserStore, set_user_store

    from backend.text_to_sql.question_cache import QuestionCache, set_cache

    set_store(UsageStore(f"sqlite:///{(tmp_path / 'usage.db').as_posix()}"))
    set_user_store(UserStore(f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"))
    set_cache(QuestionCache(f"sqlite:///{(tmp_path / 'questions.db').as_posix()}"))
    from backend import tracing

    tracing.set_store(tracing.TraceStore(f"sqlite:///{(tmp_path / 'traces.db').as_posix()}"))
    from backend import chat_list, connections, history

    history.set_store(history.HistoryStore(f"sqlite:///{(tmp_path / 'history.db').as_posix()}"))
    chat_list.set_store(chat_list.ChatListStore(f"sqlite:///{(tmp_path / 'chats.db').as_posix()}"))

    connections.set_store(connections.ConnectionStore(f"sqlite:///{(tmp_path / 'connections.db').as_posix()}"))
    yield
    history.set_store(None)
    connections.set_store(None)
    chat_list.set_store(None)
    tracing.set_store(None)
    set_store(None)
    set_user_store(None)
    set_cache(None)

CUSTOMER_COUNT = 1500
ORDER_COUNT = 2500


@pytest.fixture(scope="session")
def engine():
    url = os.getenv(TEST_DB_ENV)
    if not url:
        pytest.skip(f"{TEST_DB_ENV} is not set; skipping database tests.")

    engine = create_engine(url, pool_pre_ping=True)

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS itest_orders, itest_customers"))
        connection.execute(text(
            "CREATE TABLE itest_customers AS "
            "SELECT 'c' || g AS customer_id, "
            "       CASE WHEN g % 5 < 3 THEN 'sao paulo' "
            "            WHEN g % 5 = 3 THEN 'rio de janeiro' "
            "            ELSE 'curitiba' END AS customer_city "
            f"FROM generate_series(1, {CUSTOMER_COUNT}) g"
        ))
        connection.execute(text(
            "CREATE TABLE itest_orders AS "
            "SELECT 'o' || g AS order_id, 'c' || (1 + g % "
            f"{CUSTOMER_COUNT}) AS customer_id, (g % 97)::numeric(10,2) AS price "
            f"FROM generate_series(1, {ORDER_COUNT}) g"
        ))

    yield engine

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS itest_orders, itest_customers, itest_written"))
    engine.dispose()


class FakeLLM:
    """Scripted stand-in for InsightXLLM: returns queued responses."""

    def __init__(self, responses: List[str] | Callable[[object], str]):
        self._responses = responses
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        if callable(self._responses):
            return AIMessage(content=self._responses(prompt))
        if not self._responses:
            raise AssertionError("FakeLLM called more often than scripted.")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return AIMessage(content=response)


@pytest.fixture
def fake_llms(monkeypatch) -> Callable[..., Dict[str, FakeLLM]]:
    """
    Install fake LLMs for every agent stage. Usage:
        llms = fake_llms(generator=[...], corrector=[...])
    Unspecified stages get sensible defaults.
    """
    from backend.text_to_sql import (
        answer_generator,
        query_decomposer,
        query_rewriter,
        schema_linker,
        sql_corrector,
        sql_generator,
    )

    def install(**scripts) -> Dict[str, FakeLLM]:
        defaults = {
            "rewriter": lambda prompt: "REWRITE: same\nCANONICAL: true",
            "decomposer": lambda prompt: "Single-step question.",
            "linker": lambda prompt: "Final Relevant Schema:\nitest_customers(customer_city)",
            "generator": [],
            "corrector": [],
            "answer": lambda prompt: "Grounded answer.",
        }
        # Extra SQL candidates are off unless a test scripts them, so
        # scripted generator outputs are consumed one per question.
        candidates = scripts.pop("candidates", None)
        defaults.update(scripts)
        fakes = {name: FakeLLM(script) for name, script in defaults.items()}

        from backend.text_to_sql import agent
        count = 1 if candidates is None else 1 + len(candidates)
        monkeypatch.setattr(
            agent, "query_settings",
            dataclasses.replace(agent.query_settings, sql_candidates=count, candidate_wait_s=5),
        )
        if candidates is not None:
            # Extra candidates run in threads; each one is identified by
            # its strategy text so scripted responses map deterministically
            # (candidates[0] -> candidate 1, ...).
            scripted = list(candidates)

            def respond(prompt):
                text = "\n".join(getattr(m, "content", str(m)) for m in prompt)
                for number in range(1, len(scripted) + 1):
                    strategy = sql_generator.CANDIDATE_STRATEGIES[number % len(sql_generator.CANDIDATE_STRATEGIES)]
                    if strategy and strategy in text:
                        response = scripted[number - 1]
                        if isinstance(response, Exception):
                            raise response
                        return response
                raise AssertionError("unscripted candidate")

            fakes["candidates"] = FakeLLM(respond)
            monkeypatch.setattr(agent, "candidate_llm", lambda temperature: fakes["candidates"])

        monkeypatch.setattr(query_rewriter, "llm", fakes["rewriter"])
        monkeypatch.setattr(query_decomposer, "llm", fakes["decomposer"])
        monkeypatch.setattr(schema_linker, "llm", fakes["linker"])
        monkeypatch.setattr(sql_generator, "llm", fakes["generator"])
        monkeypatch.setattr(sql_corrector, "llm", fakes["corrector"])
        monkeypatch.setattr(answer_generator, "llm", fakes["answer"])
        return fakes

    return install


@pytest.fixture
def keyword_retrieval(monkeypatch):
    """
    Use keyword-only retrieval in graph tests so they do not depend on
    downloading/loading the sentence-transformers model. The real
    multi-query fusion path (retrieve_for_queries) still runs.
    """
    from backend.text_to_sql import retriever

    monkeypatch.setattr(retriever, "semantic_similarities", lambda query, texts: None)
