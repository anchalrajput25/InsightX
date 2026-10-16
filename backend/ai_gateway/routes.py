# backend/ai_gateway/routes.py
#
# Which model handles which job. Every pipeline step names a JOB; each
# job belongs to a TIER; each tier is an ordered list of ROUTES (one
# provider + one model). The gateway tries the routes of a tier in
# order and skips any that are cooling down, out of free quota, or
# missing an API key.
#
#   light  short, simple steps (rewrite, decompose, schema linking,
#          chat context, writing the answer)
#   sql    writing SQL
#   heavy  hard steps: repairing SQL that failed, planning and
#          explaining an investigation
#
# Defaults use free tiers only (Google Gemini, Groq, OpenRouter
# ":free" models). Every tier mixes providers, so one provider being
# down or out of quota never stops the app.
#
# Override a tier in .env, comma-separated, in order of preference:
#   INSIGHTX_ROUTES_LIGHT=gemini/gemini-3.5-flash-lite,groq/openai/gpt-oss-20b
# Optional per-route free limits (requests per minute / per day):
#   INSIGHTX_ROUTE_LIMITS=groq/openai/gpt-oss-120b=30:1000,gemini/gemini-3.8-flash=10:250
#
# Free plans change often; limits below are conservative defaults and
# the provider's own 429 answers always win (see health.py).

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

TIERS = ("light", "sql", "heavy")

# Pipeline step -> tier.
JOB_TIER: Dict[str, str] = {
    # old two-model names, still accepted
    "small": "light",
    "sql": "sql",
    # text-to-sql pipeline
    "rewrite": "light",
    "decompose": "light",
    "schema_link": "light",
    "context": "light",
    "answer": "light",
    "follow_ups": "light",
    "sql_generate": "sql",
    "sql_candidate": "sql",
    "sql_repair": "heavy",
    # investigation mode
    "investigation_plan": "heavy",
    "investigation_narrative": "heavy",
    # benchmark question generation (offline tool)
    "benchmark_generate": "heavy",
}


@dataclass(frozen=True)
class Route:
    provider: str  # gemini | groq | openrouter | vllm
    model: str     # the provider's model id
    rpm: Optional[int] = None  # requests per minute allowed (None = unknown)
    rpd: Optional[int] = None  # requests per day allowed
    free: bool = True

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"

    @property
    def litellm_model(self) -> str:
        if self.provider == "vllm":
            return f"openai/{self.model}"
        return self.id


# Known free-tier limits (conservative). Unknown routes get none and
# rely on the provider's 429 answers.
DEFAULT_LIMITS: Dict[str, Tuple[int, int]] = {
    "gemini/gemini-3.5-flash-lite": (15, 1000),
    "gemini/gemini-3.6-flash": (10, 250),
    "gemini/gemini-3.8-flash": (10, 250),
    "gemini/gemini-2.5-pro": (5, 100),
    "groq/openai/gpt-oss-20b": (30, 1000),
    "groq/openai/gpt-oss-120b": (30, 1000),
    "groq/qwen/qwen3.8-27b": (30, 1000),
    # OpenRouter ":free" models share one daily allowance per account
    # (about 50/day without credits, 1000/day with credits bought).
    "openrouter/google/gemma-4-31b-it:free": (20, 50),
    "openrouter/google/gemma-4-26b-a4b-it:free": (20, 50),
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free": (20, 50),
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free": (20, 50),
    "openrouter/cohere/north-mini-code:free": (20, 50),
}

DEFAULT_ROUTES: Dict[str, List[str]] = {
    "light": [
        "gemini/gemini-3.5-flash-lite",
        "groq/openai/gpt-oss-20b",
        "openrouter/google/gemma-4-26b-a4b-it:free",
    ],
    "sql": [
        "groq/openai/gpt-oss-120b",
        "gemini/gemini-3.6-flash",
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
        "groq/qwen/qwen3.8-27b",
    ],
    "heavy": [
        "gemini/gemini-3.8-flash",
        "groq/openai/gpt-oss-120b",
        "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
        "gemini/gemini-2.5-pro",
    ],
}

# Environment variable holding each provider's key (litellm reads the
# same names). vLLM is self-hosted and needs no key.
PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "vllm": None,
}


def parse_route(text: str, limits: Dict[str, Tuple[int, int]]) -> Route:
    text = text.strip()
    if "/" not in text:
        raise ValueError(f"Route '{text}' must look like provider/model, e.g. groq/openai/gpt-oss-120b")
    # Gemini and Groq keys are free-tier keys; on OpenRouter only the
    # ":free" models cost nothing.
    free = not (text.startswith("openrouter/") and not text.endswith(":free"))
    provider, model = text.split("/", 1)
    provider = provider.lower()
    if provider not in PROVIDER_KEYS:
        raise ValueError(f"Unknown provider '{provider}' in route '{text}'. Use one of: {', '.join(PROVIDER_KEYS)}")
    rpm, rpd = limits.get(f"{provider}/{model}", (None, None))
    return Route(provider=provider, model=model, rpm=rpm, rpd=rpd, free=free)


def _parse_limits(raw: str) -> Dict[str, Tuple[int, int]]:
    limits = dict(DEFAULT_LIMITS)
    for item in (raw or "").split(","):
        if "=" not in item:
            continue
        route_id, numbers = item.rsplit("=", 1)
        try:
            rpm, rpd = (int(part) for part in numbers.split(":", 1))
        except ValueError:
            continue
        limits[route_id.strip()] = (rpm, rpd)
    return limits


def load_routes(env: Optional[Dict[str, str]] = None) -> Dict[str, List[Route]]:
    env = os.environ if env is None else env
    limits = _parse_limits(env.get("INSIGHTX_ROUTE_LIMITS", ""))
    allow_paid = str(env.get("INSIGHTX_ALLOW_PAID_MODELS", "false")).lower() in ("1", "true", "yes")

    # Self-hosted vLLM replaces the hosted routes entirely.
    if str(env.get("LLM_PROVIDER", "")).lower() == "vllm":
        small = env.get("VLLM_SMALL_MODEL", "Qwen3-8B")
        sql = env.get("VLLM_SQL_MODEL", "Qwen3-Coder-30B")
        return {
            "light": [Route("vllm", small)],
            "sql": [Route("vllm", sql)],
            "heavy": [Route("vllm", sql)],
        }

    routes: Dict[str, List[Route]] = {}
    for tier in TIERS:
        raw = env.get(f"INSIGHTX_ROUTES_{tier.upper()}")
        names = [name for name in (raw.split(",") if raw else DEFAULT_ROUTES[tier]) if name.strip()]
        parsed = [parse_route(name, limits) for name in names]
        routes[tier] = [route for route in parsed if route.free or allow_paid]
    return routes


# When every model of a tier is unavailable, these tiers are tried next
# (a heavier model can always do a lighter job).
FALLBACK_TIERS = {"light": ("sql", "heavy"), "sql": ("heavy",), "heavy": ("sql",)}


def tier_for(job: str) -> str:
    if job in JOB_TIER:
        return JOB_TIER[job]
    if job in TIERS:
        return job
    raise ValueError(f"Unknown LLM job '{job}'. Known jobs: {', '.join(sorted(JOB_TIER))}")
