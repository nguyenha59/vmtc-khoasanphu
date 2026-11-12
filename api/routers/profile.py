"""Màn Hồ sơ theo dõi: tóm tắt giữa hai lần khám, biểu đồ, nhật ký, đồng thuận, in tóm tắt."""

from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from api.charts import series_for
from api.deps import CurrentUser, require
from api.util import file_response
from core import audit, consent
from core.alerts import FINAL_LEVELS, STATUS_VI, history_for
from core.config import TZ, load_rules
from core.db import session_scope
from core.models import Alert, Appointment, Assessment, FormResponse, FormToken, Patient
from core.repo import ga_on, patient_phone, stage_text
from core.reports import profile_html, profile_pdf
from core.security import mask_phone

router = APIRouter(prefix="/api/profile", tags=["profile"])
CONSENT_VI = {"none": "Chưa ghi nhận", "granted": "Đã đồng ý", "withdrawn": "Đã rút đồng ý"}


def _find(s: Any, q: str) -> Patient:
    p = s.scalar(select(Patient).where(or_(Patient.code == q.strip(), Patient.pid == q.strip())))
    if p is None:
        raise HTTPException(404, "Không tìm thấy thai phụ. Kiểm tra lại mã R#### hoặc PID.")
    return p


@router.get("/recent")
def recent(user: CurrentUser = Depends(require("view_profile"))) -> dict:
    with session_scope() as s:
        rows = s.execute(select(Patient.code).join(FormResponse, FormResponse.patient_id == Patient.id)
                         .group_by(Patient.code).order_by(func.max(FormResponse.submitted_at).desc()).limit(8)).all()
    return {"codes": [c for (c,) in rows]}


def _build(s: Any, p: Patient) -> dict[str, Any]:
    today = datetime.now(TZ).date()
    pid_ = p.id
    ga = ga_on(p, today)
    stage = stage_text(p, today)
    last_visit = s.scalar(select(func.max(Appointment.appt_date)).where(Appointment.patient_id == pid_, Appointment.appt_date < today))
    next_appt = s.scalar(select(func.min(Appointment.appt_date)).where(Appointment.patient_id == pid_, Appointment.appt_date >= today))
    since = datetime.combine(last_visit, time(0)) if last_visit else datetime(2000, 1, 1)
    responses = s.execute(select(FormResponse, Assessment).join(Assessment, Assessment.response_id == FormResponse.id)
                          .where(FormResponse.patient_id == pid_).order_by(FormResponse.submitted_at.desc())).all()
    resp_rows = [{"at": r.submitted_at, "form": r.form_code, "level": a.level, "summary": (a.summary or "")[:160]}
                 for r, a in responses]
    alerts = s.execute(select(Alert).join(Assessment, Alert.assessment_id == Assessment.id)
                       .join(FormResponse, Assessment.response_id == FormResponse.id)
                       .where(FormResponse.patient_id == pid_).order_by(Alert.created_at.desc())).scalars().all()
    alert_rows = [{"id": a.id, "at": a.created_at.isoformat() if a.created_at else None, "level": a.level,
                   "status": STATUS_VI.get(a.status, a.status), "final_level": FINAL_LEVELS.get(a.final_level or "", ""),
                   "note": a.note or ""} for a in alerts]
    sent = s.scalar(select(func.count()).select_from(FormToken).where(FormToken.patient_id == pid_, FormToken.created_at >= since)) or 0
    answered = s.scalar(select(func.count()).select_from(FormToken).where(
        FormToken.patient_id == pid_, FormToken.created_at >= since, FormToken.used_at.is_not(None))) or 0
    hist = history_for(s, pid_, days=90)

    period_bp = [v for t, v in hist.get("bp_sys", []) if t >= since]
    period_dia = [v for t, v in hist.get("bp_dia", []) if t >= since]
    weights = hist.get("weight", [])
    period_resp = [r for r in resp_rows if r["at"] >= since]
    n_red = sum(r["level"] == "red" for r in period_resp)
    n_yel = sum(r["level"] == "yellow" for r in period_resp)
    lines = [
        f"Khoảng theo dõi: {last_visit:%d/%m/%Y} → hôm nay" if last_visit else "Khoảng theo dõi: từ khi tham gia",
        f"Lần hẹn tới: {next_appt:%d/%m/%Y}" if next_appt else "Chưa có lịch hẹn tới",
        f"{len(period_resp)} câu trả lời form; {n_red} đỏ, {n_yel} vàng.",
        (f"Huyết áp tâm thu trung bình {statistics.fmean(period_bp):.0f}, cao nhất {max(period_bp):.0f} mmHg."
         if period_bp else "Chưa có số đo huyết áp trong khoảng này."),
    ]
    if period_resp:
        lines.append(f"Đánh giá gần nhất: {period_resp[0]['summary']}")
    return {
        "patient_id": pid_, "code": p.code, "stage": ("tuần thai " + stage) if ga else stage,
        "phone_masked": mask_phone(patient_phone(p)), "risk_groups": list(p.risk_groups or []), "insulin": bool(p.on_insulin),
        "consent": {"status": p.consent_status, "label": CONSENT_VI.get(p.consent_status, p.consent_status),
                    "at": p.consent_at.isoformat() if p.consent_at else None},
        "last_visit": last_visit.isoformat() if last_visit else None, "next_appt": next_appt.isoformat() if next_appt else None,
        "delivery_date": p.delivery_date.isoformat() if p.delivery_date else None,
        "metrics": {"bp_mean": round(statistics.fmean(period_bp)) if period_bp else None, "bp_n": len(period_bp),
                    "bp_max": f"{max(period_bp):.0f}/{max(period_dia):.0f}" if period_bp and period_dia else None,
                    "weight": round(weights[-1][1], 1) if weights else None,
                    "reply_rate": answered / sent if sent else None, "answered": answered, "sent": sent},
        "summary_lines": lines,
        "responses": [{**r, "at": r["at"].isoformat()} for r in resp_rows],
        "period_table": [{"Thời điểm": r["at"].strftime("%d/%m %H:%M"), "Form": r["form"], "Mức": r["level"],
                          "Tóm tắt": r["summary"][:120]} for r in period_resp[:20]],
        "alerts": alert_rows,
        "charts": series_for(hist, load_rules()),
    }


