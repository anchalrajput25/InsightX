# backend/tests/test_gateway_queue.py
#
# Many people at once: free per-minute limits are never overshot, a call
# waits for the next free slot instead of failing, daily quotas still fail
# at once, and questions take turns in arrival order.

import threading
import time
from types import SimpleNamespace

import litellm
import pytest

from backend.ai_gateway.errors import AIUnavailableError
from backend.ai_gateway.gateway import Gateway, Settings
from backend.ai_gateway.health import Health
from backend.ai_gateway.routes import Route
from backend.ai_gateway.slots import QuestionSlots
from backend.ai_gateway.usage_store import UsageStore


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def ok(content="fine"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))


@pytest.fixture
def make(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def build(route, send, queue_seconds=90):
        clock, sleeps = Clock(), []

        def sleep(seconds):
            sleeps.append(seconds)
            clock.now += seconds

        settings = Settings()
        settings.cache_enabled = False
        settings.queue_seconds = queue_seconds
        gateway = Gateway(routes={"light": [route], "sql": [route], "heavy": [route]}, settings=settings,
                          health=Health(clock=clock), sender=send, sleep=sleep,
                          store_getter=lambda: UsageStore(f"sqlite:///{(tmp_path / 'u.db').as_posix()}"))
        return gateway, sleeps

    return build


def test_concurrent_requests_never_overshoot_a_per_minute_limit():
    health, route = Health(), Route("groq", "oss", rpm=5, rpd=1000)
    granted, start = [], threading.Barrier(50)

    def ask():
        start.wait()
        if health.try_reserve(route) is None:
            granted.append(1)

    threads = [threading.Thread(target=ask) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(granted) == 5


def test_a_call_waits_for_the_next_free_slot_instead_of_failing(make):
    gateway, sleeps = make(Route("groq", "oss", rpm=2, rpd=1000), lambda *a: ok())
    answers = [gateway.complete(f"q{i}", job="sql", temperature=0.3) for i in range(3)]
    assert answers == ["fine"] * 3
    assert len(sleeps) == 1 and 60 <= sleeps[0] <= 62          # waited for the minute to roll over


def test_a_rate_limit_answer_also_waits_then_retries(make):
    replies = [litellm.RateLimitError(message="limit per minute, retry in 20s", llm_provider="groq", model="m"), ok()]

    def send(*args):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    gateway, sleeps = make(Route("groq", "oss", rpm=30, rpd=1000), send)
    assert gateway.complete("q", job="sql", temperature=0.3) == "fine"
    assert sleeps and sleeps[0] <= 65


def test_a_used_up_daily_quota_fails_at_once(make):
    gateway, sleeps = make(Route("groq", "oss", rpm=30, rpd=1), lambda *a: ok())
    gateway.complete("q1", job="sql", temperature=0.3)
    with pytest.raises(AIUnavailableError, match="daily free limit"):
        gateway.complete("q2", job="sql", temperature=0.3)
    assert sleeps == []


def test_waiting_can_be_turned_off(make):
    gateway, sleeps = make(Route("groq", "oss", rpm=1, rpd=1000), lambda *a: ok(), queue_seconds=0)
    gateway.complete("q1", job="sql", temperature=0.3)
    with pytest.raises(AIUnavailableError, match="per-minute"):
        gateway.complete("q2", job="sql", temperature=0.3)
    assert sleeps == []


def test_questions_take_turns_in_arrival_order():
    slots = QuestionSlots(limit=2, timeout_s=10)
    running, peak, order, lock = [0], [0], [], threading.Lock()

    def question(index):
        with slots.hold():
            with lock:
                order.append(index)
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.05)
            with lock:
                running[0] -= 1

    threads = []
    for index in range(8):
        threads.append(threading.Thread(target=question, args=(index,)))
        threads[-1].start()
        time.sleep(0.01)                                       # arrive one after another
    for thread in threads:
        thread.join()
    assert peak[0] == 2
    assert order == sorted(order)


def test_everyone_asking_after_a_restart_profiles_the_database_once(tmp_path, monkeypatch):
    import sqlite3

    from backend import schema_profiler
    from backend.database import create_sqlite_engine
    from backend.text_to_sql.schema_context import get_agent_schema

    path = tmp_path / "restart.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL)")
    engine = create_sqlite_engine(str(path))
    real, calls = schema_profiler.get_database_schema, []

    def slow_profile(target):
        calls.append(1)
        time.sleep(0.2)                                        # long enough for everyone to arrive
        return real(target)

    monkeypatch.setattr(schema_profiler, "get_database_schema", slow_profile)
    schemas, start = [], threading.Barrier(12)

    def ask():
        start.wait()
        schemas.append(get_agent_schema(engine))

    threads = [threading.Thread(target=ask) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    engine.dispose()
    assert len(calls) == 1 and len(schemas) == 12
    assert all(schema["tables"][0]["table_name"] == "orders" for schema in schemas)


def test_a_long_queue_gives_a_clear_busy_message_and_nesting_never_deadlocks():
    slots = QuestionSlots(limit=1, timeout_s=0.2)
    with slots.hold():
        with slots.hold():                                     # same question again: no second slot needed
            pass
        failure = []

        def other():
            try:
                with slots.hold():
                    pass
            except AIUnavailableError as exc:
                failure.append(str(exc))

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
    assert failure and "Many people are asking questions right now" in failure[0]
    assert slots.status() == {"limit": 1, "answering": 0, "waiting": 0}
    with slots.hold():                                         # the slot is free again
        pass
