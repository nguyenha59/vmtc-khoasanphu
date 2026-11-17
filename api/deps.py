"""Phiên đăng nhập phía máy chủ và kiểm tra quyền theo core.security.PERMISSIONS.

Cookie ký bằng khóa phiên (`SESSION_SECRET_KEY`, hoặc khóa dẫn xuất từ APP_SECRET_KEY)
chứa mã phiên ngẫu nhiên và dấu HMAC gắn với mật khẩu tại lúc đăng nhập. Mỗi yêu cầu:
  - phiên phải còn trong bảng user_session, chưa hết hạn, chưa bị thu hồi (đăng xuất = thu hồi thật);
  - người dùng phải còn trong users.yaml và vai trò lấy lại từ file (đổi vai trò / xóa tài khoản có hiệu lực ngay).
  - đổi mật khẩu làm mất hiệu lực cookie cũ; cookie không chứa hash mật khẩu.
"""

from __future__ import annotations

import hashlib
import hmac
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


def session_signing_key() -> str:
    """Khóa ký cookie phiên, tách khỏi khóa mã hóa dữ liệu (Fernet).

    Ưu tiên `SESSION_SECRET_KEY`; không đặt thì dẫn xuất một chiều từ APP_SECRET_KEY (HMAC-SHA256),
    để lộ khóa ký không suy ra được khóa giải mã tên / SĐT.
    """
    own = os.environ.get("SESSION_SECRET_KEY", "").strip()
    if own:
        return own
    key = os.environ.get("APP_SECRET_KEY", "").strip()
    if not key:
        raise MissingSecretKey("Thiếu APP_SECRET_KEY – chạy scripts/make_key.py rồi đặt vào .env")
    return hmac.new(key.encode(), b"tl-session-signing-v1", hashlib.sha256).hexdigest()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(session_signing_key(), salt="tl-session")


def _hash(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def _credential_proof(password_hash: str) -> str:
    return hmac.new(session_signing_key().encode(), ("login-credential:" + password_hash).encode(), hashlib.sha256).hexdigest()


def _cookie_payload(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        d = _serializer().loads(token, max_age=SESSION_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None
    return d if isinstance(d, dict) else None


def _sid(request: Request) -> str | None:
    payload = _cookie_payload(request)
    sid = payload.get("sid") if payload is not None else None
    return sid if isinstance(sid, str) and sid else None


def issue_session(response: Response, user: AppUser, user_id: int) -> None:
    current = next((u for u in load_users() if u.get("username") == user.username), None)
    if current is None or (user.credential_hash and current.get("password_hash") != user.credential_hash):
        raise HTTPException(401, "Tài khoản đã thay đổi. Vui lòng đăng nhập lại.")
    sid = secrets.token_urlsafe(32)
    t = now()
    with session_scope() as s:
        s.add(UserSession(sid_hash=_hash(sid), username=user.username, user_id=user_id, created_at=t,
                          expires_at=t + timedelta(hours=SESSION_HOURS)))
    response.set_cookie(COOKIE, _serializer().dumps({"sid": sid, "credential": _credential_proof(current["password_hash"])}),
                        max_age=SESSION_HOURS * 3600, httponly=True,
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
    payload = _cookie_payload(request)
    sid = payload.get("sid") if payload is not None else None
    if not isinstance(sid, str) or not sid:
        return None
    with session_scope() as s:
        row = s.get(UserSession, _hash(sid))
        if row is None or row.revoked_at is not None or row.expires_at < now():
            return None
        username, user_id = row.username, row.user_id
    u = next((x for x in load_users() if x.get("username") == username), None)
    if u is None:  # tài khoản đã bị xóa khỏi users.yaml
        revoke_user_sessions(username)
        return None
    proof = payload.get("credential", "")
    if not isinstance(proof, str) or not hmac.compare_digest(proof, _credential_proof(u["password_hash"])):
        with session_scope() as s:
            s.execute(update(UserSession).where(UserSession.sid_hash == _hash(sid)).values(revoked_at=now()))
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
            from core import audit

            audit.log("access_denied", "permission", action, {"role": user.role}, user_id=user.user_id)
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Vai trò của bạn không được phép thao tác này. Liên hệ quản trị nếu cần quyền.")
        return user

    return dep
