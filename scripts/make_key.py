"""Tạo khóa Fernet cho APP_SECRET_KEY; nếu chưa có thì tạo .env từ .env.example và config/users.yaml từ users.example.yaml."""

from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    key = Fernet.generate_key().decode()
    env = ROOT / ".env"
    if not env.exists():
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        env.write_text(template.replace("APP_SECRET_KEY=", f"APP_SECRET_KEY={key}", 1), encoding="utf-8")
        print("Đã tạo .env với khóa mới.")
    else:
        print("File .env đã tồn tại. Khóa mới (chỉ dùng khi tạo CSDL mới):")
        print(key)
    users = ROOT / "config" / "users.yaml"
    if not users.exists():
        users.write_text((ROOT / "config" / "users.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        print("Đã tạo config/users.yaml từ mẫu (tài khoản demo – đổi mật khẩu trước khi dùng thật).")


if __name__ == "__main__":
    main()
