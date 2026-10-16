# backend/llmops/routing.py
#
# Smart (auto) routing: choose the order in which free models are tried
# for each AI call, from measured data instead of a fixed list.
#
# For every route in a tier it scores
#   accuracy  - share of evaluation cases it took part in that passed
#   success   - share of its calls in the last 7 days that worked
#   speed     - its median latency, compared with the others
#   headroom  - how much of today's free limit is left (0 when blocked)
# and weights them by how hard the current question looks:
#   easy   -> speed and headroom matter most (save the strong models)
#   hard   -> accuracy matters most
#
# Modes (set on the Routing page, default off):
#   off     the gateway is not touched at all
#   shadow  scores and the order it would pick are logged; the normal
#           order is still used (safe way to see what it would do)
#   on      the tier's models are tried in the scored order; the
#           gateway's own failover, health checks and limits still apply
#
# The swap is a thin subclass of the existing Gateway that shares its
# routes, health and settings, so turning routing off restores the
# original object exactly.

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

from backend.ai_gateway.gateway import Gateway, get_gateway, set_gateway
from backend.ai_gateway.routes import FALLBACK_TIERS, Route

logger = logging.getLogger("insightx.llmops.routing")

MODES = ("off", "shadow", "on")
WEIGHTS = {
    "easy":   {"accuracy": 0.15, "success": 0.30, "speed": 0.35, "headroom": 0.20},
    "medium": {"accuracy": 0.35, "success": 0.30, "speed": 0.20, "headroom": 0.15},
    "hard":   {"accuracy": 0.55, "success": 0.25, "speed": 0.10, "headroom": 0.10},
}
PRIOR_CALLS = 5          # smoothing: a new route starts near the average
DEFAULT_ACCURACY = 0.6
STATS_TTL = 60.0
MODE_TTL = 5.0

_decisions: deque = deque(maxlen=300)
_lock = threading.Lock()
_stats_cache: Dict[str, Any] = {"at": 0.0, "value": {}}
_mode_cache: Dict[str, Any] = {"at": 0.0, "value": "off"}
_original: Dict[str, Any] = {"gateway": None}


# ------------------------------------------------------------- settings
def get_mode(fresh: bool = False) -> str:
    if not fresh and time.time() - _mode_cache["at"] < MODE_TTL:
        return _mode_cache["value"]
    try:
        from backend.llmops.store import get_store

        mode = str(get_store().get_setting("routing_mode", "off") or "off")
    except Exception:
        mode = "off"
    mode = mode if mode in MODES else "off"
    _mode_cache.update(at=time.time(), value=mode)
    return mode


def set_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    from backend.llmops.store import get_store

    get_store().set_setting("routing_mode", mode)
    _mode_cache.update(at=time.time(), value=mode)
    sync()
    return mode


