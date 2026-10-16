# backend/ai_gateway/report.py
#
# Usage summaries for the "AI usage" page, built from the ledger, plus
# the live OpenRouter key status. Only counts, tokens, costs and
# timings: no prompts or answers.

import os
import statistics
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

import requests

from backend.ai_gateway.usage_store import get_store


def _p50(values: List[float]) -> Optional[float]:
    return round(statistics.median(values), 1) if values else None


def usage_summary(days: int = 30, user_id: Optional[str] = None) -> Dict[str, Any]:
    rows = get_store().calls(days=days, user_id=user_id)
    sent = [r for r in rows if r["status"] != "cache"]
    ok = [r for r in rows if r["status"] == "ok"]
    cached = [r for r in rows if r["status"] == "cache"]
    failed = [r for r in rows if r["status"] == "error"]
    questions = {r["request_id"] for r in rows}

    by_day: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_day[r["day"]][r["provider"] if r["status"] != "cache" else "cache"] += 1

    def group(key: str) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in rows:
            buckets[r[key]].append(r)
        result = []
        for name, items in buckets.items():
            good = [i for i in items if i["status"] == "ok"]
            result.append({
                key: name,
                "requests": sum(1 for i in items if i["status"] != "cache"),
                "ok": len(good),
                "failed": sum(1 for i in items if i["status"] == "error"),
                "cache_hits": sum(1 for i in items if i["status"] == "cache"),
                "questions": len({i["request_id"] for i in items}),
                "tokens": sum(i["prompt_tokens"] + i["completion_tokens"] for i in good),
                "cost_usd": round(sum(i["cost_usd"] for i in good), 6),
                "p50_latency_ms": _p50([i["latency_ms"] for i in good]),
            })
        return sorted(result, key=lambda item: -item["requests"] - item["cache_hits"])

    failures: Dict[str, int] = defaultdict(int)
    for r in failed:
        failures[r["error_kind"] or "unknown"] += 1

    lookups = len(ok) + len(cached)
    return {
        "days": days,
        "user_id": user_id,
        "totals": {
            "questions": len(questions),
            "requests": len(sent),
            "successful": len(ok),
            "failed": len(failed),
            "cache_hits": len(cached),
            "cache_hit_rate": round(len(cached) / lookups, 4) if lookups else None,
            "prompt_tokens": sum(r["prompt_tokens"] for r in ok),
            "completion_tokens": sum(r["completion_tokens"] for r in ok),
            "cached_prompt_tokens": sum(r["cached_tokens"] for r in ok),
            "cost_usd": round(sum(r["cost_usd"] for r in ok), 6),
            "calls_per_question": round(len(sent) / len(questions), 2) if questions else None,
            "p50_latency_ms": _p50([r["latency_ms"] for r in ok]),
        },
        "by_day": [{"day": day, **dict(counts)} for day, counts in sorted(by_day.items())],
        "by_route": [item for item in group("route") if item["route"] != "cache"],
        "by_job": group("job"),
        "by_user": group("user_id"),
        "failures": [{"kind": kind, "count": count} for kind, count in sorted(failures.items(), key=lambda x: -x[1])],
    }


_credits_cache: Dict[str, Any] = {}
_credits_lock = threading.Lock()


def openrouter_key_status(max_age_s: float = 60) -> Dict[str, Any]:
    """OpenRouter's own view of the key: usage, limit, free tier. Cached briefly."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return {"configured": False}
    with _credits_lock:
        if _credits_cache and time.time() - _credits_cache["fetched"] < max_age_s:
            return _credits_cache["value"]
    try:
        response = requests.get(
            f"{os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')}/key",
            headers={"Authorization": f"Bearer {key}"}, timeout=10,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        value = {
            "configured": True,
            "usage_usd": data.get("usage"),
            "limit_usd": data.get("limit"),
            "limit_remaining_usd": data.get("limit_remaining"),
            "is_free_tier": data.get("is_free_tier"),
        }
    except Exception as exc:  # shown on the page, never fatal
        value = {"configured": True, "error": f"Could not reach OpenRouter: {type(exc).__name__}"}
    with _credits_lock:
        _credits_cache.update(fetched=time.time(), value=value)
    return value
