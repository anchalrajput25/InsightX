# backend/ai_gateway/health.py
#
# Live state of every route, shared by all requests in this process:
#   - request counters per minute and per day, so the gateway stops
#     sending to a route BEFORE its free limit answers 429;
#   - a circuit breaker: after a failure the route "cools down" for a
#     while and is skipped, instead of making every user wait for the
#     same error again.
#
# How long a route cools down depends on why it failed (see
# errors.classify): a rate limit waits a minute (or the provider's
# Retry-After), an exhausted daily quota waits until the quota resets,
# a retired model is skipped for a day.

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional

from backend.ai_gateway.routes import Route

COOLDOWN_SECONDS = {
    "rate_limit": 60,
    "unavailable": 30,
    "not_found": 24 * 3600,
    "auth": 3600,
    "quota": None,  # until the daily reset
}

# Consecutive "unavailable" failures before the breaker opens; a single
# blip only costs one retry on the next route.
UNAVAILABLE_THRESHOLD = 2


def _next_daily_reset(now: float) -> float:
    """Free daily quotas reset at midnight US Pacific (Gemini) or UTC; use the later one."""
    moment = datetime.fromtimestamp(now, tz=timezone.utc)
    pacific = moment - timedelta(hours=8)
    reset = (pacific.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) + timedelta(hours=8))
    return reset.timestamp()


@dataclass
class RouteState:
    minute: Deque[float] = field(default_factory=deque)
    day_key: str = ""
    day_count: int = 0
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    consecutive_unavailable: int = 0
    successes: int = 0
    failures: int = 0
    last_error: str = ""


class Health:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._states: Dict[str, RouteState] = {}

    def _state(self, route: Route) -> RouteState:
        return self._states.setdefault(route.id, RouteState())

    @staticmethod
    def _day(now: float) -> str:
        return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")

    def _refresh(self, state: RouteState, now: float) -> None:
        while state.minute and now - state.minute[0] >= 60:
            state.minute.popleft()
        day = self._day(now)
        if state.day_key != day:
            state.day_key, state.day_count = day, 0

    def why_unavailable(self, route: Route) -> Optional[str]:
        """None when the route may be used now, else a short reason."""
        now = self._clock()
        with self._lock:
            state = self._state(route)
            self._refresh(state, now)
            if state.cooldown_until > now:
                return f"{state.cooldown_reason} (retry in {int(state.cooldown_until - now) + 1}s)"
            if route.rpm and len(state.minute) >= route.rpm:
                return "per-minute free limit reached"
            if route.rpd and state.day_count >= route.rpd:
                return "daily free limit reached"
        return None

    def wait_hint(self, route: Route) -> float:
        """Seconds until this route is usable again (0 if usable now)."""
        now = self._clock()
        with self._lock:
            state = self._state(route)
            self._refresh(state, now)
            waits = [max(0.0, state.cooldown_until - now)]
            if route.rpm and len(state.minute) >= route.rpm:
                waits.append(60 - (now - state.minute[0]))
            if route.rpd and state.day_count >= route.rpd:
                waits.append(_next_daily_reset(now) - now)
            return max(waits)

    def reserve(self, route: Route) -> None:
        """Count a request against the route's free limits (before sending it)."""
        now = self._clock()
        with self._lock:
            state = self._state(route)
            self._refresh(state, now)
            state.minute.append(now)
            state.day_count += 1

    def success(self, route: Route) -> None:
        with self._lock:
            state = self._state(route)
            state.successes += 1
            state.consecutive_unavailable = 0
            if state.cooldown_reason and state.cooldown_until <= self._clock():
                state.cooldown_reason = ""

    def failure(self, route: Route, kind: str, message: str = "", retry_after: Optional[float] = None) -> None:
        now = self._clock()
        with self._lock:
            state = self._state(route)
            state.failures += 1
            state.last_error = f"{kind}: {message}"[:300]
            if kind == "unavailable":
                state.consecutive_unavailable += 1
                if state.consecutive_unavailable < UNAVAILABLE_THRESHOLD:
                    return
            if kind not in COOLDOWN_SECONDS:
                return
            if kind == "quota":
                until = _next_daily_reset(now)
            elif kind == "rate_limit" and retry_after:
                until = now + min(max(retry_after, 1.0), 3600)
            else:
                until = now + COOLDOWN_SECONDS[kind]
            if until > state.cooldown_until:
                state.cooldown_until, state.cooldown_reason = until, kind.replace("_", " ")

    def snapshot(self, route: Route) -> Dict[str, object]:
        now = self._clock()
        with self._lock:
            state = self._state(route)
            self._refresh(state, now)
            return {
                "requests_last_minute": len(state.minute),
                "requests_today": state.day_count,
                "cooldown_seconds": max(0, int(state.cooldown_until - now)),
                "cooldown_reason": state.cooldown_reason if state.cooldown_until > now else "",
                "successes": state.successes,
                "failures": state.failures,
                "last_error": state.last_error,
            }

    def seed_day_count(self, route: Route, count: int) -> None:
        """Restore today's count after a restart (from the usage store)."""
        now = self._clock()
        with self._lock:
            state = self._state(route)
            self._refresh(state, now)
            state.day_count = max(state.day_count, int(count))
