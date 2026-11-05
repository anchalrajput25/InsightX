# backend/tests/test_schema_store.py
#
# The database profile survives a restart: the first question after a
# restart uses the saved profile instead of scanning every table again,
# an old profile is refreshed in the background, a broken store changes
# nothing, and the password never reaches the store.

import sqlite3
import time
import uuid

import pytest
from sqlalchemy import create_engine, select

from backend import dialects, schema_profiler, schema_store
from backend.database import create_sqlite_engine
from backend.text_to_sql import schema_context
from backend.text_to_sql.schema_context import cache_schema_from_profile, get_agent_schema, invalidate_schema_cache


@pytest.fixture
def db(tmp_path):
    path = tmp_path / f"shop_{uuid.uuid4().hex}.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE customers (id INTEGER PRIMARY KEY, city TEXT);
            CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), total REAL, status TEXT);
        """)
        connection.executemany("INSERT INTO customers VALUES (?, ?)", [(1, "Pune"), (2, "Delhi")])
        connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", [(1, 1, 10.5, "paid"), (2, 2, 20.0, "late")])
    engine = create_sqlite_engine(str(path))
    yield engine
    engine.dispose()


def schema(engine):
    with dialects.using(engine):
        return get_agent_schema(engine)


def restart():
    """What a server restart does to the in-memory cache."""
    with schema_context._CACHE_LOCK:
        schema_context._SCHEMA_CACHE.clear()


def count_profiling(monkeypatch):
    real, calls = schema_profiler.get_database_schema, []

    def counted(engine, *args, **kwargs):
        calls.append(1)
        return real(engine, *args, **kwargs)

    monkeypatch.setattr(schema_profiler, "get_database_schema", counted)
    return calls


def test_first_question_after_a_restart_uses_the_saved_profile(db, monkeypatch):
    calls = count_profiling(monkeypatch)
    started = time.perf_counter()
    before = schema(db)
    cold = time.perf_counter() - started
    assert calls == [1] and schema_store.load(db) is not None

    restart()
    started = time.perf_counter()
    after = schema(db)
    warm = time.perf_counter() - started
    assert calls == [1]                                        # no second scan of every table
    assert after == before                                     # identical agent schema from the saved JSON
    print(f"profile on first use: {cold * 1000:.0f} ms, after restart from the store: {warm * 1000:.0f} ms")


def test_saved_profile_round_trips_to_the_same_agent_schema(db):
    with dialects.using(db):
        profile = schema_profiler.get_database_schema(db)
        schema_store.save(db, profile)
        loaded, age = schema_store.load(db)
        assert age < 60
        assert cache_schema_from_profile(db, loaded) == cache_schema_from_profile(db, profile)


def test_a_broken_store_falls_back_to_profiling(db, monkeypatch):
    class Broken:
        def load(self, key):
            raise RuntimeError("app database is down")

        save = forget = load

    schema_store.set_store(Broken())
    calls = count_profiling(monkeypatch)
    assert {t["table_name"] for t in schema(db)["tables"]} == {"customers", "orders"}
    restart()
    schema(db)
    assert calls == [1, 1]                                     # works as before, just not faster
    invalidate_schema_cache(db)                                # never raises either


def test_no_password_in_the_key_or_the_store(tmp_path):
    with_password = create_engine("postgresql+psycopg2://reader:s3cret-pass@db.example.com:5432/shop")
    other_password = create_engine("postgresql+psycopg2://reader:different@db.example.com:5432/shop")
    key = schema_store.profile_key(with_password)
    assert key == schema_store.profile_key(other_password)    # derived from the URL without its password
    schema_store.save(with_password, {"tables": [], "relationships": []})
    store = schema_store.get_store()
    with store.engine.connect() as connection:
        rows = [tuple(row) for row in connection.execute(select(schema_store.profiles))]
    assert len(rows) == 1 and "s3cret" not in repr(rows)


def test_invalidating_forgets_the_saved_profile(db, monkeypatch):
    schema(db)
    assert schema_store.load(db) is not None
    invalidate_schema_cache(db)                                # e.g. after importing files into this database
    assert schema_store.load(db) is None
    calls = count_profiling(monkeypatch)
    schema(db)
    assert calls == [1]


def test_an_old_profile_is_used_then_refreshed_in_the_background(db, monkeypatch):
    schema(db)
    store = schema_store.get_store()
    with store.engine.begin() as connection:                  # pretend it was saved two days ago
        connection.execute(schema_store.profiles.update().values(saved_at=time.time() - 2 * 24 * 3600))
    with sqlite3.connect(db.url.database) as connection:       # a table added since then
        connection.execute("CREATE TABLE returns (id INTEGER PRIMARY KEY, order_id INTEGER)")
    db.dispose()

    restart()
    names = {t["table_name"] for t in schema(db)["tables"]}
    assert "returns" not in names                             # answered at once from the saved profile
    deadline = time.time() + 10
    while time.time() < deadline and "returns" not in {t["table_name"] for t in schema(db)["tables"]}:
        time.sleep(0.1)
    assert "returns" in {t["table_name"] for t in schema(db)["tables"]}
    assert schema_store.load(db)[1] < 60                      # and saved again


def test_connect_uses_the_saved_profile(db, monkeypatch):
    from backend import main

    schema(db)                                                 # profiled and saved once
    restart()
    monkeypatch.setattr(main, "get_database_schema", lambda engine: (_ for _ in ()).throw(AssertionError("scan")))
    monkeypatch.setattr(main, "warm_retrieval_index", lambda agent_schema: None)
    monkeypatch.setattr(schema_store, "refresh_in_background", lambda *args: True)
    with dialects.using(db):
        profile = main.profile_database(db)
    assert {t["table_name"] for t in profile["tables"]} == {"customers", "orders"}
