# Users and chat ownership. SQLite file in logs/ by default; set
# INSIGHTX_AUTH_DB_URL (e.g. PostgreSQL) when several backend servers
# must share accounts.

import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, func, insert, select,
                        update)
from sqlalchemy.engine import Engine

from backend import app_db
from sqlalchemy.exc import IntegrityError

BASE_DIR = Path(__file__).resolve().parents[2]

metadata = MetaData()

users = Table(
    "insightx_users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("name", String(120), nullable=False, default=""),
    Column("password_hash", String(200), nullable=False),
    # bumped on password change / sign-out everywhere: older tokens stop working
    Column("session_version", Integer, nullable=False, default=1),
    Column("created_at", Float, nullable=False),
    Column("last_login_at", Float, nullable=True),
)

chat_owners = Table(
    "insightx_chat_owners", metadata,
    Column("thread_id", String(120), primary_key=True),
    Column("user_id", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
)

PUBLIC_FIELDS = ("id", "email", "name", "created_at", "last_login_at")


class EmailTaken(ValueError):
    pass


def public(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return None if row is None else {key: row[key] for key in PUBLIC_FIELDS}


class UserStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_AUTH_DB_URL", "auth.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    # ------------------------------------------------------------ users
    def count(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(select(func.count()).select_from(users)).scalar() or 0)

    def create(self, email: str, name: str, password_hash: str) -> Dict[str, Any]:
        values = {"email": email, "name": name, "password_hash": password_hash,
                  "session_version": 1, "created_at": time.time()}
        try:
            with self._lock, self.engine.begin() as connection:
                connection.execute(insert(users).values(**values))
        except IntegrityError as exc:
            raise EmailTaken(email) from exc
        return self.by_email(email)

    def _one(self, condition) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as connection:
            row = connection.execute(select(users).where(condition)).mappings().first()
        return dict(row) if row else None

    def by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return self._one(users.c.email == email)

    def by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self._one(users.c.id == user_id)

    def change(self, user_id: int, end_sessions: bool = False, **values: Any) -> Optional[Dict[str, Any]]:
        with self._lock, self.engine.begin() as connection:
            statement = update(users).where(users.c.id == user_id)
            if end_sessions:
                statement = statement.values(session_version=users.c.session_version + 1)
            if values:
                statement = statement.values(**values)
            connection.execute(statement)
        return self.by_id(user_id)

    # ------------------------------------------------------------ chats
    def chat_owner(self, thread_id: str) -> Optional[int]:
        with self.engine.connect() as connection:
            return connection.execute(
                select(chat_owners.c.user_id).where(chat_owners.c.thread_id == thread_id)).scalar()

    def claim_chat(self, thread_id: str, user_id: int) -> bool:
        """Record the owner of a new chat. False if someone else owns it."""
        owner = self.chat_owner(thread_id)
        if owner is not None:
            return owner == user_id
        try:
            with self._lock, self.engine.begin() as connection:
                connection.execute(insert(chat_owners).values(
                    thread_id=thread_id, user_id=user_id, created_at=time.time()))
            return True
        except IntegrityError:  # claimed at the same moment by another request
            return self.chat_owner(thread_id) == user_id


_store: Optional[UserStore] = None
_store_lock = threading.Lock()


def get_user_store() -> UserStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = UserStore()
        return _store


def set_user_store(store: Optional[UserStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store
