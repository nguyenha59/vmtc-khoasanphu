"""Phiên đăng nhập phía máy chủ và kiểm tra quyền theo core.security.PERMISSIONS.

Cookie (ký bằng APP_SECRET_KEY) chỉ chứa mã phiên ngẫu nhiên. Mỗi yêu cầu:
  - phiên phải còn trong bảng user_session, chưa hết hạn, chưa bị thu hồi (đăng xuất = thu hồi thật);
  - người dùng phải còn trong users.yaml và vai trò lấy lại từ file (đổi vai trò / xóa tài khoản có hiệu lực ngay).
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import update

from core.db import now, session_scope
from core.models import UserSession
from core.security import PERMISSIONS, AppUser, MissingSecretKey, can, load_users

COOKIE = "tl_session"
SESSION_HOURS = 12


@dataclass(frozen=True)
class CurrentUser:
    user: AppUser
    user_id: int

    @property
    def role(self) -> str:
        return self.user.role

    def permissions(self) -> list[str]:
        return sorted(a for a in PERMISSIONS if can(self.role, a))


def _serializer() -> URLSafeTimedSerializer:
    key = os.environ.get("APP_SECRET_KEY", "")
    if not key:
        raise MissingSecretKey("Thiếu APP_SECRET_KEY – chạy scripts/make_key.py rồi đặt vào .env")
    return URLSafeTimedSerializer(key, salt="tl-session")


def _hash(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _sid(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        d = _serializer().loads(token, max_age=SESSION_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None
    return d.get("sid") if isinstance(d, dict) else None


def issue_session(response: Response, user: AppUser, user_id: int) -> None:
    sid = secrets.token_urlsafe(32)
    t = now()
    with session_scope() as s:
        s.add(UserSession(sid_hash=_hash(sid), username=user.username, user_id=user_id, created_at=t,
                          expires_at=t + timedelta(hours=SESSION_HOURS)))
    response.set_cookie(COOKIE, _serializer().dumps({"sid": sid}), max_age=SESSION_HOURS * 3600, httponly=True,
                        samesite="lax", secure=os.environ.get("APP_COOKIE_SECURE") == "1")


def clear_session(response: Response, request: Request | None = None) -> None:
    sid = _sid(request) if request is not None else None
    if sid:
        with session_scope() as s:
            s.execute(update(UserSession).where(UserSession.sid_hash == _hash(sid), UserSession.revoked_at.is_(None))
                      .values(revoked_at=now()))
    response.delete_cookie(COOKIE)


def revoke_user_sessions(username: str) -> int:
    """Thu hồi mọi phiên của một người (ví dụ khi đổi mật khẩu / nghỉ việc)."""
    with session_scope() as s:
        res = s.execute(update(UserSession).where(UserSession.username == username, UserSession.revoked_at.is_(None))
                        .values(revoked_at=now()))
        return res.rowcount or 0  # type: ignore[attr-defined]


def optional_user(request: Request) -> CurrentUser | None:
    sid = _sid(request)
    if not sid:
        return None
    with session_scope() as s:
        row = s.get(UserSession, _hash(sid))
        if row is None or row.revoked_at is not None or row.expires_at < now():
            return None
        username, user_id = row.username, row.user_id
    u = next((x for x in load_users() if x.get("username") == username), None)
    if u is None:  # tài khoản đã bị xóa khỏi users.yaml
        return None
    return CurrentUser(AppUser(username=u["username"], name=u["name"], role=u["role"]), user_id)


def current_user(user: CurrentUser | None = Depends(optional_user)) -> CurrentUser:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Vui lòng đăng nhập.")
    return user


def require(action: str) -> Callable[[CurrentUser], CurrentUser]:
    """Dependency: dừng với 403 nếu vai trò không được phép."""

    def dep(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if not can(user.role, action):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Vai trò của bạn không được phép thao tác này. Liên hệ quản trị nếu cần quyền.")
        return user

    return dep
