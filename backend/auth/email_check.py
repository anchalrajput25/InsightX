# GET /auth/email-check : is password-reset email working on this server?
#
# Shows which email settings are present (yes/no only, never values),
# then tries to reach the SMTP server, start TLS and log in. With
# ?send=1 it also sends a test email to the server's own sender address
# (INSIGHTX_SMTP_FROM), never to an address given by the caller. Errors
# show the server's reply with the password removed. Limited to a few
# checks per hour so it cannot be used to hammer the mail server.

import os
import re
import smtplib
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Deque, Dict

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_CHECKS_PER_HOUR = 6
_recent: Deque[float] = deque()
_lock = threading.Lock()
SETTINGS = ("INSIGHTX_SMTP_HOST", "INSIGHTX_SMTP_PORT", "INSIGHTX_SMTP_USER",
            "INSIGHTX_SMTP_PASSWORD", "INSIGHTX_SMTP_FROM", "INSIGHTX_PUBLIC_URL")


def _allowed(now: float) -> bool:
    with _lock:
        while _recent and _recent[0] < now - 3600:
            _recent.popleft()
        if len(_recent) >= MAX_CHECKS_PER_HOUR:
            return False
        _recent.append(now)
        return True


def reset_limit() -> None:
    with _lock:
        _recent.clear()


def _clean(error: Exception) -> str:
    """Error type and server reply, with any password or key removed."""
    text = f"{type(error).__name__}: {error}"
    secret = os.getenv("INSIGHTX_SMTP_PASSWORD", "")
    if secret:
        text = text.replace(secret, "***")
    text = re.sub(r"x(smtp|key)sib-[A-Za-z0-9-]+", "***", text)
    return text[:300]


def check_email(send: bool = False) -> Dict[str, Any]:
    present = {name: bool(os.getenv(name, "").strip()) for name in SETTINGS}
    if os.getenv("INSIGHTX_MAIL_FROM", "").strip() and not present["INSIGHTX_SMTP_FROM"]:
        present["INSIGHTX_SMTP_FROM"] = True
    result: Dict[str, Any] = {"settings_present": present, "connect": "not tried", "login": "not tried",
                              "test_email": "not asked (add ?send=1)"}
    host = os.getenv("INSIGHTX_SMTP_HOST", "").strip()
    if not host:
        result["connect"] = "skipped: INSIGHTX_SMTP_HOST is empty"
        return result
    try:
        port = int(os.getenv("INSIGHTX_SMTP_PORT", "587"))
    except ValueError:
        result["connect"] = "INSIGHTX_SMTP_PORT is not a number"
        return result
    result["port"] = port
    sender = os.getenv("INSIGHTX_SMTP_FROM") or os.getenv("INSIGHTX_MAIL_FROM") or os.getenv("INSIGHTX_SMTP_USER", "")
    result["sender_address_looks_valid"] = "@" in parseaddr(sender)[1]
    try:
        with smtplib.SMTP(host, port, timeout=12) as server:
            result["connect"] = "ok"
            if os.getenv("INSIGHTX_SMTP_TLS", "1").strip().lower() not in ("0", "false", "no", "off"):
                server.starttls()
            user = os.getenv("INSIGHTX_SMTP_USER", "").strip()
            if user:
                server.login(user, os.getenv("INSIGHTX_SMTP_PASSWORD", ""))
                result["login"] = "ok"
            else:
                result["login"] = "skipped: INSIGHTX_SMTP_USER is empty"
            if send:
                address = parseaddr(sender)[1]
                message = EmailMessage()
                message["Subject"] = "InsightX email test"
                message["From"] = sender
                message["To"] = address
                message.set_content("This is a test from InsightX. Password reset emails can be sent.")
                server.send_message(message)
                result["test_email"] = "accepted by the mail server (check the sender inbox and spam)"
    except Exception as exc:
        step = "connect" if result["connect"] != "ok" else ("login" if result["login"] == "not tried" else "test_email")
        result[step] = _clean(exc)
    return result


@router.get("/email-check")
def email_check(send: int = 0):
    if not _allowed(time.time()):
        raise HTTPException(status_code=429, detail="Too many email checks. Try again in an hour.")
    return check_email(send=bool(send))
