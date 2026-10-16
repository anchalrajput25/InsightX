# backend/ai_gateway/context.py
#
# Who is asking, and which question an AI call belongs to. Set once per
# API request; follows the request into worker threads together with
# the telemetry collector (telemetry.in_current_context copies it).

import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

# Calls made outside a question (scripts, warm-up) belong to "system",
# never to a person, so they cannot use up anyone's daily limit.
SYSTEM_USER = "system"
_user: contextvars.ContextVar[str] = contextvars.ContextVar("insightx_user", default=SYSTEM_USER)
_request: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("insightx_request", default=None)

ANONYMOUS = "local"


def clean_user_id(value: Optional[str]) -> str:
    text = "".join(ch for ch in str(value or "").strip() if ch.isprintable())[:120]
    return text or ANONYMOUS


@contextmanager
def ai_request(user_id: Optional[str] = None, request_id: Optional[str] = None) -> Iterator[str]:
    """Bind a user and a fresh request id for the duration of one question."""
    rid = request_id or uuid.uuid4().hex
    user_token = _user.set(clean_user_id(user_id))
    request_token = _request.set(rid)
    try:
        yield rid
    finally:
        _request.reset(request_token)
        _user.reset(user_token)


def current_user() -> str:
    return _user.get()


def current_request() -> str:
    # Calls made outside a request (warm-up, scripts) each count as their own.
    return _request.get() or uuid.uuid4().hex
