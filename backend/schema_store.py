# backend/schema_store.py
#
# Saved database profiles (tables, columns, relationships, sample values),
# so a server restart does not mean profiling the whole database again
# before the next question. Profiling scans every table (COUNT, DISTINCT,
# top values) and took 108 s on a 23-table database on Neon; the profile
# itself rarely changes.
#
#   key       sha256 of the database URL WITHOUT its password; the
#             password is never stored, not even hashed
#   fresh     a saved profile is used straight away; when it is older than
#             a day (or the person reconnects on purpose) it is refreshed
#             in the background, so new tables still show up
#   failure   the store is a speed-up only: if it cannot be read or
#             written (app database down), everything works as before
#
# Saved in INSIGHTX_SCHEMA_STORE_URL, else INSIGHTX_APP_DB_URL (Neon on
# Render), else logs/schema_store.db.

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from sqlalchemy import Column, Float, MetaData, String, Table, Text, delete, insert, select, update
from sqlalchemy.engine import Engine

from backend import app_db

logger = logging.getLogger("insightx.schema_store")

MAX_AGE_SECONDS = 24 * 3600

metadata = MetaData()
profiles = Table(
    "insightx_schema_profiles", metadata,
    Column("key", String(64), primary_key=True),
    Column("profile", Text, nullable=False),  # JSON
    Column("saved_at", Float, nullable=False),
)


def profile_key(engine: Engine) -> str:
    """The database, without its password."""
    return hashlib.sha256(engine.url.render_as_string(hide_password=True).encode()).hexdigest()


class SchemaStore:
    def __init__(self, url: Optional[str] = None):
        self.engine: Engine = app_db.store_engine(url, "INSIGHTX_SCHEMA_STORE_URL", "schema_store.db")
        self._lock = threading.Lock()
        metadata.create_all(self.engine)

    def load(self, key: str) -> Optional[Tuple[Dict[str, Any], float]]:
        """(profile, age in seconds), or None."""
        with self.engine.connect() as connection:
            row = connection.execute(select(profiles.c.profile, profiles.c.saved_at).where(profiles.c.key == key)).first()
        if row is None:
            return None
        return json.loads(row.profile), max(0.0, time.time() - row.saved_at)

    def save(self, key: str, profile: Dict[str, Any]) -> None:
        values = {"profile": json.dumps(profile, default=str), "saved_at": time.time()}
        with self._lock, self.engine.begin() as connection:
            changed = connection.execute(update(profiles).where(profiles.c.key == key).values(**values))
            if not changed.rowcount:
                connection.execute(insert(profiles).values(key=key, **values))

    def forget(self, key: str) -> None:
        with self._lock, self.engine.begin() as connection:
            connection.execute(delete(profiles).where(profiles.c.key == key))


_store: Optional[SchemaStore] = None
_store_lock = threading.Lock()


def get_store() -> SchemaStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SchemaStore()
        return _store


def set_store(store: Optional[SchemaStore]) -> None:
    """Tests use this to swap in a temporary store."""
    global _store
    with _store_lock:
        _store = store


# ------------------------------------------------------------ safe helpers
# The store only ever speeds things up: every error is logged and ignored.

def load(engine: Engine) -> Optional[Tuple[Dict[str, Any], float]]:
    try:
        return get_store().load(profile_key(engine))
    except Exception:
        logger.warning("Saved schema profile not readable; profiling instead", exc_info=True)
        return None


def save(engine: Engine, profile: Dict[str, Any]) -> None:
    try:
        get_store().save(profile_key(engine), profile)
    except Exception:
        logger.warning("Could not save the schema profile", exc_info=True)


def forget(engine: Engine) -> None:
    try:
        get_store().forget(profile_key(engine))
    except Exception:
        logger.warning("Could not remove the saved schema profile", exc_info=True)


_refreshing: set = set()
_refreshing_lock = threading.Lock()


def refresh_in_background(engine: Engine, profile_fn: Callable[[Engine], Dict[str, Any]],
                          on_fresh: Callable[[Engine, Dict[str, Any]], Any]) -> bool:
    """Profile again in a thread, then save it and hand it to on_fresh. One refresh per database at a time."""
    key = profile_key(engine)
    with _refreshing_lock:
        if key in _refreshing:
            return False
        _refreshing.add(key)

    def work() -> None:
        from backend import dialects

        try:
            with dialects.using(engine):
                fresh = profile_fn(engine)
            save(engine, fresh)
            on_fresh(engine, fresh)
        except Exception:
            logger.warning("Background schema refresh failed; the saved profile stays in use", exc_info=True)
        finally:
            with _refreshing_lock:
                _refreshing.discard(key)

    threading.Thread(target=work, name="schema-refresh", daemon=True).start()
    return True
