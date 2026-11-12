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
# config/users.yaml không nằm trong git → tạo từ mẫu nếu chưa có.
if not (ROOT / "config" / "users.yaml").exists():
    shutil.copy(ROOT / "config" / "users.example.yaml", ROOT / "config" / "users.yaml")
os.environ["APP_JOBS"] = "0"  # không chạy việc định kỳ nền khi test (test gọi core.jobs trực tiếp)


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
