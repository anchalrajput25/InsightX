# backend/llmops/store.py
#
# LLMOps data: one row per answered question ("answer checks": trust,
# hallucination findings, the judge's verdict, feedback) and a small
# key/value table for settings (routing mode, quality gate, SLO targets).
#
# Same storage rule as the other stores: logs/llmops.db locally, the
# shared database when INSIGHTX_APP_DB_URL is set, or
# INSIGHTX_LLMOPS_DB_URL. Each person sees only their own answers.
#
# Stored per answer: question, final SQL, answer text, trust, check
# results, and a small preview of the result (INSIGHTX_LLMOPS_KEEP_ROWS
# rows, default 20, 0 to keep none) so an answer can be re-checked by
# the AI judge later. Never stored: prompts, passwords, full results.

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, Text, delete, func, insert, select, update
from sqlalchemy.engine import Engine

from backend import app_db

KEEP_DAYS = float(os.getenv("INSIGHTX_LLMOPS_DAYS", "90"))
KEEP_PER_USER = int(os.getenv("INSIGHTX_LLMOPS_MAX", "2000"))

metadata = MetaData()

answers = Table(
    "insightx_answer_checks", metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", String(200), nullable=False),
    Column("ts", Float, nullable=False),
    Column("day", String(10), nullable=False),
    Column("trace_id", String(32), nullable=False, default=""),
    Column("thread_id", String(100), nullable=False, default=""),
    Column("question", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("sql", Text, nullable=False, default=""),
    Column("answer", Text, nullable=False, default=""),
    Column("row_count", Integer),
    Column("seconds", Float),
    Column("trust_level", String(10), nullable=False, default=""),
    Column("trust_score", Integer),
    Column("difficulty", String(10), nullable=False, default=""),
    Column("faithfulness", Float),          # 0..1, deterministic grounding score
    Column("risk", String(10), nullable=False, default="none"),  # none | low | medium | high
    Column("hallucinated", Integer, nullable=False, default=0),
    Column("checks", Text, nullable=False, default="{}"),       # findings JSON
    Column("preview", Text, nullable=False, default="{}"),      # columns + a few rows
    Column("judge", Text, nullable=False, default="{}"),        # AI judge verdict JSON
    Column("rating", Integer, nullable=False, default=0),       # 1 up, -1 down, 0 none
    Column("note", Text, nullable=False, default=""),
    Column("review", String(20), nullable=False, default="open"),  # open | fixed | dismissed | promoted
    Column("fixed_sql", Text, nullable=False, default=""),
    Index("ix_insightx_answer_checks_user_ts", "user_id", "ts"),
    Index("ix_insightx_answer_checks_trace", "trace_id"),
)

settings = Table(
    "insightx_llmops_settings", metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated", Float, nullable=False),
)

JSON_FIELDS = ("checks", "preview", "judge")


def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    for name in JSON_FIELDS:
        if name in item:
            try:
                item[name] = json.loads(item[name] or "{}")
            except ValueError:
                item[name] = {}
    return item


class LLMOpsStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_LLMOPS_DB_URL", "llmops.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    # ------------------------------------------------------- answers
    def add_answer(self, row: Dict[str, Any]) -> str:
        now = float(row.get("ts") or time.time())
        values = {
            "id": row.get("id") or uuid.uuid4().hex,
            "ts": now,
            "day": time.strftime("%Y-%m-%d", time.gmtime(now)),
            **{key: value for key, value in row.items() if key not in ("id", "ts", "day")},
        }
        for name in JSON_FIELDS:
            if name in values and not isinstance(values[name], str):
                values[name] = json.dumps(values[name], default=str)
        with self._lock, self.engine.begin() as connection:
            connection.execute(insert(answers).values(**values))
            connection.execute(delete(answers).where(answers.c.ts < time.time() - KEEP_DAYS * 86400))
            old = connection.execute(
                select(answers.c.id).where(answers.c.user_id == values["user_id"])
                .order_by(answers.c.ts.desc()).offset(KEEP_PER_USER)
            ).scalars().all()
            if old:
                connection.execute(delete(answers).where(answers.c.id.in_(old)))
        return values["id"]

    def list_answers(self, user_id: str, since: float = 0.0, limit: int = 200,
                     only: str = "all", search: str = "") -> List[Dict[str, Any]]:
        query = select(answers).where(answers.c.user_id == user_id, answers.c.ts >= since)
        if only == "flagged":
            query = query.where(answers.c.risk.in_(("medium", "high")))
        elif only == "hallucinated":
            query = query.where(answers.c.hallucinated == 1)
        elif only == "down":
            query = query.where(answers.c.rating < 0)
        elif only == "up":
            query = query.where(answers.c.rating > 0)
        elif only == "review":
            query = query.where(answers.c.review == "open").where(
                (answers.c.rating < 0) | answers.c.risk.in_(("medium", "high")) | (answers.c.trust_level == "low")
                | (answers.c.status != "SUCCESS"))
        if search:
            query = query.where(answers.c.question.ilike(f"%{search[:100]}%"))
        query = query.order_by(answers.c.ts.desc()).limit(max(1, min(int(limit), 1000)))
        with self.engine.connect() as connection:
            return [_decode(row._mapping) for row in connection.execute(query)]

    def get_answer(self, user_id: str, answer_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(answers).where(answers.c.user_id == user_id,
                                      (answers.c.id == answer_id) | (answers.c.trace_id == answer_id))
                .order_by(answers.c.ts.desc())
            ).first()
        return _decode(row._mapping) if row is not None else None

    def update_answer(self, user_id: str, answer_id: str, **values: Any) -> bool:
        for name in JSON_FIELDS:
            if name in values and not isinstance(values[name], str):
                values[name] = json.dumps(values[name], default=str)
        with self._lock, self.engine.begin() as connection:
            result = connection.execute(
                update(answers).where(answers.c.user_id == user_id, answers.c.id == answer_id).values(**values))
        return bool(result.rowcount)

    def counts(self, user_id: str) -> Dict[str, int]:
        with self.engine.connect() as connection:
            total = connection.execute(select(func.count()).where(answers.c.user_id == user_id)).scalar() or 0
        return {"answers": int(total)}

    # ------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.engine.connect() as connection:
            value = connection.execute(select(settings.c.value).where(settings.c.key == key)).scalar()
        if value is None:
            return default
        try:
            return json.loads(value)
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(delete(settings).where(settings.c.key == key))
            connection.execute(insert(settings).values(key=key, value=json.dumps(value, default=str), updated=time.time()))


_store: Optional[LLMOpsStore] = None
_store_lock = threading.Lock()


def get_store() -> LLMOpsStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = LLMOpsStore()
        return _store


def set_store(store: Optional[LLMOpsStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store
