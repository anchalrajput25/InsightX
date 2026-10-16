# backend/ai_gateway/usage_store.py
#
# Durable record of every AI call (the "ledger") plus the response
# cache. Defaults to a SQLite file in logs/; for a deployment with
# several backend servers point INSIGHTX_USAGE_DB_URL at PostgreSQL so
# all servers share one ledger and one cache.
#
# Stored per call: time, user, job, model route, outcome, tokens, cost,
# latency. Never stored: prompts, API keys, database passwords. The
# cache keeps model replies keyed by a SHA-256 of the prompt.

import hashlib
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import (Column, Float, Index, Integer, MetaData, String, Table, Text, delete,
                        func, insert, select, update)
from sqlalchemy.engine import Engine

from backend import app_db

BASE_DIR = Path(__file__).resolve().parents[2]

metadata = MetaData()

ai_calls = Table(
    "insightx_ai_calls", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("day", String(10), nullable=False),
    Column("user_id", String(120), nullable=False),
    Column("request_id", String(64), nullable=False),
    Column("job", String(40), nullable=False),
    Column("tier", String(10), nullable=False),
    Column("route", String(160), nullable=False),
    Column("provider", String(20), nullable=False),
    Column("status", String(10), nullable=False),  # ok | error | cache
    Column("error_kind", String(30), nullable=False, default=""),
    Column("prompt_tokens", Integer, nullable=False, default=0),
    Column("completion_tokens", Integer, nullable=False, default=0),
    Column("cached_tokens", Integer, nullable=False, default=0),
    Column("cost_usd", Float, nullable=False, default=0.0),
    Column("latency_ms", Float, nullable=False, default=0.0),
    Index("ix_insightx_ai_calls_day", "day"),
    Index("ix_insightx_ai_calls_user_day", "user_id", "day"),
)

ai_cache = Table(
    "insightx_ai_cache", metadata,
    Column("key", String(64), primary_key=True),
    Column("created", Float, nullable=False),
    Column("job", String(40), nullable=False),
    Column("route", String(160), nullable=False),
    Column("response", Text, nullable=False),
    Column("hits", Integer, nullable=False, default=0),
)


def today(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(now or time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


def cache_key(job: str, prompt: str, max_tokens: int) -> str:
    # The tier, not the exact model, is part of the key: any model of the
    # same tier gives an acceptable answer for a deterministic prompt.
    from backend.ai_gateway.routes import tier_for

    raw = f"{tier_for(job)}\x00{max_tokens}\x00{prompt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class UsageStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_USAGE_DB_URL", "usage.db")
        self._lock = threading.Lock()  # SQLite allows one writer at a time
        metadata.create_all(self.engine)

    # ---------------------------------------------------------- ledger
    def record(self, **row: Any) -> None:
        now = row.pop("ts", None) or time.time()
        values = {
            "ts": now, "day": today(now), "error_kind": "", "prompt_tokens": 0, "completion_tokens": 0,
            "cached_tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0, **row,
        }
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(ai_calls).values(**values))

    def questions_today(self, user_id: str) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(
                select(func.count(func.distinct(ai_calls.c.request_id)))
                .where(ai_calls.c.user_id == user_id, ai_calls.c.day == today())
            ).scalar() or 0)

    def route_requests_today(self) -> Dict[str, int]:
        """Requests actually sent to each route today (cache hits excluded)."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(ai_calls.c.route, func.count())
                .where(ai_calls.c.day == today(), ai_calls.c.status != "cache")
                .group_by(ai_calls.c.route)
            ).all()
        return {route: int(count) for route, count in rows}

    def spend_this_month(self) -> float:
        start = today()[:8] + "01"
        with self.engine.connect() as connection:
            return float(connection.execute(
                select(func.coalesce(func.sum(ai_calls.c.cost_usd), 0.0)).where(ai_calls.c.day >= start)
            ).scalar() or 0.0)

    def calls(self, days: int = 30, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=max(days, 1) - 1)).strftime("%Y-%m-%d")
        query = select(ai_calls).where(ai_calls.c.day >= since).order_by(ai_calls.c.ts)
        if user_id:
            query = query.where(ai_calls.c.user_id == user_id)
        with self.engine.connect() as connection:
            return [dict(row._mapping) for row in connection.execute(query)]

    # ----------------------------------------------------------- cache
    def cache_get(self, key: str, ttl_seconds: float) -> Optional[str]:
        with self.engine.connect() as connection:
            row = connection.execute(select(ai_cache).where(ai_cache.c.key == key)).first()
        if row is None:
            return None
        if time.time() - row.created > ttl_seconds:
            with self._lock, self.engine.begin() as connection:
                connection.execute(delete(ai_cache).where(ai_cache.c.key == key))
            return None
        with self._lock, self.engine.begin() as connection:
            connection.execute(update(ai_cache).where(ai_cache.c.key == key).values(hits=ai_cache.c.hits + 1))
        return row.response

    def cache_put(self, key: str, job: str, route: str, response: str) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(delete(ai_cache).where(ai_cache.c.key == key))
            connection.execute(insert(ai_cache).values(key=key, created=time.time(), job=job, route=route,
                                                       response=response, hits=0))

    def cache_clear(self) -> int:
        with self._lock, self.engine.begin() as connection:
            return connection.execute(delete(ai_cache)).rowcount or 0


_store: Optional[UsageStore] = None
_store_lock = threading.Lock()


def get_store() -> UsageStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = UsageStore()
        return _store


def set_store(store: Optional[UsageStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store
