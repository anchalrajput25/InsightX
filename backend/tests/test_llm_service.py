# AI gateway: routing per job, failover across providers, free-limit
# tracking, circuit breaker, cache, usage ledger and per-user limits.
# No network calls: the provider is a scripted fake.

from types import SimpleNamespace

import litellm
import pytest

from backend import llm_service, telemetry
from backend.ai_gateway import context
from backend.ai_gateway.errors import AIUnavailableError, UsageLimitError, classify
from backend.ai_gateway.gateway import Gateway, Settings, set_gateway
from backend.ai_gateway.health import Health
from backend.ai_gateway.routes import Route, load_routes, tier_for
from backend.ai_gateway.usage_store import UsageStore


def ok(content="fine", prompt_tokens=100, completion_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def rate_limit(text="429 too many requests"):
    return litellm.RateLimitError(message=text, llm_provider="groq", model="m")


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


GEMINI = Route("gemini", "flash", rpm=2, rpd=5)
GROQ = Route("groq", "oss", rpm=30, rpd=1000)
ROUTER = Route("openrouter", "big:free")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    store = UsageStore(f"sqlite:///{(tmp_path / 'usage.db').as_posix()}")
    clock = Clock()
    log, script = [], {}

    def send(route, prompt, temperature, max_tokens):
        log.append(route.id)
        outcome = script.get(route.id, []).pop(0) if script.get(route.id) else ok(f"from {route.id}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    settings = Settings()
    settings.cache_enabled = True
    settings.user_daily_questions = 3
    gateway = Gateway(
        routes={"light": [GEMINI, GROQ], "sql": [GROQ, ROUTER], "heavy": [ROUTER, GROQ]},
        settings=settings, health=Health(clock=clock), sender=send, store_getter=lambda: store,
    )
    set_gateway(gateway)
    yield SimpleNamespace(gateway=gateway, store=store, clock=clock, log=log, script=script)
    set_gateway(None)


def test_each_job_goes_to_its_tier(env):
    assert tier_for("rewrite") == "light" and tier_for("sql_generate") == "sql"
    assert tier_for("sql_repair") == "heavy" and tier_for("small") == "light"
    llm_service.call_llm("a", task="rewrite")
    llm_service.call_llm("b", task="sql_generate")
    llm_service.call_llm("c", task="sql_repair")
    assert env.log == ["gemini/flash", "groq/oss", "openrouter/big:free"]
    with pytest.raises(ValueError):
        tier_for("nonsense")


def test_failover_to_next_provider_and_cooldown(env):
    env.script["gemini/flash"] = [rate_limit()]
    assert llm_service.call_llm("q1", task="rewrite") == "from groq/oss"
    # gemini is cooling down: the next call goes straight to groq
    assert llm_service.call_llm("q2", task="rewrite") == "from groq/oss"
    assert env.log == ["gemini/flash", "groq/oss", "groq/oss"]
    env.clock.now += 61
    assert llm_service.call_llm("q3", task="rewrite") == "from gemini/flash"


def test_daily_quota_or_no_credits_skips_route_until_reset(env):
    env.script["openrouter/big:free"] = [litellm.APIError(
        status_code=402, message="This request requires more credits", llm_provider="openrouter", model="m")]
    assert llm_service.call_llm("q", task="heavy") == "from groq/oss"
    env.clock.now += 3 * 3600
    assert env.gateway.health.why_unavailable(ROUTER).startswith("quota")
    assert classify(rate_limit("Quota exceeded for metric generate_requests_per_day"))[0] == "quota"
    assert classify(rate_limit("Rate limit reached on requests per day (RPD): Limit 1000"))[0] == "quota"
    # Gemini's per-minute message also says "quota": only a short pause
    assert classify(rate_limit("You exceeded your current quota. Quota exceeded for metric: "
                               "generate_content_free_tier_requests, limit: 10, quotaId: PerMinutePerProject"))[0] == "rate_limit"
    assert classify(rate_limit("Rate limit reached for model on tokens per minute (TPM): Limit 8000"))[0] == "rate_limit"


def test_free_limits_are_respected_before_the_provider_says_429(env):
    for n in range(2):
        llm_service.call_llm(f"q{n}", task="light", temperature=0.5)
    # gemini allows 2 per minute: the third call is sent to groq without trying gemini
    llm_service.call_llm("q3", task="light", temperature=0.5)
    assert env.log == ["gemini/flash", "gemini/flash", "groq/oss"]


def test_retired_model_and_missing_key_are_skipped(env, monkeypatch):
    env.script["groq/oss"] = [litellm.NotFoundError(message="model decommissioned", llm_provider="groq", model="m")]
    assert llm_service.call_llm("q", task="sql") == "from openrouter/big:free"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(AIUnavailableError, match="Groq: not found"):
        llm_service.call_llm("q2", task="sql")


def test_nothing_available_gives_a_clear_message(env):
    env.script["groq/oss"] = [rate_limit()]
    env.script["openrouter/big:free"] = [rate_limit()]
    with pytest.raises(AIUnavailableError) as error:
        llm_service.call_llm("q", task="sql")
    message = str(error.value)
    assert "All free AI models are busy" in message and "Try again in about a minute" in message
    assert error.value.wait_seconds <= 61


def test_no_keys_at_all_says_what_to_add(env, monkeypatch):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name)
    with pytest.raises(AIUnavailableError, match="Add at least one free API key"):
        llm_service.call_llm("q", task="light")
    assert env.log == []


def test_rejected_request_tries_next_model_then_fails_clearly(env):
    def rejected():
        return litellm.BadRequestError(message="invalid parameter", llm_provider="groq", model="m")

    env.script["groq/oss"] = [rejected()]
    assert llm_service.call_llm("q", task="sql") == "from openrouter/big:free"
    env.script["groq/oss"] = [rejected()]
    env.script["openrouter/big:free"] = [rejected()]
    with pytest.raises(RuntimeError, match="LLM request failed.*invalid parameter"):
        llm_service.call_llm("q2", task="sql")
    # a rejection does not pause the model for other requests
    assert env.gateway.health.why_unavailable(GROQ) is None


def test_cache_answers_repeated_deterministic_prompts(env):
    with telemetry.collect() as stats:
        assert llm_service.call_llm("same prompt", task="answer") == "from gemini/flash"
        assert llm_service.call_llm("same prompt", task="answer") == "from gemini/flash"
        llm_service.call_llm("same prompt", task="answer", temperature=0.7)  # sampled: never cached
    assert env.log == ["gemini/flash", "gemini/flash"]
    assert stats.summary()["cache_hits"] == 1


def test_ledger_records_user_job_route_tokens(env):
    with context.ai_request(user_id="asha") as request_id:
        env.script["gemini/flash"] = [rate_limit(), ]
        llm_service.call_llm("p", task="rewrite")
        llm_service.call_llm("p2", task="sql_generate")
    rows = env.store.calls(days=1)
    assert [(r["status"], r["route"], r["job"]) for r in rows] == [
        ("error", "gemini/flash", "rewrite"), ("ok", "groq/oss", "rewrite"), ("ok", "groq/oss", "sql_generate")]
    assert {r["user_id"] for r in rows} == {"asha"} and {r["request_id"] for r in rows} == {request_id}
    assert rows[1]["prompt_tokens"] == 100 and rows[1]["cost_usd"] == 0.0
    assert env.store.route_requests_today() == {"gemini/flash": 1, "groq/oss": 2}


def test_daily_question_limit_per_user(env):
    for n in range(3):
        with context.ai_request(user_id="ravi"):
            llm_service.call_llm(f"q{n}", task="rewrite")
    with pytest.raises(UsageLimitError, match="3 questions for today"):
        env.gateway.check_user_quota("ravi")
    env.gateway.check_user_quota("someone_else")


def test_counts_survive_a_restart(env):
    for n in range(5):
        llm_service.call_llm(f"q{n}", task="sql", temperature=0.5)
    fresh = Gateway(routes={"light": [GEMINI], "sql": [GROQ], "heavy": [GROQ]}, settings=env.gateway.settings,
                    health=Health(clock=env.clock), sender=lambda *a: ok(), store_getter=lambda: env.store)
    fresh._seed_from_store()
    assert fresh.health.snapshot(GROQ)["requests_today"] == 5


def test_default_routes_are_free_and_mix_providers():
    routes = load_routes({})
    for tier in ("light", "sql", "heavy"):
        assert routes[tier] and all(route.free for route in routes[tier])
        assert len({route.provider for route in routes[tier]}) >= 3
    paid = load_routes({"INSIGHTX_ROUTES_SQL": "openrouter/qwen/qwen3-coder"})
    assert all(route.id != "openrouter/qwen/qwen3-coder" for route in paid["sql"])
    allowed = load_routes({"INSIGHTX_ROUTES_SQL": "openrouter/qwen/qwen3-coder", "INSIGHTX_ALLOW_PAID_MODELS": "true"})
    assert allowed["sql"][0].id == "openrouter/qwen/qwen3-coder"
    vllm = load_routes({"LLM_PROVIDER": "vllm", "VLLM_SQL_MODEL": "coder"})
    assert vllm["sql"][0].litellm_model == "openai/coder"


def test_telemetry_follows_work_into_threads():
    from concurrent.futures import ThreadPoolExecutor

    with telemetry.collect() as stats:
        with ThreadPoolExecutor(2) as pool:
            for future in [pool.submit(telemetry.in_current_context(telemetry.record_stage), "x", 1.0) for _ in range(3)]:
                future.result()
    assert stats.summary()["stages"]["x"] == 3.0
    assert telemetry.current() is None


def test_pipeline_turns_no_capacity_into_a_plain_answer(monkeypatch):
    from backend.text_to_sql import agent

    def node(state):
        raise AIUnavailableError("All free AI models are busy right now. Try again in about a minute.")

    update = agent._guard("rewrite", node)({})
    assert update["status"] == "ERROR" and update["ai_unavailable"]
    assert update["error"].startswith("All free AI models are busy")


def test_retired_models_are_parked_at_startup(env):
    from backend.ai_gateway.discovery import check_routes

    def broken():
        raise ConnectionError("offline")

    problems = check_routes(env.gateway, listers={
        "groq": (lambda: {"oss"}, "GROQ_API_KEY"),
        "gemini": (lambda: {"other-model"}, "GEMINI_API_KEY"),
        "openrouter": (broken, None),  # unreachable: routes are kept
    })
    assert problems == {"gemini/flash": "not offered by the provider any more"}
    assert env.gateway.health.why_unavailable(GEMINI).startswith("not found")
    assert env.gateway.health.why_unavailable(ROUTER) is None
    assert llm_service.call_llm("q", task="light") == "from groq/oss"


def test_calls_outside_a_question_never_count_for_a_person(env):
    for n in range(5):
        llm_service.call_llm(f"script call {n}", task="rewrite", temperature=0.5)
    assert env.store.questions_today("local") == 0
    with pytest.raises(UsageLimitError):  # limit is 3: the five calls are all "system"
        env.gateway.check_user_quota("system")
    env.gateway.check_user_quota("local")


def test_benchmark_questions_are_logged_as_the_benchmark_user(env, monkeypatch):
    from backend.evaluation import runner
    from backend.evaluation.golden import GoldenCase

    def agent(*args, **kwargs):
        llm_service.call_llm("a", task="rewrite", temperature=0.5)
        llm_service.call_llm("b", task="sql_generate", temperature=0.5)
        raise RuntimeError("stop here")

    monkeypatch.setattr(runner, "evaluate_case", lambda case, thread_id, engine, agent_fn, lock: _run(agent_fn, case))
    cases = [GoldenCase(id=f"c{n}", question="q", expected_sql="SELECT 1") for n in range(2)]
    runner.run_cases(cases, engine=None, agent=agent)
    rows = env.store.calls(days=1)
    assert {row["user_id"] for row in rows} == {"benchmark"}
    assert env.store.questions_today("benchmark") == 2 and env.store.questions_today("local") == 0


def _run(agent, case):
    from backend.evaluation.runner import CaseResult
    try:
        agent()
    except RuntimeError:
        pass
    return CaseResult(id=case.id, question=case.question, tags=[], thread=None, status="EXCEPTION")
