# backend/tests/test_isolation.py
#
# Several people, several databases, at the same time: each person only
# ever reaches their own connection, parallel questions never mix data,
# and people connecting the same database share one connection pool
# instead of opening one pool each.

import sqlite3
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.auth import service, tokens
from backend.database import create_database_engine, forget_engine

PASSWORD = "good pass 123"
SPEC = {"metric_name": "sales", "metric_expression": "SUM(sales.amount)", "fact_table": "sales",
        "levers": [{"type": "scale", "column": "sales.amount", "percent": 0}]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INSIGHTX_AUTH_REQUIRED", "1")
    tokens.set_secret(b"test-secret")
    service.lockout.reset()
    yield TestClient(main.app)
    tokens.set_secret(None)
    service.lockout.reset()


def sign_up(client, email):
    response = client.post("/auth/signup", json={"email": email, "name": "", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def make_db(path, amounts, extra_table):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT, amount REAL)")
        connection.executemany("INSERT INTO sales (region, amount) VALUES (?, ?)",
                               [("north", amount) for amount in amounts])
        connection.execute(f"CREATE TABLE {extra_table} (id INTEGER PRIMARY KEY)")
    return path


def connect(client, headers, path):
    with open(path, "rb") as handle:
        response = client.post("/connect-sqlite", headers=headers, files={"file": (path.name, handle)})
    assert response.status_code == 200, response.text
    return response.json()["connection_id"]


def test_two_people_only_ever_see_their_own_database(client, tmp_path):
    asha, ravi = sign_up(client, f"asha-{uuid.uuid4().hex[:6]}@example.com"), sign_up(client, f"ravi-{uuid.uuid4().hex[:6]}@example.com")
    asha_db = connect(client, asha, make_db(tmp_path / "asha.sqlite", [10, 20, 30], "asha_only"))
    ravi_db = connect(client, ravi, make_db(tmp_path / "ravi.sqlite", [1000, 2000], "ravi_only"))

    # Both ask many times at once; every answer must be their own database's.
    answers, failures = [], []

    def ask(headers, connection_id, expected):
        for _ in range(8):
            response = client.post("/whatif", headers=headers, json={"connection_id": connection_id, "spec": SPEC})
            if response.status_code != 200:
                failures.append(response.text)
            else:
                answers.append((expected, response.json()["baseline"]))

    threads = [threading.Thread(target=ask, args=args)
               for args in [(asha, asha_db, 60.0), (ravi, ravi_db, 3000.0)] * 3]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == [] and len(answers) == 48
    assert all(expected == baseline for expected, baseline in answers)

    overview = client.get("/dashboard/overview", headers=asha, params={"connection_id": asha_db})
    assert overview.status_code == 200
    assert "asha_only" in overview.text and "ravi_only" not in overview.text


def test_someone_elses_connection_id_is_not_found(client, tmp_path):
    asha, ravi = sign_up(client, f"asha-{uuid.uuid4().hex[:6]}@example.com"), sign_up(client, f"ravi-{uuid.uuid4().hex[:6]}@example.com")
    asha_db = connect(client, asha, make_db(tmp_path / "a.sqlite", [5], "secret_table"))
    attempts = {
        "query": client.post("/query", headers=ravi, json={"connection_id": asha_db, "question": "list tables"}),
        "investigate": client.post("/investigate", headers=ravi, json={"connection_id": asha_db, "question": "why?"}),
        "whatif": client.post("/whatif", headers=ravi, json={"connection_id": asha_db, "spec": SPEC}),
        "sweep": client.post("/whatif/sweep", headers=ravi, json={"connection_id": asha_db, "spec": SPEC}),
        "forecast": client.post("/forecast", headers=ravi, json={"connection_id": asha_db, "spec": SPEC}),
        "dashboard": client.get("/dashboard/overview", headers=ravi, params={"connection_id": asha_db}),
        "history": client.get("/threads/t1/history", headers=ravi, params={"connection_id": asha_db}),
        "disconnect": client.delete(f"/disconnect-postgres/{asha_db}", headers=ravi),
    }
    leaked = {name: response.status_code for name, response in attempts.items() if response.status_code != 404}
    assert leaked == {}
    for response in attempts.values():
        assert "secret_table" not in response.text
    # Asha's connection still works after Ravi's attempts.
    assert client.post("/whatif", headers=asha, json={"connection_id": asha_db, "spec": SPEC}).json()["baseline"] == 5


def test_twenty_people_connecting_at_once_all_succeed(client, tmp_path):
    path = make_db(tmp_path / "shared.sqlite", [1, 2, 3], "t")
    people = [sign_up(client, f"p{i}-{uuid.uuid4().hex[:6]}@example.com") for i in range(20)]
    results = []

    def go(headers):
        connection_id = connect(client, headers, path)
        response = client.post("/whatif", headers=headers, json={"connection_id": connection_id, "spec": SPEC})
        results.append((response.status_code, response.json().get("baseline")))

    threads = [threading.Thread(target=go, args=(headers,)) for headers in people]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [(200, 6.0)] * 20


def test_people_on_the_same_server_database_share_one_pool():
    params = dict(host="db.example.com", port=5432, database="shop", username="reader")
    engines = [create_database_engine("postgresql", password="pw", **params) for _ in range(20)]
    assert all(engine is engines[0] for engine in engines)     # 20 people, one pool (at most 15 connections)
    other_password = create_database_engine("postgresql", password="other", **params)
    other_user = create_database_engine("postgresql", password="pw", **{**params, "username": "admin"})
    shared = create_database_engine("postgresql", password="pw", **params)
    assert other_password is not shared and other_user is not shared
    forget_engine(other_password)                              # a failed login is not kept
    assert create_database_engine("postgresql", password="other", **params) is not other_password
    shared.dispose()                                           # one person disconnects ...
    assert create_database_engine("postgresql", password="pw", **params) is shared  # ... the others keep it
