# /auth endpoints: status, sign-up, sign-in, me, change password, sign out everywhere.

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.ai_gateway import get_gateway
from backend.ai_gateway.usage_store import get_store
from backend.auth import service
from backend.auth.deps import Caller, auth_required, current_caller
from backend.auth.store import get_user_store, public

router = APIRouter(prefix="/auth", tags=["auth"])


class SignUpRequest(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=120)
    password: str = Field(max_length=200)


class SignInRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class PasswordRequest(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _fail(error: service.AuthError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=error.message)


@router.get("/status")
def auth_status():
    """Whether sign-in is required (the app shows the sign-in screen when it is)."""
    return {"required": auth_required()}


@router.post("/signup")
def sign_up(request: SignUpRequest):
    try:
        return service.sign_up(request.email, request.name, request.password)
    except service.AuthError as error:
        raise _fail(error) from error


@router.post("/login")
def sign_in(request: SignInRequest):
    try:
        return service.sign_in(request.email, request.password)
    except service.AuthError as error:
        raise _fail(error) from error


@router.get("/me")
def me(caller: Caller = Depends(current_caller)):
    """The signed-in account and how many of today's questions it has used."""
    limit = get_gateway().settings.user_daily_questions
    used = get_store().questions_today(caller.user_id)
    account = public(get_user_store().by_id(caller.account_id)) if caller.account_id else {
        "email": "", "name": caller.user_id}
    return {"user": account, "questions_today": used, "daily_limit": limit}


@router.post("/password")
def change_password(request: PasswordRequest, caller: Caller = Depends(current_caller)):
    if caller.account_id is None:
        raise HTTPException(status_code=400, detail="Sign-in is turned off on this server.")
    try:
        return service.change_password(get_user_store().by_id(caller.account_id),
                                       request.current_password, request.new_password)
    except service.AuthError as error:
        raise _fail(error) from error


@router.post("/logout-everywhere")
def logout_everywhere(caller: Caller = Depends(current_caller)):
    if caller.account_id is not None:
        service.sign_out_everywhere(caller.account_id)
    return {"status": "signed_out"}
