"""Mã hóa Fernet, che số điện thoại, phân quyền, đăng nhập."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
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
    "manage_patient": {"nurse", "doctor"},  # tạo hồ sơ, sửa theo gói / không theo gói
    "override_monitoring": {"doctor"},  # chỉ định theo dõi từ xa ngoài gói (ca nguy cơ cao), bắt buộc ghi lý do
    "record_delivery": {"nurse", "doctor"},  # nhập ngày sinh → bật form sau sinh S1–S4
    "decide_red": {"doctor"},  # quyết định xử trí ca đỏ "Giữ đỏ"
    "review_downgrades": {"doctor"},  # xem lại ca đỏ bị điều dưỡng hạ mức
    "propose_rule": {"nurse", "doctor"},
    "approve_rule": {"doctor"},
    "change_channel": {"admin"},
    "run_eval": {"admin"},
    "retrain_model": {"admin"},
    "view_noshow": {"nurse", "admin"},
    "export_noshow_contacts": {"nurse"},
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


class MissingUsersFile(RuntimeError):
    """Chưa có file tài khoản (config/users.yaml không đưa lên GitHub – tạo bằng scripts/make_key.py)."""


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
    credential_hash: str = field(default="", repr=False)


def users_file() -> Path:
    return ROOT / os.environ.get("APP_USERS_FILE", "config/users.yaml")


_USERS_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}


def load_users(path: Path | None = None) -> list[dict[str, str]]:
    """Đọc users.yaml – lưu tạm theo thời điểm sửa file (mỗi request đăng nhập không đọc lại đĩa; sửa file → đọc lại)."""
    target = Path(path or users_file())
    try:
        mtime = target.stat().st_mtime
    except FileNotFoundError as e:
        raise MissingUsersFile(
            f"Chưa có file tài khoản {target.name} – chạy: python scripts/make_key.py (tạo từ users.example.yaml)"
        ) from e
    hit = _USERS_CACHE.get(str(target))
    if hit is None or hit[0] != mtime:
        with open(target, encoding="utf-8") as f:
            hit = (mtime, list((yaml.safe_load(f) or {}).get("users", [])))
        _USERS_CACHE[str(target)] = hit
    return [dict(u) for u in hit[1]]


# ---------- Mật khẩu ----------
BCRYPT_ROUNDS = 12  # hệ số công bcrypt cho mật khẩu mới (tài khoản demo cũ: 10)
MIN_PASSWORD_LEN = 12
ROLES = ("nurse", "doctor", "admin")
WEAK_PASSWORDS = {"nurse123", "doctor123", "admin123", "password", "matkhau", "123456789012", "qwertyuiop12",
                  "vinmec123456", "abc123456789", "password1234", "matkhau12345"}
_COST_RE = re.compile(r"^\$2[abxy]?\$(\d{2})\$")


def password_problems(password: str, username: str = "") -> list[str]:
    """Lý do mật khẩu chưa đạt (rỗng = đạt). Hàm thuần."""
    out: list[str] = []
    if len(password) < MIN_PASSWORD_LEN:
        out.append(f"cần ít nhất {MIN_PASSWORD_LEN} ký tự")
    if len(password.encode("utf-8")) > 72:
        out.append("dài quá 72 byte (bcrypt chỉ dùng 72 byte đầu)")
    low = password.lower()
    if low in WEAK_PASSWORDS or any(low.startswith(w) and len(low) - len(w) <= 2 for w in WEAK_PASSWORDS):
        out.append("nằm trong danh sách mật khẩu yếu / mật khẩu demo")
    if username and username.lower() in low:
        out.append("không được chứa tên đăng nhập")
    if password and (len(set(password)) < 5 or password.isdigit()):
        out.append("quá đơn giản (toàn số hoặc ít ký tự khác nhau)")
    return out


def hash_password(password: str) -> str:
    problems = password_problems(password)
    if any("72 byte" in p for p in problems):
        raise ValueError("Mật khẩu dài quá 72 byte")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def hash_cost(password_hash: str) -> int:
    m = _COST_RE.match(password_hash or "")
    return int(m.group(1)) if m else BCRYPT_ROUNDS


_DUMMY_HASHES: dict[int, bytes] = {}


def _dummy_hash(cost: int) -> bytes:
    """Băm giả CÙNG hệ số công với tài khoản thật: tên đăng nhập không tồn tại tốn thời gian như tên có thật
    (không dò được tài khoản qua thời gian phản hồi)."""
    if cost not in _DUMMY_HASHES:
        _DUMMY_HASHES[cost] = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=max(4, min(cost, 16))))
    return _DUMMY_HASHES[cost]


def authenticate(username: str, password: str, path: Path | None = None) -> AppUser | None:
    users = load_users(path)
    cost = max((hash_cost(u.get("password_hash", "")) for u in users), default=BCRYPT_ROUNDS)
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        bcrypt.checkpw(b"oversized-invalid-password", _dummy_hash(cost))
        return None
    for u in users:
        if u["username"] == username:
            if bcrypt.checkpw(encoded, u["password_hash"].encode()):
                return AppUser(username=u["username"], name=u["name"], role=u["role"], credential_hash=u["password_hash"])
            return None
    bcrypt.checkpw(encoded, _dummy_hash(cost))
    return None


def set_user_password(username: str, password: str, *, name: str | None = None, role: str | None = None,
                      path: Path | None = None) -> bool:
    """Đặt mật khẩu (tạo tài khoản nếu chưa có – cần name + role). Kiểm độ mạnh, băm bcrypt, ghi file nguyên tử.
    Trả True nếu tạo mới. Phiên cũ của người này phải được thu hồi ở nơi gọi (api.deps.revoke_user_sessions)."""
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username or ""):
        raise ValueError("Tên đăng nhập: 3–32 ký tự a-z, 0-9, . _ -")
    problems = password_problems(password, username)
    if problems:
        raise ValueError("Mật khẩu chưa đạt: " + "; ".join(problems))
    if role is not None and role not in ROLES:
        raise ValueError(f"Vai trò phải là một trong {', '.join(ROLES)}")
    target = Path(path or users_file())
    data = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}) if target.exists() else {}
    users = list(data.get("users") or [])
    hit = next((u for u in users if u.get("username") == username), None)
    created = hit is None
    if created:
        if not name or not role:
            raise ValueError("Tài khoản mới cần --name và --role")
        hit = {"username": username, "name": name, "role": role}
        users.append(hit)
    if name:
        hit["name"] = name
    if role:
        hit["role"] = role
    hit["password_hash"] = hash_password(password)
    data["users"] = users
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(target)
    _USERS_CACHE.pop(str(target), None)
    return created
