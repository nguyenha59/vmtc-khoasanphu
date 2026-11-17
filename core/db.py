"""Kết nối SQLite (data/app.db) qua SQLAlchemy 2."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from core.config import DATA_DIR, TZ
from core.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def db_url() -> str:
    return os.environ.get("APP_DB_URL", f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}")


def get_engine(url: str | None = None) -> Engine:
    global _engine, _Session
    target = url or db_url()
    if _engine is None or str(_engine.url) != target:
        if target.startswith("sqlite:///") and ":memory:" not in target:
            os.makedirs(os.path.dirname(target.replace("sqlite:///", "")) or ".", exist_ok=True)
        # timeout: chờ tối đa 15 giây khi CSDL đang có người ghi (mặc định 5 giây). Giao dịch ghi luôn ngắn
        # (không gọi AI hay mạng trong giao dịch – xem core.alerts.submit_response).
        connect_args = {"timeout": 15} if target.startswith("sqlite") else {}
        _engine = create_engine(target, future=True, connect_args=connect_args)

        @event.listens_for(_engine, "connect")
        def _fk_on(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            if target.startswith("sqlite") and ":memory:" not in target:
                # WAL: người đọc không bị chặn khi có người ghi (nhiều nhân viên cùng lúc); NORMAL an toàn với WAL.
                dbapi_conn.execute("PRAGMA journal_mode=WAL")
                dbapi_conn.execute("PRAGMA synchronous=NORMAL")

        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db(url: str | None = None) -> Engine:
    engine = get_engine(url)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """CSDL tạo từ phiên bản cũ: thêm các cột MỚI (luôn cho phép NULL) mà create_all không tự thêm vào bảng đã có."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or not col.nullable:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None or _engine is None:  # engine bị đặt lại (đổi CSDL) → phiên cũng tạo lại theo CSDL mới
        init_db()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def now() -> datetime:
    """Giờ hiện tại theo Asia/Bangkok, không kèm tzinfo để lưu SQLite."""
    return datetime.now(TZ).replace(tzinfo=None)
