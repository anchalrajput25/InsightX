# backend/alerting/notify.py
#
# Send an alert outside the app (optional, per alert):
#
#   web hook  a Slack, Discord, Microsoft Teams or any "incoming webhook"
#             URL. The message goes as {"text": ..., "content": ...}, which
#             Slack and Teams read as "text" and Discord as "content".
#   e-mail    needs an SMTP server in the backend's environment:
#             INSIGHTX_SMTP_HOST, INSIGHTX_SMTP_PORT (587),
#             INSIGHTX_SMTP_USER, INSIGHTX_SMTP_PASSWORD,
#             INSIGHTX_SMTP_FROM, INSIGHTX_SMTP_TLS (1).
#
# Failures never stop a check; they are written on the alert instead.

import os
import re
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List

import requests

ICONS = {"critical": "🔴", "warning": "🟠", "resolved": "✅"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_webhook(url: str) -> bool:
    return bool(re.match(r"^https?://[^\s/]+", str(url or "").strip()))


def valid_email(address: str) -> bool:
    return bool(_EMAIL.match(str(address or "").strip()))


def smtp_ready() -> bool:
    return bool(os.getenv("INSIGHTX_SMTP_HOST", "").strip())


def text_for(alert: Dict[str, Any], result: Dict[str, Any]) -> str:
    icon = ICONS.get(result.get("severity") or "warning", "🟠")
    severity = str(result.get("severity") or "warning").upper()
    where = f" · {alert['database']}" if alert.get("database") else ""
    return (f"{icon} InsightX alert ({severity}): {result.get('title') or alert['name']}\n"
            f"{result.get('message') or ''}\n"
            f"Alert: {alert['name']}{where}")


def send_webhook(url: str, text: str, timeout: float = 8.0) -> None:
    response = requests.post(url.strip(), json={"text": text, "content": text[:1900]}, timeout=timeout)
    if response.status_code >= 300:
        raise RuntimeError(f"web hook answered {response.status_code}")


def send_email(address: str, subject: str, text: str, timeout: float = 15.0) -> None:
    host = os.getenv("INSIGHTX_SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("no e-mail server set (INSIGHTX_SMTP_HOST)")
    port = int(os.getenv("INSIGHTX_SMTP_PORT", "587"))
    user = os.getenv("INSIGHTX_SMTP_USER", "").strip()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.getenv("INSIGHTX_SMTP_FROM", user or "insightx@localhost")
    message["To"] = address.strip()
    message.set_content(text)
    with smtplib.SMTP(host, port, timeout=timeout) as server:
        if os.getenv("INSIGHTX_SMTP_TLS", "1").strip().lower() not in ("0", "false", "no", "off"):
            server.starttls()
        if user:
            server.login(user, os.getenv("INSIGHTX_SMTP_PASSWORD", ""))
        server.send_message(message)


def send(alert: Dict[str, Any], result: Dict[str, Any]) -> List[str]:
    """Send to the alert's channels. Returns one line per channel: 'web hook: sent' / '... failed: why'."""
    channels = alert.get("channels") or {}
    text = text_for(alert, result)
    lines = []
    if channels.get("webhook"):
        try:
            send_webhook(channels["webhook"], text)
            lines.append("web hook: sent")
        except Exception as exc:
            lines.append(f"web hook failed: {str(exc)[:150]}")
    if channels.get("email"):
        try:
            send_email(channels["email"], f"InsightX alert: {result.get('title') or alert['name']}", text)
            lines.append("e-mail: sent")
        except Exception as exc:
            lines.append(f"e-mail failed: {str(exc)[:150]}")
    return lines
