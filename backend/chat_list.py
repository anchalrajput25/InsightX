# backend/chat_list.py
#
# Each person's list of chats (id, title, dates), kept by the backend
# so it is the same on every device and is not lost when the app's
# server restarts. The conversations themselves are kept by the
# LangGraph checkpointer.

import json
import threading
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Float, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.engine import Engine

from backend import app_db

MAX_CHATS = 500
FIELDS = ("thread_id", "title", "created_at", "updated_at")

metadata = MetaData()

chat_lists = Table(
    "insightx_chat_lists", metadata,
    Column("owner", String(120), primary_key=True),
    Column("chats", Text, nullable=False),  # JSON list
    Column("updated", Float, nullable=False),
)


def clean(chats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only the known fields, with short strings."""
    result = []
    for chat in chats[:MAX_CHATS]:
        if not isinstance(chat, dict) or not str(chat.get("thread_id") or "").strip():
            continue
        result.append({key: str(chat.get(key) or "")[:200] for key in FIELDS})
    return result


class ChatListStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_CHATS_DB_URL", "chats.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def get(self, owner: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as connection:
            raw = connection.execute(select(chat_lists.c.chats).where(chat_lists.c.owner == owner)).scalar()
        return json.loads(raw) if raw else []

    def put(self, owner: str, chats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        chats = clean(chats)
        values = {"chats": json.dumps(chats, ensure_ascii=False), "updated": time.time()}
        with self._lock, self.engine.begin() as connection:
            changed = connection.execute(update(chat_lists).where(chat_lists.c.owner == owner).values(**values))
            if not changed.rowcount:
                connection.execute(insert(chat_lists).values(owner=owner, **values))
        return chats


_store: Optional[ChatListStore] = None
_store_lock = threading.Lock()


def get_store() -> ChatListStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ChatListStore()
        return _store


def set_store(store: Optional[ChatListStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store
