# Password hashing with PBKDF2-HMAC-SHA256 from the standard library
# (no extra package on Windows). Stored as
#   pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>
# so the cost can be raised later without breaking old hashes.

import base64
import hashlib
import hmac
import os

ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256
MIN_LENGTH = 8


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str:
    """Why a new password is not acceptable, or "" when it is."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if password.isdigit() or password.isalpha():
        return "Use letters and numbers (or symbols), not only one kind."
    return ""
