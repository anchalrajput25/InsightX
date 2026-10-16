# backend/ai_gateway/gateway.py
#
# The one place every AI call goes through.
#
#   1. Cache: a deterministic call (temperature 0) whose exact prompt was
#      answered recently is served from the cache, free and instant.
#   2. Routing: the job's tier gives an ordered list of free models
#      across providers.
#   3. Health: routes that are cooling down, at their free per-minute or
#      per-day limit, or missing an API key are skipped without a call.
#   4. Failover: a failing route is marked (see health.py) and the next
#      route is tried at once; only a mistake in our own request stops
#      the search.
#   5. Ledger: every call, cache hit and failure is written to the usage
#      store with user, job, route, tokens, cost and latency.
#
# When nothing can answer, AIUnavailableError says why per provider and
# when to try again, so users see a clear message instead of a stack
# trace.

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from litellm import completion

from backend import telemetry
from backend.ai_gateway import context
from backend.ai_gateway.errors import AIUnavailableError, UsageLimitError, classify, retry_after
from backend.ai_gateway.health import Health
from backend.ai_gateway.routes import FALLBACK_TIERS, PROVIDER_KEYS, Route, load_routes, tier_for
from backend.ai_gateway.usage_store import cache_key, get_store

import logging

logger = logging.getLogger("insightx.ai_gateway")


def _flag(name: str, default: str) -> bool:
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


class Settings:
    def __init__(self):
        self.timeout = _number("LLM_TIMEOUT", 120)
        self.cache_enabled = _flag("INSIGHTX_LLM_CACHE", "true")
        self.cache_ttl_s = _number("INSIGHTX_LLM_CACHE_TTL_HOURS", 24) * 3600
        self.user_daily_questions = int(_number("INSIGHTX_USER_DAILY_QUESTIONS", 100))
        self.monthly_budget_usd = _number("INSIGHTX_MONTHLY_BUDGET_USD", 0)
        self.vllm_base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
        self.vllm_api_key = os.getenv("VLLM_API_KEY", "EMPTY")


PROVIDER_NAMES = {"gemini": "Google Gemini", "groq": "Groq", "openrouter": "OpenRouter", "vllm": "self-hosted vLLM"}


