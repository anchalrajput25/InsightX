# backend/ai_gateway/discovery.py
#
# Free models get retired often (OpenRouter retired qwen3-8b on
# 2026-10-09 with no warning). At startup, and then every few hours,
# ask each provider which models it currently serves and park any
# configured route whose model is gone, so no user request is wasted
# on it. A provider that cannot be reached is left alone: the normal
# failover still covers it.

import logging
import os
import threading
from typing import Dict, Iterable, Optional, Set

import requests

from backend.ai_gateway.gateway import Gateway, get_gateway

logger = logging.getLogger("insightx.ai_gateway")

REFRESH_SECONDS = 6 * 3600


def _openrouter() -> Set[str]:
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    data = requests.get(f"{base}/models", timeout=15).json()["data"]
    return {item["id"] for item in data}


def _groq() -> Set[str]:
    response = requests.get("https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"}, timeout=15)
    response.raise_for_status()
    return {item["id"] for item in response.json()["data"]}


def _gemini() -> Set[str]:
    names: Set[str] = set()
    token: Optional[str] = None
    for _ in range(10):
        params = {"key": os.getenv("GEMINI_API_KEY"), "pageSize": 1000}
        if token:
            params["pageToken"] = token
        response = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params=params, timeout=15)
        response.raise_for_status()
        body = response.json()
        names |= {item["name"].split("/", 1)[-1] for item in body.get("models", [])}
        token = body.get("nextPageToken")
        if not token:
            break
    return names


LISTERS = {"openrouter": (_openrouter, None), "groq": (_groq, "GROQ_API_KEY"), "gemini": (_gemini, "GEMINI_API_KEY")}


def check_routes(gateway: Optional[Gateway] = None, listers: Optional[Dict] = None) -> Dict[str, str]:
    """Park routes whose model the provider no longer lists. Returns {route id: problem}."""
    gateway = gateway or get_gateway()
    listers = listers or LISTERS
    routes = gateway.all_routes()
    problems: Dict[str, str] = {}
    for provider in sorted({route.provider for route in routes}):
        if provider not in listers:
            continue
        lister, key_name = listers[provider]
        if key_name and not os.getenv(key_name):
            continue
        try:
            available = lister()
        except Exception as exc:
            logger.warning("Could not list %s models (%s); keeping its routes", provider, type(exc).__name__)
            continue
        for route in (r for r in routes if r.provider == provider):
            if route.model not in available:
                problems[route.id] = "not offered by the provider any more"
                gateway.health.failure(route, "not_found", "model not in the provider's model list")
                logger.warning("AI route %s is not available at %s; skipping it", route.id, provider)
    return problems


def start_background_checks(gateway: Optional[Gateway] = None) -> threading.Event:
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                check_routes(gateway)
            except Exception:
                logger.warning("AI route check failed", exc_info=True)
            stop.wait(REFRESH_SECONDS)

    threading.Thread(target=loop, name="insightx-route-check", daemon=True).start()
    return stop


def describe(routes: Iterable) -> str:
    return ", ".join(route.id for route in routes)
