from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_SECRET_KEY", Fernet.generate_key().decode())
os.environ["LLM_PROVIDER"] = "mock"
os.environ["QUESTION_SELECTOR_PROVIDER"] = "mock"  # không bao giờ gọi OpenAI thật khi test
# config/users.yaml không nằm trong git → tạo từ mẫu nếu chưa có.
if not (ROOT / "config" / "users.yaml").exists():
    shutil.copy(ROOT / "config" / "users.example.yaml", ROOT / "config" / "users.yaml")
os.environ["APP_JOBS"] = "0"  # không chạy việc định kỳ nền khi test (test gọi core.jobs trực tiếp)
# Không bao giờ dùng khóa API thật khi test (setdefault của load_env không ghi đè biến đã đặt, kể cả chuỗi rỗng).
for _k in ("OPENAI_API_KEY", "TEAMS_WEBHOOK_URL", "SMS_TEST_API_URL"):
    os.environ[_k] = ""


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    """Chặn MỌI kết nối mạng ra ngoài máy khi test: chứng minh không luồng nào gọi API bên ngoài (OpenAI, Teams, SMS).
    Cho phép localhost (Ollama nếu có). Gọi ra ngoài → lỗi, test thất bại."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"Test cố kết nối ra ngoài: {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture()
def rules():
    from core.config import load_rules

    return load_rules(ROOT / "config" / "rules.yaml")


@pytest.fixture()
def his_cfg():
    from core.config import load_his_columns

    return load_his_columns()


@pytest.fixture()
def tmp_rules(tmp_path, monkeypatch):
    """Bản sao rules.yaml để test ghi file cấu hình."""
    p = tmp_path / "rules.yaml"
    shutil.copy(ROOT / "config" / "rules.yaml", p)
    monkeypatch.setenv("APP_RULES_FILE", str(p))
    return p


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from core import db as dbmod

    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("APP_DB_URL", url)
    dbmod.init_db(url)
    yield dbmod
    dbmod._engine = None
    dbmod._Session = None