# ---------------------------------------------------------------- stats
def route_stats(fresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Per-route success and latency over 7 days from the usage ledger (cached)."""
    if not fresh and time.time() - _stats_cache["at"] < STATS_TTL:
        return _stats_cache["value"]
    from backend.ai_gateway.usage_store import get_store as usage_store

    grouped: Dict[str, Dict[str, Any]] = {}
    try:
        calls = usage_store().calls(days=7)
    except Exception:
        calls = []
    for call in calls:
        if call["status"] == "cache":
            continue
        item = grouped.setdefault(call["route"], {"calls": 0, "ok": 0, "latency": []})
        item["calls"] += 1
        if call["status"] == "ok":
            item["ok"] += 1
            item["latency"].append(float(call.get("latency_ms") or 0) / 1000)
    value = {}
    for route, item in grouped.items():
        lat = sorted(item["latency"])
        value[route] = {
            "calls": item["calls"],
            "success_rate": item["ok"] / item["calls"] if item["calls"] else None,
            "p50_seconds": lat[len(lat) // 2] if lat else None,
        }
    _stats_cache.update(at=time.time(), value=value)
    return value


def _accuracy() -> Dict[str, Dict[str, float]]:
    try:
        from backend.llmops.evals import route_accuracy

        return route_accuracy()
    except Exception:
        return {}


def score_routes(gateway: Gateway, routes: List[Route], level: str) -> List[Dict[str, Any]]:
    weights = WEIGHTS.get(level or "medium", WEIGHTS["medium"])
    stats = route_stats()
    accuracy = _accuracy()
    latencies = [stats[r.id]["p50_seconds"] for r in routes if stats.get(r.id, {}).get("p50_seconds")]
    fastest = min(latencies) if latencies else None
    known_success = [s["success_rate"] for s in stats.values() if s.get("success_rate") is not None]
    mean_success = sum(known_success) / len(known_success) if known_success else 0.9

    scored = []
    for position, route in enumerate(routes):
        s = stats.get(route.id, {})
        calls = s.get("calls", 0)
        # smoothed success: a route with few calls stays near the average
        success = ((s.get("success_rate") or 0) * calls + mean_success * PRIOR_CALLS) / (calls + PRIOR_CALLS)
        p50 = s.get("p50_seconds")
        speed = (fastest / p50) if (fastest and p50) else 0.6
        acc_item = accuracy.get(route.id)
        acc = acc_item["accuracy"] if acc_item else DEFAULT_ACCURACY
        try:
            blocked = gateway.health.why_unavailable(route)
            snap = gateway.health.snapshot(route)
        except Exception:
            blocked, snap = None, {}
        if blocked or not gateway.key_configured(route):
            headroom = 0.0
        elif route.rpd:
            headroom = max(0.0, 1 - (snap.get("requests_today", 0) / route.rpd))
        else:
            headroom = 0.8
        parts = {"accuracy": acc, "success": success, "speed": speed, "headroom": headroom}
        total = sum(weights[k] * parts[k] for k in weights)
        # tiny tie-break keeps the configured order when scores match
        total -= position * 1e-4
        scored.append({
            "route": route.id, "score": round(total, 4), "position": position + 1,
            "parts": {k: round(v, 3) for k, v in parts.items()},
            "blocked": bool(blocked) or not gateway.key_configured(route),
            "calls_7d": calls, "eval_cases": acc_item["cases"] if acc_item else 0,
        })
    return scored


# ---------------------------------------------------------------- gateway
class SmartGateway(Gateway):
    """The existing gateway with a scored chain(). Shares all its state."""

    def chain(self, tier: str) -> List[Route]:
        base = super().chain(tier)
        mode = get_mode()
        if mode == "off" or tier in self.pinned:
            return base
        try:
            own = list(self.routes.get(tier, []))
            if len(own) < 2:
                return base
            from backend.ai_gateway.context import current_user
            from backend.llmops.capture import difficulty_now

            user = current_user()
            level = difficulty_now(user) or "medium"
            scored = score_routes(self, own, level)
            ranked = sorted(scored, key=lambda item: (item["blocked"], -item["score"]))
            by_id = {route.id: route for route in own}
            ordered = [by_id[item["route"]] for item in ranked]
            changed = [r.id for r in ordered] != [r.id for r in own]
            _record({
                "ts": time.time(), "tier": tier, "difficulty": level, "mode": mode, "user": user,
                "default": [r.id for r in own], "chosen": [r.id for r in ordered], "changed": changed,
                "scores": scored,
            })
            if mode != "on":
                return base
            rest = [route for route in base if route not in ordered]
            return ordered + rest
        except Exception:
            logger.warning("Smart routing failed; using the normal order", exc_info=True)
            return base


def _record(decision: Dict[str, Any]) -> None:
    with _lock:
        _decisions.append(decision)


def decisions(user_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        items = list(_decisions)
    if user_id is not None:
        items = [d for d in items if d.get("user") == user_id]
    items = items[-limit:]
    items.reverse()
    return [{k: v for k, v in d.items() if k != "user"} for d in items]


def sync() -> None:
    """Wrap or unwrap the gateway to match the mode. Off = original object."""
    from backend.ai_gateway import gateway as gateway_module

    mode = get_mode()
    if mode == "off":
        # read the slot directly: never create a gateway just to leave it alone
        current = gateway_module._gateway
        if isinstance(current, SmartGateway) and _original["gateway"] is not None:
            set_gateway(_original["gateway"])
        return
    current = get_gateway()
    if isinstance(current, SmartGateway) or type(current) is not Gateway:
        return  # already wrapped, or a test double we leave alone
    smart = SmartGateway.__new__(SmartGateway)
    smart.__dict__ = current.__dict__  # same routes, health, settings, counters
    _original["gateway"] = current
    set_gateway(smart)


def preview(level: str) -> List[Dict[str, Any]]:
    """For the Routing page: how each tier would be ordered for a question of this difficulty."""
    gateway = get_gateway()
    out = []
    for tier, routes in gateway.routes.items():
        scored = score_routes(gateway, list(routes), level)
        ranked = sorted(scored, key=lambda item: (item["blocked"], -item["score"]))
        out.append({"tier": tier, "default": [r.id for r in routes], "ranked": ranked,
                    "fallback_tiers": list(FALLBACK_TIERS.get(tier, ()))})
    return out


def summary() -> Dict[str, Any]:
    with _lock:
        items = list(_decisions)
    changed = sum(1 for d in items if d["changed"])
    by_level: Dict[str, int] = {}
    first_choice: Dict[str, int] = {}
    for d in items:
        by_level[d["difficulty"]] = by_level.get(d["difficulty"], 0) + 1
        if d["chosen"]:
            first_choice[d["chosen"][0]] = first_choice.get(d["chosen"][0], 0) + 1
    return {"mode": get_mode(), "decisions": len(items), "changed": changed,
            "by_difficulty": by_level, "first_choice": first_choice,
            "installed": isinstance(get_gateway(), SmartGateway)}