@router.get("")
def profile(q: str, user: CurrentUser = Depends(require("view_profile"))) -> dict:
    with session_scope() as s:
        p = _find(s, q)
        out = _build(s, p)
        audit.log("view_profile", "patient", p.code, user_id=user.user_id, session=s)
    out.pop("period_table")
    return out


class ConsentIn(BaseModel):
    note: str = ""


@router.post("/{patient_id}/consent/grant")
def grant(patient_id: int, body: ConsentIn, user: CurrentUser = Depends(require("manage_consent"))) -> dict:
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None:
            raise HTTPException(404, "Không tìm thấy thai phụ.")
        if p.consent_status == "granted":
            raise HTTPException(409, "Thai phụ đã đồng ý.")
        consent.grant(s, patient_id, user.user_id, body.note)
    return {"ok": True}


@router.post("/{patient_id}/consent/withdraw")
def withdraw(patient_id: int, body: ConsentIn, user: CurrentUser = Depends(require("manage_consent"))) -> dict:
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None:
            raise HTTPException(404, "Không tìm thấy thai phụ.")
        if p.consent_status != "granted":
            raise HTTPException(409, "Thai phụ chưa đồng ý nên không rút được.")
        n = consent.withdraw(s, patient_id, user.user_id, body.note)
    return {"ok": True, "cancelled": n}


class DeliveryIn(BaseModel):
    delivery_date: date


@router.post("/{patient_id}/delivery")
def record_delivery(patient_id: int, body: DeliveryIn, user: CurrentUser = Depends(require("record_delivery"))) -> dict:
    """Ghi ngày sinh → thai phụ chuyển sang theo dõi sau sinh (form S1–S4), dừng form thai kỳ."""
    today = datetime.now(TZ).date()
    if body.delivery_date > today:
        raise HTTPException(422, "Ngày sinh không được ở tương lai.")
    if body.delivery_date < today - timedelta(days=365):
        raise HTTPException(422, "Ngày sinh quá xa (hơn 1 năm).")
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None:
            raise HTTPException(404, "Không tìm thấy thai phụ.")
        old = p.delivery_date
        p.delivery_date = body.delivery_date
        audit.log("record_delivery", "patient", p.code, {"old": old.isoformat() if old else None,
                                                         "new": body.delivery_date.isoformat()},
                  user_id=user.user_id, session=s)
    return {"ok": True}


@router.get("/{patient_id}/print")
def print_summary(patient_id: int, user: CurrentUser = Depends(require("view_profile"))):
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None:
            raise HTTPException(404, "Không tìm thấy thai phụ.")
        d = _build(s, p)
        audit.log("print_profile", "patient", p.code, user_id=user.user_id, session=s)
    title = f"Tóm tắt theo dõi {d['code']} – {d['stage']}"
    pdf = profile_pdf(title, d["summary_lines"], d["period_table"])
    if pdf is not None:
        return file_response(pdf, f"tom_tat_{d['code']}.pdf", "application/pdf")
    return file_response(profile_html(title, d["summary_lines"], d["period_table"]), f"tom_tat_{d['code']}.html", "text/html")
