# Sign-up, sign-in and password changes. Sign-up is open and every
# account is equal; the per-person daily question limit protects the
# free AI quota. Repeated wrong passwords lock that email for a while.

import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional

from backend.auth import passwords, tokens
from backend.auth.store import EmailTaken, get_user_store, public

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60
LOCKOUT_S = 15 * 60


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def clean_email(email: str) -> str:
    return str(email or "").strip().lower()


# ------------------------------------------------------------- lockout
class _Lockout:
    def __init__(self):
        self.failures: Dict[str, Deque[float]] = defaultdict(deque)
        self.locked_until: Dict[str, float] = {}
        self.lock = threading.Lock()

    def check(self, key: str, now: float) -> None:
        with self.lock:
            until = self.locked_until.get(key, 0)
        if until > now:
            minutes = max(1, round((until - now) / 60))
            raise AuthError(429, f"Too many wrong passwords. Try again in about {minutes} minutes.")

    def failed(self, key: str, now: float) -> None:
        with self.lock:
            recent = self.failures[key]
            recent.append(now)
            while recent and recent[0] < now - FAILURE_WINDOW_S:
                recent.popleft()
            if len(recent) >= MAX_FAILURES:
                self.locked_until[key] = now + LOCKOUT_S
                recent.clear()

    def succeeded(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)
            self.locked_until.pop(key, None)

    def reset(self) -> None:
        with self.lock:
            self.failures.clear()
            self.locked_until.clear()


lockout = _Lockout()

# Computed once: checking a password for an unknown email costs the same
# as for a real one, so response time does not reveal which emails exist.
_DUMMY_HASH = passwords.hash_password("not-a-real-password-1")


# ------------------------------------------------------------- accounts
def sign_up(email: str, name: str, password: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Create an account and sign it in."""
    email = clean_email(email)
    name = " ".join(str(name or "").split())[:120]
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AuthError(400, "Enter a valid email address.")
    problem = passwords.password_problem(password or "")
    if problem:
        raise AuthError(400, problem)
    try:
        user = get_user_store().create(email=email, name=name or email.split("@")[0],
                                       password_hash=passwords.hash_password(password))
    except EmailTaken:
        raise AuthError(409, "An account with this email already exists. Sign in instead.")
    return _session(user, now)


def sign_in(email: str, password: str, now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    email = clean_email(email)
    lockout.check(email, now)
    user = get_user_store().by_email(email)
    if not passwords.verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH) or not user:
        lockout.failed(email, now)
        raise AuthError(401, "Wrong email or password.")
    lockout.succeeded(email)
    return _session(user, now)


def _session(user: Dict[str, Any], now: Optional[float]) -> Dict[str, Any]:
    now = now or time.time()
    store = get_user_store()
    store.change(user["id"], last_login_at=now)
    return {**tokens.issue(user["id"], user["session_version"], now), "user": public(store.by_id(user["id"]))}


def user_for_token(token: str) -> Optional[Dict[str, Any]]:
    """The account a token belongs to, or None (bad, expired, or signed out)."""
    payload = tokens.read(token or "")
    if not payload:
        return None
    user = get_user_store().by_id(int(payload.get("uid", 0)))
    if user is None or user["session_version"] != payload.get("ver"):
        return None
    return user


def sign_out_everywhere(user_id: int) -> None:
    get_user_store().change(user_id, end_sessions=True)


def change_password(user: Dict[str, Any], current: str, new: str) -> Dict[str, Any]:
    """Change the password; every other session is signed out, this one gets a fresh token."""
    if not passwords.verify_password(current or "", user["password_hash"]):
        raise AuthError(400, "Your current password is not correct.")
    problem = passwords.password_problem(new or "")
    if problem:
        raise AuthError(400, problem)
    if new == current:
        raise AuthError(400, "The new password is the same as the current one.")
    updated = get_user_store().change(user["id"], end_sessions=True, password_hash=passwords.hash_password(new))
    return _session(updated, None)


def set_password(email: str, new: str) -> Dict[str, Any]:
    """For scripts/reset_password.py: reset a forgotten password (signs out everywhere)."""
    user = get_user_store().by_email(clean_email(email))
    if user is None:
        raise AuthError(404, f"No account for {email}.")
    problem = passwords.password_problem(new or "")
    if problem:
        raise AuthError(400, problem)
    return public(get_user_store().change(user["id"], end_sessions=True,
                                          password_hash=passwords.hash_password(new)))
