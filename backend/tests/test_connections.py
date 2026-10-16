# Saved connections: a connection survives a backend restart, the
# password is stored encrypted, and only its owner can use it.

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import app_db, connections


def connect_form(engine):
    url = engine.url
    return {"host": url.host, "port": str(url.port or 5432), "database": url.database,
            "username": url.username, "password": url.password}


def test_connection_survives_a_restart(engine):
    from backend import main

    with TestClient(main.app) as client:
        connection_id = client.post("/connect-postgres", data=connect_form(engine)).json()["connection_id"]

        # The server restarts: nothing is left in memory.
        main.POSTGRES_SESSIONS.pop(connection_id).get("engine").dispose()

        session = main.get_connection_session(connection_id)
        assert session["database"] == engine.url.database and session["source_type"] == "postgresql"
        with session["engine"].connect() as connection:
            assert connection.exec_driver_sql("SELECT 1").scalar() == 1

        assert client.delete(f"/disconnect-postgres/{connection_id}").status_code == 200
        assert connections.get_store().load(connection_id) is None


def test_password_is_encrypted(engine):
    store = connections.get_store()
    store.save("c1", 7, "postgresql", "postgresql", "db.example", 5432, "shop", "anchal", "secret-pass")
    with store.engine.connect() as connection:
        stored = connection.execute(select(connections.connections.c.password)).scalar()
    assert "secret-pass" not in stored
    assert store.load("c1")["password"] == "secret-pass"


def test_changed_secret_forgets_connections(monkeypatch):
    from backend.auth import tokens

    store = connections.get_store()
    store.save("c2", None, "postgresql", "postgresql", "h", 5432, "d", "u", "p")
    monkeypatch.setattr(tokens, "_secret", b"another-secret")
    assert store.load("c2") is None


def test_someone_elses_saved_connection_is_hidden(engine):
    from fastapi import HTTPException
    import pytest

    from backend import main
    from backend.auth.deps import Caller

    url = engine.url
    connections.get_store().save("c3", 1, "postgresql", "postgresql", url.host, url.port or 5432,
                                 url.database, url.username, url.password)
    with pytest.raises(HTTPException) as error:
        main.get_connection_session("c3", Caller(user_id="other", account_id=2))
    assert error.value.status_code == 404
    main.POSTGRES_SESSIONS.pop("c3")["engine"].dispose()


def test_one_app_database_for_every_store(monkeypatch):
    monkeypatch.setenv("INSIGHTX_APP_DB_URL", "postgres://u:p@neon.example/app")
    monkeypatch.delenv("INSIGHTX_TRACE_DB_URL", raising=False)
    assert app_db.store_url(None, "INSIGHTX_TRACE_DB_URL", "traces.db") == "postgresql+psycopg2://u:p@neon.example/app"
    monkeypatch.setenv("INSIGHTX_TRACE_DB_URL", "sqlite:///x.db")
    assert app_db.store_url(None, "INSIGHTX_TRACE_DB_URL", "traces.db") == "sqlite:///x.db"
    monkeypatch.delenv("INSIGHTX_APP_DB_URL")
    monkeypatch.delenv("INSIGHTX_TRACE_DB_URL")
    assert app_db.store_url(None, "INSIGHTX_TRACE_DB_URL", "traces.db").endswith("logs/traces.db")
