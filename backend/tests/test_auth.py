# Sign-up / sign-in: passwords, tokens, lockout, and that every endpoint
# is protected and only shows a person their own chats, connections and usage.

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import main
from backend.ai_gateway.usage_store import get_store
from backend.auth import passwords, service, tokens
from backend.auth.deps import current_caller
from backend.auth.store import get_user_store

PASSWORD = "good pass 123"
PUBLIC_PATHS = {"/", "/test", "/auth/status", "/auth/signup", "/auth/login"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INSIGHTX_AUTH_REQUIRED", "1")
    tokens.set_secret(b"test-secret")
    service.lockout.reset()
    yield TestClient(main.app)
    tokens.set_secret(None)
    service.lockout.reset()


def sign_up(client, email, password=PASSWORD):
    response = client.post("/auth/signup", json={"email": email, "name": "", "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_passwords_are_hashed_and_checked():
    stored = passwords.hash_password("abc12345", iterations=1000)
    assert "abc12345" not in stored and stored.startswith("pbkdf2_sha256$1000$")
    assert passwords.verify_password("abc12345", stored)
    assert not passwords.verify_password("abc12346", stored)
    assert not passwords.verify_password("abc12345", "garbage")
    assert passwords.password_problem("short1") and passwords.password_problem("onlyletters")
    assert passwords.password_problem("letters and 1") == ""


def test_sign_up_then_use_the_token(client):
    assert client.get("/auth/status").json() == {"required": True}
    headers = sign_up(client, "Asha@Example.com ")
    me = client.get("/auth/me", headers=headers).json()
    assert me["user"]["email"] == "asha@example.com" and me["user"]["name"] == "asha"
    assert "password_hash" not in me["user"] and me["questions_today"] == 0

    again = client.post("/auth/signup", json={"email": "asha@example.com", "password": PASSWORD})
    assert again.status_code == 409
    assert client.post("/auth/signup", json={"email": "nope", "password": PASSWORD}).status_code == 400
    assert client.post("/auth/signup", json={"email": "b@x.io", "password": "12345678"}).status_code == 400


def test_sign_in_and_lockout(client):
    sign_up(client, "ravi@example.com")
    ok = client.post("/auth/login", json={"email": "RAVI@example.com", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["user"]["last_login_at"]
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert unknown.status_code == 401 and unknown.json()["detail"] == "Wrong email or password."
    for _ in range(service.MAX_FAILURES):
        assert client.post("/auth/login", json={"email": "ravi@example.com", "password": "wrong"}).status_code == 401
    locked = client.post("/auth/login", json={"email": "ravi@example.com", "password": PASSWORD})
    assert locked.status_code == 429 and "Too many wrong passwords" in locked.json()["detail"]


def test_bad_expired_or_old_tokens_are_refused(client):
    headers = sign_up(client, "mia@example.com")
    token = headers["Authorization"].split()[1]
    body, signature = token.split(".")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {body}.x{signature[1:]}"}).status_code == 401
    assert client.get("/auth/me").status_code == 401
    user = get_user_store().by_email("mia@example.com")
    expired = tokens.issue(user["id"], user["session_version"], now=1_000_000)["token"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    changed = client.post("/auth/password", headers=headers,
                          json={"current_password": PASSWORD, "new_password": "new pass 456"})
    assert changed.status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 401  # old session ended
    fresh = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/auth/me", headers=fresh).status_code == 200
    assert client.post("/auth/login", json={"email": "mia@example.com", "password": "new pass 456"}).status_code == 200

    client.post("/auth/logout-everywhere", headers=fresh)
    assert client.get("/auth/me", headers=fresh).status_code == 401


def _uses_current_caller(dependant) -> bool:
    return any(dep.call is current_caller or _uses_current_caller(dep) for dep in dependant.dependencies)


def test_every_endpoint_requires_sign_in():
    unprotected = [route.path for route in main.app.routes
                   if isinstance(route, APIRoute) and route.path not in PUBLIC_PATHS
                   and not _uses_current_caller(route.dependant)]
    assert unprotected == []


class FakeEngine:
    def dispose(self):
        pass


def test_people_only_see_their_own_connections_chats_and_usage(client, monkeypatch):
    asha, ravi = sign_up(client, "asha@example.com"), sign_up(client, "ravi@example.com")
    asha_id = get_user_store().by_email("asha@example.com")["id"]
    ravi_id = get_user_store().by_email("ravi@example.com")["id"]
    monkeypatch.setitem(main.POSTGRES_SESSIONS, "conn-asha", {"engine": FakeEngine(), "owner": asha_id})
    monkeypatch.setitem(main.POSTGRES_SESSIONS, "conn-ravi", {"engine": FakeEngine(), "owner": ravi_id})
    monkeypatch.setattr(main, "get_thread_state", lambda engine, thread_id: {"conversation_history": []})

    # someone else's connection looks like a missing one
    assert client.get("/threads/t1/history", params={"connection_id": "conn-asha"}, headers=ravi).status_code == 404
    assert client.delete("/disconnect-postgres/conn-asha", headers=ravi).status_code == 404
    assert "conn-asha" in main.POSTGRES_SESSIONS

    # a chat belongs to whoever used it first
    assert get_user_store().claim_chat("t1", asha_id)
    assert not get_user_store().claim_chat("t1", ravi_id)
    assert client.get("/threads/t1/history", params={"connection_id": "conn-asha"}, headers=asha).status_code == 200
    assert client.get("/threads/t1/history", params={"connection_id": "conn-ravi"}, headers=ravi).status_code == 404
    with pytest.raises(Exception) as missing:
        main._own_chat("t1", main.Caller(user_id="ravi@example.com", account_id=ravi_id))
    assert getattr(missing.value, "status_code", None) == 404

    # usage: only your own, whatever you ask for
    store = get_store()
    for user, n in (("asha@example.com", 3), ("ravi@example.com", 1)):
        for i in range(n):
            store.record(user_id=user, request_id=f"{user}-{i}", job="rewrite", tier="light",
                         route="groq/x", provider="groq", status="ok")
    usage = client.get("/ai/usage", params={"user": "asha@example.com"}, headers=ravi).json()
    assert [row["user_id"] for row in usage["by_user"]] == ["ravi@example.com"]
    assert client.get("/auth/me", headers=asha).json()["questions_today"] == 3


def test_chat_list_is_private_and_kept_by_the_backend(client):
    asha, ravi = sign_up(client, "asha.chats@example.com"), sign_up(client, "ravi.chats@example.com")
    chats = [{"thread_id": "t1", "title": "Top cities", "created_at": "2026-10-10T10:00:00",
              "updated_at": "2026-10-10T10:05:00", "extra": "dropped"}]
    saved = client.put("/chats", json={"chats": chats}, headers=asha).json()["chats"]
    assert saved == [{key: chats[0][key] for key in ("thread_id", "title", "created_at", "updated_at")}]
    assert client.get("/chats", headers=asha).json()["chats"] == saved
    assert client.get("/chats", headers=ravi).json()["chats"] == []
    assert client.get("/chats").status_code == 401
