# backend/connections.py
#
# Saved database connections, so a connection survives a backend
# restart (or reaches another backend server) instead of living only in
# one server's memory.
#
# The password is encrypted with a key made from INSIGHTX_AUTH_SECRET;
# if that secret changes, saved connections simply stop working and
# the person connects again. A connection unused for
# INSIGHTX_CONNECTION_DAYS (default 7) is forgotten.
# INSIGHTX_SAVE_CONNECTIONS=0 keeps connections in memory only.

import base64
import hashlib
import os
import threading
import time
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.engine import Engine

from backend import app_db

KEEP_DAYS = float(os.getenv("INSIGHTX_CONNECTION_DAYS", "7"))

metadata = MetaData()

connections = Table(
    "insightx_connections", metadata,
    Column("id", String(64), primary_key=True),
    Column("owner", Integer, nullable=True),
    Column("source_type", String(30), nullable=False),
    Column("db_type", String(20), nullable=False),
    Column("host", String(255), nullable=False),
    Column("port", Integer, nullable=False),
    Column("database", String(255), nullable=False),
    Column("username", String(255), nullable=False),
    Column("password", Text, nullable=False),  # encrypted
    Column("created", Float, nullable=False),
    Column("last_used", Float, nullable=False),
)


def enabled() -> bool:
    return os.getenv("INSIGHTX_SAVE_CONNECTIONS", "1").strip().lower() not in ("0", "false", "no", "off")


def _cipher() -> Fernet:
    from backend.auth.tokens import secret

    key = hashlib.sha256(b"insightx-connections\x00" + secret()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class ConnectionStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_CONNECTIONS_DB_URL", "connections.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def save(self, connection_id: str, owner: Optional[int], source_type: str, db_type: str,
             host: str, port: int, database: str, username: str, password: str) -> None:
        now = time.time()
        row = {
            "id": connection_id, "owner": owner, "source_type": source_type, "db_type": db_type,
            "host": host, "port": int(port), "database": database, "username": username,
            "password": _cipher().encrypt(password.encode("utf-8")).decode("ascii"),
            "created": now, "last_used": now,
        }
        with self._lock, self.engine.begin() as connection:
            connection.execute(delete(connections).where(connections.c.last_used < now - KEEP_DAYS * 86400))
            connection.execute(insert(connections).values(**row))

    def load(self, connection_id: str) -> Optional[Dict[str, Any]]:
        """The saved connection with its password, or None."""
        now = time.time()
        with self.engine.connect() as connection:
            row = connection.execute(select(connections).where(connections.c.id == connection_id)).first()
        if row is None or row.last_used < now - KEEP_DAYS * 86400:
            return None
        saved = dict(row._mapping)
        try:
            saved["password"] = _cipher().decrypt(saved["password"].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None  # the secret changed
        with self._lock, self.engine.begin() as connection:
            connection.execute(update(connections).where(connections.c.id == connection_id).values(last_used=now))
        return saved

    def forget(self, connection_id: str) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(delete(connections).where(connections.c.id == connection_id))


_store: Optional[ConnectionStore] = None
_store_lock = threading.Lock()


def get_store() -> ConnectionStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ConnectionStore()
        return _store


def set_store(store: Optional[ConnectionStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store
