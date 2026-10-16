# backend/history.py
#
# Every question a person asked, with its answer: normal questions and
# "why" investigations, from every chat and every database. Shown on
# the History page, where it can be searched and asked again.
#
# Stored: question, short answer, SQL, status, database name, chat id
# and time. Never stored: result rows or passwords.
# Kept INSIGHTX_HISTORY_DAYS (default 365) days, at most
# INSIGHTX_HISTORY_MAX (default 5000) per person.
# INSIGHTX_HISTORY=0 turns it off.

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Float, Index, MetaData, String, Table, Text, delete, func, insert, or_, select
from sqlalchemy.engine import Engine

from backend import app_db

KEEP_DAYS = float(os.getenv("INSIGHTX_HISTORY_DAYS", "365"))
KEEP_PER_OWNER = int(os.getenv("INSIGHTX_HISTORY_MAX", "5000"))
KINDS = ("question", "investigation")

metadata = MetaData()

history = Table(
    "insightx_history", metadata,
    Column("id", String(32), primary_key=True),
    Column("owner", String(120), nullable=False),
    Column("ts", Float, nullable=False),
    Column("kind", String(20), nullable=False),  # question | investigation
    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False, default=""),
    Column("sql", Text, nullable=False, default=""),
    Column("status", String(30), nullable=False),
    Column("database", String(255), nullable=False, default=""),
    Column("thread_id", String(64), nullable=False, default=""),
    Column("detail", Text, nullable=False, default="{}"),  # JSON: extra facts (headline, real reason)
    Index("ix_insightx_history_owner_ts", "owner", "ts"),
)


def enabled() -> bool:
    return os.getenv("INSIGHTX_HISTORY", "1").strip().lower() not in ("0", "false", "no", "off")


def _row(row) -> Dict[str, Any]:
    item = dict(row._mapping)
    item.pop("owner", None)
    try:
        item["detail"] = json.loads(item.get("detail") or "{}")
    except ValueError:
        item["detail"] = {}
    return item


class HistoryStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_HISTORY_DB_URL", "history.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def add(self, owner: str, kind: str, question: str, status: str, answer: str = "", sql: str = "",
            database: str = "", thread_id: str = "", detail: Optional[Dict[str, Any]] = None) -> str:
        entry_id = uuid.uuid4().hex
        now = time.time()
        row = {
            "id": entry_id, "owner": owner, "ts": now, "kind": kind if kind in KINDS else "question",
            "question": str(question or "")[:4000], "answer": str(answer or "")[:8000],
            "sql": str(sql or "")[:20000], "status": str(status or "")[:30],
            "database": str(database or "")[:255], "thread_id": str(thread_id or "")[:64],
            "detail": json.dumps(detail or {}, ensure_ascii=False, default=str)[:20000],
        }
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(history).values(**row))
            connection.execute(delete(history).where(history.c.ts < now - KEEP_DAYS * 86400))
            # keep the newest KEEP_PER_OWNER for this person
            cutoff = connection.execute(
                select(history.c.ts).where(history.c.owner == owner)
                .order_by(history.c.ts.desc()).offset(KEEP_PER_OWNER).limit(1)
            ).scalar()
            if cutoff is not None:
                connection.execute(delete(history).where(history.c.owner == owner, history.c.ts <= cutoff))
        return entry_id

    def _filters(self, owner: str, search: str = "", kind: str = "") -> List[Any]:
        conditions = [history.c.owner == owner]
        if kind in KINDS:
            conditions.append(history.c.kind == kind)
        for word in (search or "").lower().split()[:8]:
            pattern = f"%{word}%"
            conditions.append(or_(func.lower(history.c.question).like(pattern),
                                  func.lower(history.c.answer).like(pattern)))
        return conditions

    def list(self, owner: str, search: str = "", kind: str = "", limit: int = 50,
             offset: int = 0) -> Dict[str, Any]:
        conditions = self._filters(owner, search, kind)
        with self.engine.connect() as connection:
            total = connection.execute(select(func.count()).select_from(history).where(*conditions)).scalar() or 0
            rows = connection.execute(
                select(history).where(*conditions).order_by(history.c.ts.desc())
                .limit(max(1, min(int(limit), 500))).offset(max(0, int(offset)))
            ).fetchall()
            days = connection.execute(select(history.c.ts, history.c.kind).where(history.c.owner == owner)).fetchall()
        return {"items": [_row(row) for row in rows], "total": int(total),
                "activity": [{"ts": row.ts, "kind": row.kind} for row in days]}

    def delete(self, owner: str, entry_id: str) -> bool:
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(delete(history).where(history.c.owner == owner, history.c.id == entry_id))
        return bool(result.rowcount)

    def clear(self, owner: str) -> int:
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(delete(history).where(history.c.owner == owner))
        return int(result.rowcount or 0)


_store: Optional[HistoryStore] = None
_store_lock = threading.Lock()


def get_store() -> HistoryStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = HistoryStore()
        return _store


def set_store(store: Optional[HistoryStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store


def record(owner: str, kind: str, question: str, status: str, **fields: Any) -> Optional[str]:
    """Save one entry; never lets a history problem break the answer."""
    if not enabled() or not str(question or "").strip():
        return None
    try:
        return get_store().add(owner, kind, question, status, **fields)
    except Exception as exc:  # pragma: no cover - logged, not raised
        print(f"History not saved: {type(exc).__name__}: {exc}")
        return None


def investigation_summary(report: Dict[str, Any]) -> Dict[str, Any]:
    """The answer text and key facts of an investigation, for the history."""
    narrative = report.get("narrative") or {}
    headline = report.get("headline") or {}
    parts = [str(narrative.get("real_reason") or "").strip(), str(narrative.get("summary") or "").strip()]
    answer = "\n\n".join(part for part in parts if part)
    detail = {
        "metric": headline.get("metric"),
        "baseline_label": headline.get("baseline_label"), "current_label": headline.get("current_label"),
        "baseline": headline.get("baseline"), "current": headline.get("current"),
        "change_pct": headline.get("change_pct"),
        "reasons": [r.get("title") for r in report.get("reasons") or []][:4],
        "actions": [a.get("action") if isinstance(a, dict) else str(a) for a in narrative.get("actions") or []][:4],
    }
    return {"answer": answer, "detail": detail}
