"""Đồng thuận tham gia nhận form (US-F2). Rút đồng thuận → dừng gửi ngay."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core import audit
from core.db import now
from core.models import FormSchedule, FormToken, Patient


def grant(s: Session, patient_id: int, user_id: int | None, note: str = "") -> None:
    p = s.get(Patient, patient_id)
    assert p is not None
    p.consent_status = "granted"
    p.consent_at = now()
    p.consent_by = user_id
    audit.log("consent_grant", "patient", p.code, {"note": note}, user_id=user_id, session=s)


def withdraw(s: Session, patient_id: int, user_id: int | None, note: str = "") -> int:
    """Rút đồng thuận: hủy lịch gửi chưa xuất và vô hiệu đường dẫn form chưa dùng."""
    p = s.get(Patient, patient_id)
    assert p is not None
    p.consent_status = "withdrawn"
    p.consent_at = now()
    p.consent_by = user_id
    cancelled = s.execute(
        update(FormSchedule)
        .where(FormSchedule.patient_id == patient_id, FormSchedule.status == "planned")
        .values(status="cancelled", skip_reason="Rút đồng thuận")
    ).rowcount
    for t in s.scalars(select(FormToken).where(FormToken.patient_id == patient_id, FormToken.used_at.is_(None))):
        t.expires_at = now()
    audit.log("consent_withdraw", "patient", p.code, {"cancelled": cancelled, "note": note},
              user_id=user_id, session=s)
    return cancelled
