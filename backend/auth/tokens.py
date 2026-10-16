# Signed session tokens: base64url(json payload) + "." + HMAC-SHA256.
# The payload carries the user id, the user's session version (bumped on
# sign-out everywhere, password change, disable or role change, which
# invalidates older tokens) and an expiry time.

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("insightx.auth")

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_FILE = BASE_DIR / "logs" / "auth_secret.key"


def session_hours() -> float:
    try:
        return max(0.1, float(os.getenv("INSIGHTX_SESSION_HOURS", "12")))
    except ValueError:
        return 12.0


_secret: Optional[bytes] = None


def secret() -> bytes:
    """INSIGHTX_AUTH_SECRET, or a random key kept in logs/ for local use."""
    global _secret
    if _secret is None:
        configured = os.getenv("INSIGHTX_AUTH_SECRET", "").strip()
        if configured:
            _secret = configured.encode("utf-8")
        else:
            SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            if not SECRET_FILE.exists():
                SECRET_FILE.write_text(secrets.token_urlsafe(48), encoding="utf-8")
                logger.warning("INSIGHTX_AUTH_SECRET is not set; created %s. Set it in .env when you deploy.",
                               SECRET_FILE)
            _secret = SECRET_FILE.read_text(encoding="utf-8").strip().encode("utf-8")
    return _secret


def set_secret(value: Optional[bytes]) -> None:
    """Tests use this."""
    global _secret
    _secret = value


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(secret(), body.encode("ascii"), hashlib.sha256).digest())


def issue(user_id: int, session_version: int, now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    expires = now + session_hours() * 3600
    body = _b64(json.dumps({"uid": user_id, "ver": session_version, "exp": int(expires)},
                           separators=(",", ":")).encode("utf-8"))
    return {"token": f"{body}.{_sign(body)}", "expires_at": int(expires)}


def read(token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The payload of a valid, unexpired token, else None."""
    try:
        body, signature = token.split(".")
        if not hmac.compare_digest(signature, _sign(body)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < (now or time.time()):
        return None
    return payload
