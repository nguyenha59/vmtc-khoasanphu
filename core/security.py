"""Mã hóa Fernet, che số điện thoại, phân quyền, đăng nhập."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import bcrypt
import yaml
from cryptography.fernet import Fernet, InvalidToken

from core.config import ROOT

# Phân quyền theo docs/07.
PERMISSIONS: dict[str, set[str]] = {
    "upload_his": {"nurse"},
    "confirm_arrival": {"nurse"},
    "export_worldsms": {"nurse"},
    "form_schedule": {"nurse"},
    "export_form_sms": {"nurse"},
    "view_alerts": {"nurse", "doctor"},
    "handle_alerts": {"nurse", "doctor"},
    "view_profile": {"nurse", "doctor"},
    "reveal_phone": {"nurse", "doctor"},
    "manage_consent": {"nurse", "doctor"},
    "record_delivery": {"nurse", "doctor"},  # nhập ngày sinh → bật form sau sinh S1–S4
    "decide_red": {"doctor"},  # quyết định xử trí ca đỏ "Giữ đỏ"
    "review_downgrades": {"doctor"},  # xem lại ca đỏ bị điều dưỡng hạ mức
    "propose_rule": {"nurse", "doctor"},
    "approve_rule": {"doctor"},
    "change_channel": {"admin"},
    "run_eval": {"admin"},
    "retrain_model": {"admin"},
    "view_noshow": {"nurse", "admin"},
    "view_quality": {"admin", "doctor"},
    "view_config": {"nurse", "doctor", "admin"},
    "demo_form": {"nurse", "doctor", "admin"},
}


class PermissionDenied(Exception):
    pass


def can(role: str | None, action: str) -> bool:
    return role is not None and role in PERMISSIONS.get(action, set())


def require(role: str | None, action: str) -> None:
    if not can(role, action):
        raise PermissionDenied(f"Vai trò '{role}' không được phép thực hiện '{action}'.")


# ---------- Mã hóa ----------


class MissingSecretKey(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("APP_SECRET_KEY", "").strip()
    if not key:
        raise MissingSecretKey(
            "Chưa đặt APP_SECRET_KEY. Chạy: python scripts/make_key.py rồi chép vào file .env"
        )
    return Fernet(key.encode())


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Không giải mã được – sai APP_SECRET_KEY?") from e


# ---------- Số điện thoại ----------

_PHONE_RE = re.compile(r"^0\d{9}$")


def normalize_phone(raw: object) -> str:
    """Chuẩn hóa về 10 số bắt đầu bằng 0. Trả chuỗi (có thể không hợp lệ)."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if s.endswith(".0"):  # Excel đọc số thành float
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    if digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    elif len(digits) == 9 and not digits.startswith("0"):
        digits = "0" + digits
    return digits


def is_valid_phone(phone: str) -> bool:
    return bool(_PHONE_RE.match(phone or ""))


def mask_phone(phone: str | None) -> str:
    """Che số điện thoại để hiển thị: 0912345412 → '09•• ••• 412'."""
    p = normalize_phone(phone)
    if len(p) < 6:
        return "•••"
    return f"{p[:2]}•• ••• {p[-3:]}"


# ---------- Đăng nhập ----------


@dataclass(frozen=True)
class AppUser:
    username: str
    name: str
    role: str


def users_file() -> Path:
    return ROOT / os.environ.get("APP_USERS_FILE", "config/users.yaml")


def load_users(path: Path | None = None) -> list[dict[str, str]]:
    with open(path or users_file(), encoding="utf-8") as f:
        return list((yaml.safe_load(f) or {}).get("users", []))


# Băm giả để tên đăng nhập không tồn tại vẫn tốn thời gian như tên có thật (không dò được tài khoản qua thời gian).
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=10))


def authenticate(username: str, password: str, path: Path | None = None) -> AppUser | None:
    for u in load_users(path):
        if u["username"] == username:
            if bcrypt.checkpw(password.encode(), u["password_hash"].encode()):
                return AppUser(username=u["username"], name=u["name"], role=u["role"])
            return None
    bcrypt.checkpw(password.encode(), _DUMMY_HASH)
    return None