class Gateway:
    def __init__(self, routes: Optional[Dict[str, List[Route]]] = None, settings: Optional[Settings] = None,
                 health: Optional[Health] = None, sender=None, store_getter=get_store):
        self.routes = routes or load_routes()
        # tiers pinned to one model (benchmark variants): no fallback to other tiers
        self.pinned: set = set()
        self.settings = settings or Settings()
        self.health = health or Health()
        self._send = sender or self._litellm_send
        self._store = store_getter
        self._seeded = False
        self._seed_lock = threading.Lock()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def key_configured(route: Route) -> bool:
        env_name = PROVIDER_KEYS.get(route.provider)
        return env_name is None or bool(os.getenv(env_name))

    def chain(self, tier: str) -> List[Route]:
        """The tier's own models in order, then the fallback tiers' models."""
        ordered = list(self.routes.get(tier, []))
        if tier not in self.pinned:
            for other in FALLBACK_TIERS.get(tier, ()):
                ordered += [route for route in self.routes.get(other, []) if route not in ordered]
        return ordered

    def all_routes(self) -> List[Route]:
        seen, unique = set(), []
        for tier_routes in self.routes.values():
            for route in tier_routes:
                if route.id not in seen:
                    seen.add(route.id)
                    unique.append(route)
        return unique

    def _seed_from_store(self) -> None:
        """After a restart, restore today's per-route counts so free daily limits still hold."""
        with self._seed_lock:
            if self._seeded:
                return
            self._seeded = True
            try:
                counts = self._store().route_requests_today()
            except Exception:  # the ledger must never block answering
                logger.warning("Could not read today's usage; free-limit counters start at zero", exc_info=True)
                return
            for route in self.all_routes():
                if counts.get(route.id):
                    self.health.seed_day_count(route, counts[route.id])

    def _litellm_send(self, route: Route, prompt: str, temperature: float, max_tokens: int):
        kwargs: Dict[str, Any] = dict(
            model=route.litellm_model,
            messages=[{"role": "user", "content": str(prompt)}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=self.settings.timeout,
            num_retries=0,
        )
        if route.provider == "vllm":
            kwargs.update(api_base=self.settings.vllm_base_url, api_key=self.settings.vllm_api_key)
        elif route.provider == "openrouter":
            kwargs.update(api_base=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                          api_key=os.getenv("OPENROUTER_API_KEY"))
        else:
            kwargs.update(api_key=os.getenv(PROVIDER_KEYS[route.provider]))
        return completion(**kwargs)

    @staticmethod
    def _content(response) -> str:
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, KeyError) as exc:
            raise RuntimeError("Unable to extract content from LLM response.") from exc
        if content is None or not str(content).strip():
            raise RuntimeError("LLM returned an empty response.")
        return str(content).strip()

    @staticmethod
    def _usage(response) -> Tuple[int, int, int]:
        usage = getattr(response, "usage", None) or {}

        def number(source, name):
            value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        details = (usage.get("prompt_tokens_details") if isinstance(usage, dict)
                   else getattr(usage, "prompt_tokens_details", None)) or {}
        return number(usage, "prompt_tokens"), number(usage, "completion_tokens"), number(details, "cached_tokens")

    @staticmethod
    def _cost(route: Route, response) -> Optional[float]:
        if route.free or route.provider == "vllm":
            return 0.0
        try:
            import litellm

            return float(litellm.completion_cost(completion_response=response))
        except Exception:
            hidden = getattr(response, "_hidden_params", None) or {}
            value = hidden.get("response_cost")
            return float(value) if isinstance(value, (int, float)) else None

    def _record(self, **row: Any) -> None:
        try:
            self._store().record(user_id=context.current_user(), request_id=context.current_request(), **row)
        except Exception:
            logger.warning("Could not write AI usage record", exc_info=True)

    # ------------------------------------------------------------ limits
    def check_user_quota(self, user_id: str) -> None:
        """Raise UsageLimitError when a user has asked their daily number of questions."""
        limit = self.settings.user_daily_questions
        if limit <= 0:
            return
        try:
            used = self._store().questions_today(user_id)
        except Exception:
            logger.warning("Could not read usage for %s; allowing the question", user_id, exc_info=True)
            return
        if used >= limit:
            raise UsageLimitError(
                f"You have used your {limit} questions for today. The limit resets at midnight UTC. "
                "Ask the admin to raise INSIGHTX_USER_DAILY_QUESTIONS if you need more."
            )

    def _check_budget(self, routes: List[Route]) -> List[Route]:
        """With a monthly budget set, paid routes are dropped once it is spent (free ones keep working)."""
        budget = self.settings.monthly_budget_usd
        if budget <= 0 or all(route.free for route in routes):
            return routes
        try:
            spent = self._store().spend_this_month()
        except Exception:
            return routes
        if spent < budget:
            return routes
        free = [route for route in routes if route.free]
        if not free:
            raise UsageLimitError(f"This month's AI budget (${budget:.2f}) is used up.")
        return free

    # -------------------------------------------------------------- call
    def complete(self, prompt: str, job: str = "light", temperature: float = 0.0, max_tokens: int = 2500) -> str:
        if not prompt or not str(prompt).strip():
            raise ValueError("LLM prompt cannot be empty.")
        tier = tier_for(job)
        self._seed_from_store()
        routes = self._check_budget(self.chain(tier))

        cacheable = self.settings.cache_enabled and float(temperature) == 0.0
        key = cache_key(job, prompt, max_tokens) if cacheable else None
        if key:
            try:
                cached = self._store().cache_get(key, self.settings.cache_ttl_s)
            except Exception:
                cached = None
            if cached is not None:
                self._record(job=job, tier=tier, route="cache", provider="cache", status="cache")
                telemetry.record_cache_hit(job)
                return cached

        attempts: List[Tuple[Route, str]] = []
        last_rejection = None
        for route in routes:
            if not self.key_configured(route):
                attempts.append((route, f"no {PROVIDER_KEYS[route.provider]} in .env"))
                continue
            blocked = self.health.why_unavailable(route)
            if blocked:
                attempts.append((route, blocked))
                continue

            self.health.reserve(route)
            logger.info("LLM request | job=%s | tier=%s | route=%s", job, tier, route.id)
            started = time.perf_counter()
            try:
                response = self._send(route, prompt, temperature, max_tokens)
                content = self._content(response)
            except Exception as exc:
                seconds = time.perf_counter() - started
                if isinstance(exc, RuntimeError) and "LLM returned an empty response" in str(exc):
                    kind, message = "unavailable", "empty response"
                else:
                    kind, message = classify(exc)
                self.health.failure(route, kind, message, retry_after(exc))
                telemetry.record_llm_failure(job, route.id, seconds, f"{kind}: {message}")
                self._record(job=job, tier=tier, route=route.id, provider=route.provider, status="error",
                             error_kind=kind, latency_ms=seconds * 1000)
                logger.warning("LLM %s on %s (%s): %s", kind, route.id, job, message)
                # A rejected request is usually model-specific (an unsupported
                # parameter, a stricter format): the next model still gets a try.
                attempts.append((route, kind.replace("_", " ")))
                last_rejection = (route, message, exc) if kind == "bad_request" else None
                continue

            seconds = time.perf_counter() - started
            self.health.success(route)
            prompt_tokens, completion_tokens, cached_tokens = self._usage(response)
            cost = self._cost(route, response)
            telemetry.record_llm(job, route.id, response, seconds, cost=cost)
            self._record(job=job, tier=tier, route=route.id, provider=route.provider, status="ok",
                         prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                         cached_tokens=cached_tokens, cost_usd=cost or 0.0, latency_ms=seconds * 1000)
            if key:
                try:
                    self._store().cache_put(key, job, route.id, content)
                except Exception:
                    logger.warning("Could not write AI cache", exc_info=True)
            return content

        if last_rejection and all(why == "bad request" for _, why in attempts if not why.startswith("no ")):
            route, message, exc = last_rejection
            raise RuntimeError(f"LLM request failed | job={job} | route={route.id} | error={message}") from exc
        raise self._unavailable(job, routes, attempts)

    def _unavailable(self, job: str, routes: List[Route], attempts: List[Tuple[Route, str]]) -> AIUnavailableError:
        usable = [route for route in routes if self.key_configured(route)]
        if not usable:
            needed = sorted({PROVIDER_KEYS[route.provider] for route in routes if PROVIDER_KEYS.get(route.provider)})
            return AIUnavailableError(
                "No AI provider is set up. Add at least one free API key to .env: " + ", ".join(needed) + ".",
                attempts=[(r.id, why) for r, why in attempts],
            )
        wait = min(self.health.wait_hint(route) for route in usable)
        by_provider: Dict[str, str] = {}
        for route, why in attempts:
            by_provider.setdefault(PROVIDER_NAMES.get(route.provider, route.provider), why)
        reasons = "; ".join(f"{name}: {why}" for name, why in by_provider.items())
        when = ("in about a minute" if wait < 90 else f"in about {int(wait // 60)} minutes" if wait < 3600
                else f"in about {int(wait // 3600)} hours")
        return AIUnavailableError(
            f"All free AI models are busy or out of their free limits right now ({reasons}). Try again {when}.",
            wait_seconds=wait, attempts=[(r.id, why) for r, why in attempts],
        )

    # ------------------------------------------------------------ status
    def status(self) -> List[Dict[str, Any]]:
        rows = []
        for tier, tier_routes in self.routes.items():
            for position, route in enumerate(tier_routes):
                rows.append({
                    "tier": tier, "position": position + 1, "route": route.id,
                    "provider": route.provider, "model": route.model, "free": route.free,
                    "rpm_limit": route.rpm, "rpd_limit": route.rpd,
                    "key_configured": self.key_configured(route),
                    "blocked_reason": self.health.why_unavailable(route) or "",
                    **self.health.snapshot(route),
                })
        return rows


_gateway: Optional[Gateway] = None
_gateway_lock = threading.Lock()


def get_gateway() -> Gateway:
    global _gateway
    with _gateway_lock:
        if _gateway is None:
            _gateway = Gateway()
        return _gateway


def set_gateway(gateway: Optional[Gateway]) -> None:
    global _gateway
    with _gateway_lock:
        _gateway = gateway
