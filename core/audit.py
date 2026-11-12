"""Nhật ký kiểm toán. Chỉ ghi thêm, không có hàm xóa."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.db import now, session_scope
from core.models import AuditLog


def log(
    action: str,
    entity: str | None = None,
    entity_id: object = None,
    details: dict[str, Any] | None = None,
    *,
    user_id: int | None = None,
    session: Session | None = None,
) -> None:
    entry = AuditLog(
        ts=now(),
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=None if entity_id is None else str(entity_id),
        details=details or {},
    )
    if session is not None:
        session.add(entry)
        return
    with session_scope() as s:
        s.add(entry)
