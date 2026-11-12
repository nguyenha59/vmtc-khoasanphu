"""Đăng nhập / đăng xuất / thông tin người dùng hiện tại."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from api.deps import CurrentUser, clear_session, current_user, issue_session, optional_user
from api.throttle import login_throttle
from core import audit
from core.db import session_scope
from core.repo import ensure_user
from core.security import authenticate

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def me_payload(u: CurrentUser) -> dict:
    return {"username": u.user.username, "name": u.user.name, "role": u.role, "permissions": u.permissions()}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    username = body.username.strip()
    key = login_throttle.key(request.client.host if request.client else "?", username)
    wait = login_throttle.retry_after(key)
    if wait:
        audit.log("login_blocked", "user", None, {"username": username, "retry_after": wait})
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Sai mật khẩu quá nhiều lần. Thử lại sau {wait // 60 + 1} phút.",
                            headers={"Retry-After": str(wait)})
    user = authenticate(username, body.password)
    if user is None:
        login_throttle.failed(key)
        audit.log("login_failed", "user", None, {"username": username})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sai tên đăng nhập hoặc mật khẩu.")
    login_throttle.succeeded(key)
    with session_scope() as s:
        uid = ensure_user(s, user)
    audit.log("login", "user", uid, {"role": user.role}, user_id=uid)
    issue_session(response, user, uid)
    return me_payload(CurrentUser(user, uid))


@router.post("/logout")
def logout(request: Request, response: Response, user: CurrentUser | None = Depends(optional_user)) -> dict:
    if user is not None:
        audit.log("logout", "user", user.user_id, user_id=user.user_id)
    clear_session(response, request)  # thu hồi phiên phía máy chủ – cookie cũ không dùng lại được
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> dict:
    return me_payload(user)
