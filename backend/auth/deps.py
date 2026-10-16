# Who is asking. With sign-in on (the default), every protected endpoint
# needs "Authorization: Bearer <token>" from /auth/login or /auth/signup.
# INSIGHTX_AUTH_REQUIRED=0 turns sign-in off for local development and
# tests; the X-InsightX-User header then names the user, as before.

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException

from backend.ai_gateway.context import clean_user_id
from backend.auth.service import user_for_token


def auth_required() -> bool:
    return os.getenv("INSIGHTX_AUTH_REQUIRED", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class Caller:
    user_id: str                       # name in the AI usage ledger and daily limit
    account_id: Optional[int] = None   # None when sign-in is off
    email: str = ""
    name: str = ""

    def owns(self, owner_account_id: Optional[int]) -> bool:
        """Whether a chat / connection / upload owned by owner_account_id is this caller's."""
        return self.account_id is None or owner_account_id == self.account_id


def bearer_token(authorization: Optional[str]) -> str:
    scheme, _, token = str(authorization or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def current_caller(
    authorization: Optional[str] = Header(default=None),
    x_insightx_user: Optional[str] = Header(default=None),
) -> Caller:
    if not auth_required():
        return Caller(user_id=clean_user_id(x_insightx_user))
    user = user_for_token(bearer_token(authorization))
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    return Caller(user_id=user["email"], account_id=user["id"], email=user["email"], name=user["name"])
