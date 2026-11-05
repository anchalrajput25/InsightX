# Forgotten passwords: "email me a link", then "set a new password".
#
#   POST /auth/forgot-password {email}
#       Always the same answer, so nobody can find out which emails have
#       an account. For a real account a random link is emailed; only the
#       SHA-256 of its token is stored, it works once, for 30 minutes.
#   POST /auth/reset-password {token, new_password}
#       Same password rules and hashing as sign-up. Signs out every
#       session of that account.
#
# Email goes over SMTP, set on the backend server (free with a Gmail app
# password or Brevo):
#   INSIGHTX_SMTP_HOST, INSIGHTX_SMTP_PORT (587), INSIGHTX_SMTP_USER,
#   INSIGHTX_SMTP_PASSWORD, INSIGHTX_SMTP_FROM (or INSIGHTX_MAIL_FROM)
#   INSIGHTX_PUBLIC_URL   the website address used in the link
# Without SMTP nothing breaks: a warning is logged. Outside production
# (INSIGHTX_ENV != "production") the link is also logged, for local tests.

import hashlib
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.engine import Engine

from backend.auth import passwords, service
from backend.auth.store import get_user_store

log = logging.getLogger("insightx.auth.reset")

TOKEN_TTL_S = 30 * 60
EMAIL_LIMIT = 3            # links per email per hour
IP_LIMIT = 10              # requests per address per hour
LIMIT_WINDOW_S = 60 * 60
GENERIC_REPLY = "If that email has an account, we sent a link to reset the password. It works for 30 minutes."

metadata = MetaData()
reset_tokens = Table(
    "insightx_password_resets", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("user_id", Integer, nullable=False, index=True),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("used_at", Float, nullable=True),
)

_ready: Dict[int, bool] = {}
_ready_lock = threading.Lock()


def _engine() -> Engine:
    """The accounts database (Neon on Render), with the reset table created once."""
    engine = get_user_store().engine
    with _ready_lock:
        if not _ready.get(id(engine)):
            metadata.create_all(engine)
            _ready[id(engine)] = True
    return engine


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ rate limits
class _Limiter:
    def __init__(self):
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, limit: int, now: float) -> bool:
        with self.lock:
            recent = self.hits[key]
            while recent and recent[0] < now - LIMIT_WINDOW_S:
                recent.popleft()
            if len(recent) >= limit:
                return False
            recent.append(now)
            return True

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()


limiter = _Limiter()


# ------------------------------------------------------------ email
def _production() -> bool:
    return os.getenv("INSIGHTX_ENV", "").strip().lower() in ("production", "prod")


def reset_link(token: str) -> str:
    base = (os.getenv("INSIGHTX_PUBLIC_URL") or "http://localhost:3000").strip().rstrip("/")
    return f"{base}/reset-password?token={token}"


def send_reset_email(address: str, link: str) -> None:
    """Separate function so tests can replace it. Raises when sending fails."""
    from backend.alerting import notify

    if os.getenv("INSIGHTX_MAIL_FROM") and not os.getenv("INSIGHTX_SMTP_FROM"):
        os.environ["INSIGHTX_SMTP_FROM"] = os.environ["INSIGHTX_MAIL_FROM"]
    notify.send_email(
        address,
        "Reset your InsightX password",
        "Someone asked to reset the password for your InsightX account.\n\n"
        f"Open this link to choose a new password (it works once, for 30 minutes):\n{link}\n\n"
        "If this was not you, ignore this email. Your password stays the same.",
    )


def _deliver(address: str, token: str) -> None:
    link = reset_link(token)
    if not os.getenv("INSIGHTX_SMTP_HOST", "").strip():
        log.warning("Password reset asked, but email is not set up (INSIGHTX_SMTP_HOST is empty).")
        if not _production():
            log.warning("Local reset link (not shown in production): %s", link)
        return
    try:
        send_reset_email(address, link)
    except Exception as exc:  # never tell the caller; never log the token
        from backend.auth.email_check import _clean

        log.warning("Could not send the password reset email: %s", _clean(exc))


# ------------------------------------------------------------ the flow
def request_reset(email: str, ip: str = "", now: Optional[float] = None) -> str:
    """Email a reset link when the account exists. Always returns the same text."""
    now = now or time.time()
    email = service.clean_email(email)
    if not limiter.allow(f"ip:{ip}", IP_LIMIT, now):
        raise service.AuthError(429, "Too many requests. Try again in an hour.")
    if not service.EMAIL_RE.match(email) or not limiter.allow(f"email:{email}", EMAIL_LIMIT, now):
        return GENERIC_REPLY
    user = get_user_store().by_email(email)
    if user is None:
        return GENERIC_REPLY
    token = secrets.token_urlsafe(32)
    with _engine().begin() as connection:
        connection.execute(insert(reset_tokens).values(
            token_hash=_hash(token), user_id=user["id"], created_at=now,
            expires_at=now + TOKEN_TTL_S, used_at=None))
    _deliver(user["email"], token)
    return GENERIC_REPLY


def reset_password(token: str, new_password: str, now: Optional[float] = None) -> None:
    now = now or time.time()
    invalid = service.AuthError(400, "This reset link is not valid or has expired. Ask for a new one.")
    if not token or len(token) > 200:
        raise invalid
    with _engine().connect() as connection:
        row = connection.execute(
            select(reset_tokens).where(reset_tokens.c.token_hash == _hash(token))).mappings().first()
    if row is None or row["used_at"] is not None or row["expires_at"] < now:
        raise invalid
    problem = passwords.password_problem(new_password or "")
    if problem:
        raise service.AuthError(400, problem)
    with _engine().begin() as connection:
        # Mark used first; only one request can win.
        claimed = connection.execute(
            update(reset_tokens)
            .where(reset_tokens.c.id == row["id"], reset_tokens.c.used_at.is_(None))
            .values(used_at=now)).rowcount
    if claimed != 1:
        raise invalid
    store = get_user_store()
    user = store.by_id(row["user_id"])
    if user is None:
        raise invalid
    store.change(row["user_id"], end_sessions=True, password_hash=passwords.hash_password(new_password))
    service.lockout.succeeded(user["email"])  # a locked-out person can sign in right away


# ------------------------------------------------------------ endpoints
router = APIRouter(prefix="/auth", tags=["auth"])


class ForgotRequest(BaseModel):
    email: str = Field(max_length=254)


class ResetRequest(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


@router.post("/forgot-password")
def forgot_password(body: ForgotRequest, request: Request):
    try:
        return {"message": request_reset(body.email, _client_ip(request))}
    except service.AuthError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error


@router.post("/reset-password")
def reset_password_endpoint(body: ResetRequest):
    try:
        reset_password(body.token, body.new_password)
    except service.AuthError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from error
    return {"message": "Your password has been changed. Sign in with the new password."}
