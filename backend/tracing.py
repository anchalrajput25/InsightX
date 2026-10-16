# backend/tracing.py
#
# A trace is the full story of one question: every pipeline step, AI
# call and database query, with when it started and how long it took.
# It answers "why was this slow?" and "where did this go wrong?".
#
# The timeline comes from backend.telemetry (the Collector bound to the
# question). Saved per person in a SQLite file in logs/ (or the database
# in INSIGHTX_TRACE_DB_URL); each person sees only their own traces.
#
# Stored: the question, the final SQL, step names and timings, model
# names, token counts, database queries and error messages. Never
# stored: AI prompts, AI replies, database rows or passwords.
# INSIGHTX_TRACING=0 turns it off.

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, Text, delete, insert, select
from sqlalchemy.engine import Engine

from backend import app_db

BASE_DIR = Path(__file__).resolve().parents[1]
KEEP_DAYS = float(os.getenv("INSIGHTX_TRACE_DAYS", "30"))
KEEP_PER_USER = int(os.getenv("INSIGHTX_TRACE_MAX", "300"))

metadata = MetaData()

traces = Table(
    "insightx_traces", metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", String(120), nullable=False),
    Column("ts", Float, nullable=False),
    Column("kind", String(20), nullable=False),  # question | investigation
    Column("question", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("seconds", Float, nullable=False),
    Column("llm_calls", Integer, nullable=False, default=0),
    Column("tokens", Integer, nullable=False, default=0),
    Column("db_queries", Integer, nullable=False, default=0),
    Column("cache", String(20), nullable=False, default=""),
    Column("error", Text, nullable=False, default=""),
    Column("detail", Text, nullable=False),  # JSON: events, summary, sql, ...
    Index("ix_insightx_traces_user_ts", "user_id", "ts"),
)


def enabled() -> bool:
    return os.getenv("INSIGHTX_TRACING", "1").strip().lower() not in ("0", "false", "no", "off")


class TraceStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_TRACE_DB_URL", "traces.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def save(self, trace: Dict[str, Any]) -> str:
        trace_id = trace.get("id") or uuid.uuid4().hex
        row = {
            "id": trace_id,
            "user_id": trace["user_id"],
            "ts": trace.get("ts") or time.time(),
            "kind": trace.get("kind", "question"),
            "question": str(trace.get("question") or "")[:4000],
            "status": str(trace.get("status") or "")[:30],
            "seconds": float(trace.get("seconds") or 0.0),
            "llm_calls": int(trace.get("llm_calls") or 0),
            "tokens": int(trace.get("tokens") or 0),
            "db_queries": int(trace.get("db_queries") or 0),
            "cache": str(trace.get("cache") or ""),
            "error": str(trace.get("error") or "")[:2000],
            "detail": json.dumps(trace.get("detail") or {}, default=str),
        }
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(traces).values(**row))
            connection.execute(delete(traces).where(traces.c.ts < time.time() - KEEP_DAYS * 86400))
            old = connection.execute(
                select(traces.c.id).where(traces.c.user_id == row["user_id"])
                .order_by(traces.c.ts.desc()).offset(KEEP_PER_USER)
            ).scalars().all()
            if old:
                connection.execute(delete(traces).where(traces.c.id.in_(old)))
        return trace_id

    def recent(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        columns = [c for c in traces.c if c.name != "detail"]
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(*columns).where(traces.c.user_id == user_id)
                .order_by(traces.c.ts.desc()).limit(max(1, min(int(limit), 300)))
            ).all()
        return [dict(row._mapping) for row in rows]

    def get(self, user_id: str, trace_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(traces).where(traces.c.id == trace_id, traces.c.user_id == user_id)
            ).first()
        if row is None:
            return None
        trace = dict(row._mapping)
        trace["detail"] = json.loads(trace["detail"] or "{}")
        return trace


_store: Optional[TraceStore] = None
_store_lock = threading.Lock()


def get_store() -> TraceStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = TraceStore()
        return _store


def set_store(store: Optional[TraceStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store


# ------------------------------------------------------------
# Building a trace from a finished question
# ------------------------------------------------------------

def build(collector, user_id: str, question: str, kind: str = "question",
          result: Optional[Dict[str, Any]] = None, error: str = "") -> Dict[str, Any]:
    """Turn a telemetry Collector (and the pipeline result) into a trace."""
    result = result or {}
    summary = collector.summary()
    events = sorted(collector.events, key=lambda event: event["start"])
    status = str(result.get("status") or ("FAILED" if error else "UNKNOWN")).upper()
    cache = result.get("cache") or {}
    if cache.get("hit"):
        cache_text = "answer" if cache.get("answer_reused") else "sql"
    else:
        cache_text = ""
    execution = result.get("execution_result") or {}
    return {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "ts": time.time() - collector.elapsed(),
        "kind": kind,
        "question": question,
        "status": status,
        "seconds": round(collector.elapsed(), 3),
        "llm_calls": summary["llm_calls"],
        "tokens": summary["total_tokens"],
        "db_queries": sum(1 for event in events if event["kind"] == "db"),
        "cache": cache_text,
        "error": error or str(result.get("error") or ""),
        "detail": {
            "events": events,
            "summary": summary,
            "sql": result.get("generated_sql") or "",
            "rewritten_query": result.get("rewritten_query") or "",
            "error_stage": result.get("error_stage") or "",
            "row_count": execution.get("row_count"),
            "trust": {k: (result.get("trust") or {}).get(k) for k in ("level", "score")},
        },
    }


def save(collector, user_id: str, question: str, kind: str = "question",
         result: Optional[Dict[str, Any]] = None, error: str = "") -> Optional[Dict[str, Any]]:
    """Save the trace; returns a short summary for the response, or None."""
    if not enabled() or collector is None:
        return None
    try:
        trace = build(collector, user_id, question, kind, result, error)
        get_store().save(trace)
    except Exception as exc:  # tracing must never break a question
        print(f"[InsightX] Trace not saved: {type(exc).__name__}: {exc}")
        return None
    return {key: trace[key] for key in ("id", "seconds", "llm_calls", "tokens", "db_queries", "cache")}
