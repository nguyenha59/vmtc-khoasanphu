"""Đặt / đổi mật khẩu tài khoản nhân viên trong config/users.yaml (thay cho việc tự dán mã băm bcrypt).

Chạy (gõ mật khẩu ẩn, 2 lần):
  python scripts/set_password.py nurse                                   # đổi mật khẩu tài khoản có sẵn
  python scripts/set_password.py dd.lan --name "ĐD Lan" --role nurse     # tạo tài khoản mới

Mật khẩu: ≥ 12 ký tự, không phải mật khẩu demo / mật khẩu yếu, không chứa tên đăng nhập; băm bcrypt hệ số 12.
Sau khi đổi: thu hồi mọi phiên đăng nhập cũ của người đó + ghi audit_log (luật 7). Không in mật khẩu ra màn hình.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_env  # noqa: E402
from core.security import ROLES, password_problems, set_user_password  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("--name")
    ap.add_argument("--role", choices=ROLES)
    args = ap.parse_args()
    load_env()
    pw = getpass.getpass("Mật khẩu mới: ")
    problems = password_problems(pw, args.username)
    if problems:
        print("✗ Mật khẩu chưa đạt: " + "; ".join(problems))
        return 1
    if getpass.getpass("Nhập lại: ") != pw:
        print("✗ Hai lần nhập không khớp.")
        return 1
    try:
        created = set_user_password(args.username, pw, name=args.name, role=args.role)
    except ValueError as e:
        print(f"✗ {e}")
        return 1
    print(f"✓ Đã {'tạo tài khoản' if created else 'đổi mật khẩu cho'} {args.username}.")
    try:  # cần CSDL: thu hồi phiên cũ + nhật ký
        from api.deps import revoke_user_sessions
        from core import audit
        from core.db import init_db

        init_db()
        n = revoke_user_sessions(args.username)
        audit.log("password_set", "user", None, {"username": args.username, "created": created, "revoked_sessions": n})
        print(f"  Đã thu hồi {n} phiên đăng nhập cũ.")
    except Exception as e:  # noqa: BLE001 – máy chưa có CSDL: cookie cũ vẫn mất hiệu lực vì gắn với mã băm cũ
        print(f"  ⚠ Không ghi được CSDL ({type(e).__name__}); phiên cũ vẫn hết hiệu lực vì mã băm đã đổi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
