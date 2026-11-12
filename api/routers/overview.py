"""Màn Tổng quan: số liệu trong ngày, các bước việc, cảnh báo mới nhất."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from api.deps import CurrentUser, current_user
from core.alerts import STATUS_VI
from core.config import TZ, llm_provider, load_rules
from core.db import now, session_scope
from core.models import (
    Alert,
    Appointment,
    ArrivalPlan,
    Assessment,
    AuditLog,
    FormResponse,
    FormSchedule,
    Patient,
)
from core.security import can

router = APIRouter(prefix="/api", tags=["overview"])


def shift_label(h: int) -> str:
    return "Ca sáng" if 6 <= h < 12 else "Ca chiều" if 12 <= h < 18 else "Ca tối"


def _llm_info() -> dict[str, str]:
    """Loại AI đang chạy – để giao diện ghi đúng (trước đây ghi cứng "Ollama")."""
    rules = load_rules()
    try:
        provider = llm_provider(rules)
    except ValueError:
        return {"provider": "blocked", "model": ""}
    model = {"ollama": rules["llm"]["model"],
             "openai": os.environ.get("OPENAI_MODEL") or rules["llm"].get("openai_model", "")}.get(provider, "mock")
    return {"provider": provider, "model": model}


@router.get("/overview")
def overview(user: CurrentUser = Depends(current_user)) -> dict:
    today = datetime.now(TZ).date()
    tmr = today + timedelta(days=1)
    since = now() - timedelta(hours=24)
    with session_scope() as s:
        n_appt = s.scalar(select(func.count(Appointment.id)).where(Appointment.appt_date == tmr)) or 0
        n_review = s.scalar(
            select(func.count(ArrivalPlan.id)).join(Appointment, ArrivalPlan.appointment_id == Appointment.id)
            .where(Appointment.appt_date == tmr, ArrivalPlan.status == "needs_review")
        ) or 0
        n_forms = s.scalar(select(func.count(FormSchedule.id)).where(
            FormSchedule.send_date == today, FormSchedule.status == "exported")) or 0
        n_resp = s.scalar(select(func.count(FormResponse.id)).where(FormResponse.submitted_at >= since)) or 0
        n_open = s.scalar(select(func.count(Alert.id)).where(Alert.status == "open")) or 0
        n_red = s.scalar(select(func.count(Alert.id)).where(Alert.status == "open", Alert.level == "red")) or 0
        exported = s.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "export_worldsms", AuditLog.entity_id == tmr.isoformat())) or 0
        # Cảnh báo mới nhất có mã thai phụ + tóm tắt lâm sàng → chỉ vai trò được xem cảnh báo (admin thì không).
        latest = s.execute(
            select(Alert, Assessment, FormResponse, Patient)
            .join(Assessment, Alert.assessment_id == Assessment.id)
            .join(FormResponse, Assessment.response_id == FormResponse.id)
            .join(Patient, FormResponse.patient_id == Patient.id)
            .order_by(Alert.created_at.desc()).limit(5)
        ).all() if can(user.role, "view_alerts") else []
        latest_rows = [{"id": a.id, "level": a.level, "status": a.status, "status_label": STATUS_VI.get(a.status, a.status),
                        "code": p.code, "summary": (asm.summary or "")[:160],
                        "created_at": a.created_at.isoformat() if a.created_at else None}
                       for a, asm, _r, p in latest]

    steps = [
        {"key": "his", "label": "Tải file lịch hẹn HIS ngày mai", "done": n_appt > 0, "to": "/lich-hen"},
        {"key": "confirm", "label": "Xác nhận giờ đến", "done": n_appt > 0 and n_review == 0, "to": "/lich-hen",
         "pending": n_review if n_appt else None},
        {"key": "sms", "label": "Xuất 2 file WorldSMS", "done": exported > 0, "to": "/lich-hen"},
        {"key": "forms", "label": "Lịch gửi form hôm nay", "done": n_forms > 0, "to": "/lich-gui-form"},
        {"key": "alerts", "label": "Xử lý cảnh báo", "done": n_open == 0, "to": "/canh-bao"},
    ]
    return {
        "today": today.isoformat(), "tomorrow": tmr.isoformat(), "shift": shift_label(datetime.now(TZ).hour),
        "metrics": {"appointments_tomorrow": n_appt, "forms_today": n_forms, "responses_24h": n_resp,
                    "open_alerts": n_open, "open_red": n_red},
        "steps": steps, "latest_alerts": latest_rows, "llm": _llm_info(),
    }
