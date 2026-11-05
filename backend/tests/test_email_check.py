# /auth/email-check: reports settings as yes/no, names the failing step,
# never shows the password, only mails the server's own sender, and is rate-limited.

import smtplib

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.auth import email_check

SECRET = "xsmtpsib-abc123-SECRET"


@pytest.fixture
def client(monkeypatch):
    email_check.reset_limit()
    for name, value in {"INSIGHTX_SMTP_HOST": "smtp.example.test", "INSIGHTX_SMTP_PORT": "2525",
                        "INSIGHTX_SMTP_USER": "login@smtp.example.test", "INSIGHTX_SMTP_PASSWORD": SECRET,
                        "INSIGHTX_SMTP_FROM": "InsightX <owner@example.test>"}.items():
        monkeypatch.setenv(name, value)
    yield TestClient(main.app)
    email_check.reset_limit()


class FakeSMTP:
    sent = []
    fail_login = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, f"Authentication failed for {password}".encode())

    def send_message(self, message):
        FakeSMTP.sent.append(message["To"])


def test_all_good_and_test_mail_goes_only_to_the_sender(client, monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail_login = [], False
    monkeypatch.setattr(email_check.smtplib, "SMTP", FakeSMTP)
    body = client.get("/auth/email-check?send=1").json()
    assert body["connect"] == "ok" and body["login"] == "ok" and body["port"] == 2525
    assert body["test_email"].startswith("accepted")
    assert all(body["settings_present"][name] for name in ("INSIGHTX_SMTP_HOST", "INSIGHTX_SMTP_PASSWORD"))
    assert FakeSMTP.sent == ["owner@example.test"]
    assert SECRET not in str(body)


def test_login_failure_is_named_without_the_password(client, monkeypatch):
    FakeSMTP.fail_login = True
    monkeypatch.setattr(email_check.smtplib, "SMTP", FakeSMTP)
    body = client.get("/auth/email-check").json()
    FakeSMTP.fail_login = False
    assert body["connect"] == "ok" and body["login"].startswith("SMTPAuthenticationError")
    assert SECRET not in str(body) and "xsmtpsib" not in str(body)


def test_blocked_port_shows_as_connect_error(client, monkeypatch):
    def blocked(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(email_check.smtplib, "SMTP", blocked)
    body = client.get("/auth/email-check").json()
    assert body["connect"].startswith("TimeoutError") and body["login"] == "not tried"


def test_missing_host_and_rate_limit(client, monkeypatch):
    monkeypatch.delenv("INSIGHTX_SMTP_HOST")
    body = client.get("/auth/email-check").json()
    assert body["settings_present"]["INSIGHTX_SMTP_HOST"] is False and "empty" in body["connect"]
    codes = [client.get("/auth/email-check").status_code for _ in range(email_check.MAX_CHECKS_PER_HOUR)]
    assert codes[-1] == 429
