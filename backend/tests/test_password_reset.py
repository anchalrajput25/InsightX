# Forgot password / reset link: generic answers, token storage, expiry,
# single use, rate limits, sessions ended, and no email server needed.

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import main
from backend.auth import password_reset, service, tokens
from backend.auth.store import get_user_store

PASSWORD = "good pass 123"
NEW_PASSWORD = "brand new 456"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INSIGHTX_AUTH_REQUIRED", "1")
    monkeypatch.setenv("INSIGHTX_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("INSIGHTX_PUBLIC_URL", "https://app.example.test/")
    tokens.set_secret(b"test-secret")
    service.lockout.reset()
    password_reset.limiter.reset()
    password_reset._ready.clear()
    yield TestClient(main.app)
    tokens.set_secret(None)
    service.lockout.reset()
    password_reset.limiter.reset()


@pytest.fixture
def outbox(monkeypatch):
    """Captures emails instead of sending them."""
    sent = []
    monkeypatch.setattr(password_reset, "send_reset_email", lambda address, link: sent.append((address, link)))
    return sent


def sign_up(client, email="asha@example.com"):
    response = client.post("/auth/signup", json={"email": email, "name": "", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def token_from(link):
    assert link.startswith("https://app.example.test/reset-password?token=")
    return link.split("token=", 1)[1]


def test_unknown_and_real_email_get_the_same_answer(client, outbox):
    sign_up(client)
    unknown = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    real = client.post("/auth/forgot-password", json={"email": "Asha@Example.com "})
    bad = client.post("/auth/forgot-password", json={"email": "not an email"})
    assert unknown.status_code == real.status_code == bad.status_code == 200
    assert unknown.json() == real.json() == bad.json() == {"message": password_reset.GENERIC_REPLY}
    assert [address for address, _ in outbox] == ["asha@example.com"]


def test_only_the_token_hash_is_stored(client, outbox):
    sign_up(client)
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    token = token_from(outbox[0][1])
    assert len(token) >= 40
    with get_user_store().engine.connect() as connection:
        rows = connection.execute(select(password_reset.reset_tokens)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["token_hash"] != token and token not in str(dict(rows[0]))
    assert rows[0]["token_hash"] == password_reset._hash(token)
    assert rows[0]["expires_at"] - rows[0]["created_at"] == password_reset.TOKEN_TTL_S


def test_reset_changes_password_and_signs_out_everywhere(client, outbox):
    old_session = sign_up(client)
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    token = token_from(outbox[0][1])

    response = client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD})
    assert response.status_code == 200, response.text
    assert NEW_PASSWORD not in response.text and token not in response.text

    assert client.get("/auth/me", headers=old_session).status_code == 401
    assert client.post("/auth/login", json={"email": "asha@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": "asha@example.com", "password": NEW_PASSWORD}).status_code == 200


def test_token_works_only_once(client, outbox):
    sign_up(client)
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    token = token_from(outbox[0][1])
    assert client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200
    again = client.post("/auth/reset-password", json={"token": token, "new_password": "another 789"})
    assert again.status_code == 400 and "not valid" in again.json()["detail"]


def test_expired_and_wrong_tokens_are_refused(client, outbox):
    sign_up(client)
    password_reset.request_reset("asha@example.com", now=1_000.0)
    token = token_from(outbox[0][1])
    with pytest.raises(service.AuthError) as expired:
        password_reset.reset_password(token, NEW_PASSWORD, now=1_000.0 + password_reset.TOKEN_TTL_S + 1)
    assert expired.value.status == 400
    for wrong in ["", "x" * 43, token[:-1] + ("A" if token[-1] != "A" else "B")]:
        response = client.post("/auth/reset-password", json={"token": wrong, "new_password": NEW_PASSWORD})
        assert response.status_code == 400
    # still valid just before expiry
    password_reset.reset_password(token, NEW_PASSWORD, now=1_000.0 + password_reset.TOKEN_TTL_S - 1)


def test_weak_new_password_is_refused_and_token_stays_usable(client, outbox):
    sign_up(client)
    client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    token = token_from(outbox[0][1])
    weak = client.post("/auth/reset-password", json={"token": token, "new_password": "short"})
    assert weak.status_code == 400 and "at least" in weak.json()["detail"]
    assert client.post("/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}).status_code == 200


def test_rate_limits_per_email_and_per_address(client, outbox):
    sign_up(client)
    for _ in range(5):
        assert client.post("/auth/forgot-password", json={"email": "asha@example.com"}).status_code == 200
    assert len(outbox) == password_reset.EMAIL_LIMIT  # answers stay generic, emails stop

    password_reset.limiter.reset()
    codes = [client.post("/auth/forgot-password", json={"email": f"p{i}@example.com"}).status_code
             for i in range(password_reset.IP_LIMIT + 1)]
    assert codes[:-1] == [200] * password_reset.IP_LIMIT and codes[-1] == 429


def test_without_email_server_nothing_breaks(client, monkeypatch, caplog):
    monkeypatch.delenv("INSIGHTX_SMTP_HOST")
    monkeypatch.setenv("INSIGHTX_ENV", "production")
    called = []
    monkeypatch.setattr(password_reset, "send_reset_email", lambda *a: called.append(a))
    sign_up(client)
    with caplog.at_level(logging.WARNING, logger="insightx.auth.reset"):
        response = client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    assert response.status_code == 200 and called == []
    assert "not set up" in caplog.text and "token=" not in caplog.text


def test_local_runs_log_the_link_for_testing(client, monkeypatch, caplog):
    monkeypatch.delenv("INSIGHTX_SMTP_HOST")
    monkeypatch.delenv("INSIGHTX_ENV", raising=False)
    sign_up(client)
    with caplog.at_level(logging.WARNING, logger="insightx.auth.reset"):
        client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    assert "/reset-password?token=" in caplog.text


def test_failed_email_send_is_hidden_from_the_caller(client, monkeypatch, caplog):
    def broken(address, link):
        raise OSError("smtp down")

    monkeypatch.setattr(password_reset, "send_reset_email", broken)
    sign_up(client)
    with caplog.at_level(logging.WARNING, logger="insightx.auth.reset"):
        response = client.post("/auth/forgot-password", json={"email": "asha@example.com"})
    assert response.status_code == 200 and response.json()["message"] == password_reset.GENERIC_REPLY
    assert "token=" not in caplog.text
