"""Lưu / đọc dữ liệu nghiệp vụ trong CSDL. Mã hóa trường nhạy cảm khi lưu."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import audit
from core.db import now
from core.models import (
    Appointment,
    ArrivalPlan,
    ImportBatch,
    NoshowPrediction,
    NoteExtraction,
    Patient,
    User,
)
from core.pipeline import PlannedAppt, ProcessResult
from core.security import AppUser, decrypt, encrypt


def ensure_user(s: Session, u: AppUser) -> int:
    row = s.scalar(select(User).where(User.username == u.username))
    if row is None:
        try:
            with s.begin_nested():  # hai lần đăng nhập đầu tiên cùng lúc → một bên thắng, bên kia đọc lại
                row = User(username=u.username, name=u.name, role=u.role)
                s.add(row)
                s.flush()
        except IntegrityError:
            row = s.scalar(select(User).where(User.username == u.username))
            assert row is not None
    return row.id


def next_code(s: Session) -> str:
    n = s.scalar(select(func.count(Patient.id))) or 0
    code = f"R{n + 1:04d}"
    while s.scalar(select(Patient.id).where(Patient.code == code)) is not None:
        n += 1
        code = f"R{n + 1:04d}"
    return code


def upsert_patient(s: Session, pid: str, full_name: str, phone: str, nationality: str) -> Patient:
    p = s.scalar(select(Patient).where(Patient.pid == pid))
    if p is None:
        p = Patient(pid=pid, full_name_enc=encrypt(full_name) or "", phone_enc=encrypt(phone) or "",
                    nationality=nationality, code=next_code(s), risk_groups=[], proposed_risk_flags=[])
        s.add(p)
        s.flush()
    else:
        # File HIS là nguồn đúng: cập nhật họ tên / SĐT / quốc tịch theo lần tải mới nhất (tin nhắn dùng tên này).
        if full_name and full_name != (decrypt(p.full_name_enc) or ""):
            p.full_name_enc = encrypt(full_name) or p.full_name_enc
            from core import audit

            audit.log("patient_name_updated_from_his", "patient", p.code, {}, session=s)  # không ghi tên vào nhật ký
        if phone:
            p.phone_enc = encrypt(phone) or p.phone_enc
        p.nationality = nationality or p.nationality
    return p


def patient_name(p: Patient) -> str:
    return decrypt(p.full_name_enc) or ""


def patient_phone(p: Patient) -> str:
    return decrypt(p.phone_enc) or ""


def ga_on(p: Patient, d: date) -> tuple[int, int] | None:
    """Tuần thai tại ngày d. Đã sinh (d ≥ delivery_date) → None, không còn tuần thai."""
    if p.edd is None:
        return None
    if p.delivery_date is not None and d >= p.delivery_date:
        return None
    total = 280 - (p.edd - d).days
    if total < 0:
        return None
    return total // 7, total % 7


def stage_text(p: Patient, d: date) -> str:
    """Nhãn hiển thị: '32+1' khi mang thai, 'Hậu sản 5 ngày' sau sinh, '—' nếu không rõ."""
    if p.delivery_date is not None and d >= p.delivery_date:
        return f"Hậu sản {(d - p.delivery_date).days} ngày"
    ga = ga_on(p, d)
    return f"{ga[0]}+{ga[1]}" if ga else "—"


RISK_TO_MODULE = {"HTN": "M-THA", "PREECLAMPSIA": "M-THA", "GDM": "M-DTD"}


def _update_patient_from_note(p: Patient, plan: PlannedAppt) -> dict[str, Any]:
    info: dict[str, Any] = {}
    w, d = plan.decision.ga_weeks, plan.decision.ga_days
    if w is not None:
        appt_d = plan.merged.appt_date
        expected = ga_on(p, appt_d)
        expected_w = expected[0] + expected[1] / 7 if expected else None
        from core.ai.note_extractor import ga_inconsistent

        if ga_inconsistent(w, expected_w):
            info["ga_inconsistent"] = True
        else:
            p.edd = appt_d + timedelta(days=280 - (w * 7 + (d or 0)))
    ex = plan.extraction
    if ex and ex.ai and ex.ai.risk_flags:
        flags = sorted(set(p.proposed_risk_flags or []) | set(ex.ai.risk_flags))
        p.proposed_risk_flags = flags
    return info


def save_his_result(
    s: Session, res: ProcessResult, file_name: str, target_date: date, user_id: int | None
) -> int:
    batch = ImportBatch(kind="his", file_name=file_name, row_count=len(res.rows),
                        errors=res.report.as_dicts(), target_date=target_date, created_by=user_id,
                        created_at=now())
    s.add(batch)
    s.flush()
    # Thay lịch của đúng những ngày có trong file (không theo ngày đang chọn trên màn hình): chọn nhầm ngày
    # thì không xóa lịch của ngày khác, tải lại cùng file thì không bị trùng hai bộ.
    days = {plan.merged.appt_date for plan in res.planned} or {target_date}
    old_ids = s.scalars(select(Appointment.id).where(Appointment.appt_date.in_(days))).all()
    if old_ids:
        s.execute(delete(ArrivalPlan).where(ArrivalPlan.appointment_id.in_(old_ids)))
        s.execute(delete(NoteExtraction).where(NoteExtraction.appointment_id.in_(old_ids)))
        s.execute(delete(NoshowPrediction).where(NoshowPrediction.appointment_id.in_(old_ids)))
        s.execute(delete(Appointment).where(Appointment.id.in_(old_ids)))
    for plan in res.planned:
        m = plan.merged
        p = upsert_patient(s, m.pid, m.full_name, m.phone or m.phone_raw, m.nationality)
        extra = _update_patient_from_note(p, plan)
        a = Appointment(patient_id=p.id, appt_date=m.appt_date, start_time=m.start_time, end_time=m.end_time,
                        services=m.services, department=m.department, doctor=m.doctor,
                        note_enc=encrypt(m.note) if m.note else None, booking_source=m.booking_source,
                        booked_at=m.booked_at, batch_id=batch.id, merged_from=m.merged_from,
                        row_warnings=m.warnings)
        s.add(a)
        s.flush()
        ex = plan.extraction
        if m.note:
            ai = ex.ai if ex else None
            s.add(NoteExtraction(
                appointment_id=a.id,
                ga_weeks=plan.decision.ga_weeks, ga_days=plan.decision.ga_days,
                risk_flags=ai.risk_flags if ai else [],
                arrival_hint=(plan.arrival.ai_time.strftime("%H:%M") if plan.arrival.ai_time else None),
                votes=ex.votes if ex else [], confidence=ex.confidence if ex else None,
                model=ex.model if ex else None, prompt_version=ex.prompt_version if ex else None,
                rules_version=res.rules_version,
                masked_note=plan.mask.text if plan.mask else None,
                raw={**(ex.raw if ex else {}), **extra,
                     "mask_ok": plan.mask.ok if plan.mask else True,
                     "mask_leftovers_count": len(plan.mask.leftovers) if plan.mask else 0,
                     "ai": _ai_dict(ai)},
            ))
        s.add(ArrivalPlan(
            appointment_id=a.id, arrival_time=plan.arrival.arrival_time, proposed_time=plan.arrival.arrival_time,
            method="waiting" if plan.arrival.waiting else plan.arrival.method,
            confidence=ex.confidence if ex else None,
            status=plan.decision.status, reasons=plan.decision.reasons, rules_version=res.rules_version,
            details={
                "rule_applied": plan.arrival.rule_applied,
                "ai_status": plan.ai_status,
                "rule_time": _t(plan.arrival.rule_time),
                "rule_minutes": plan.arrival.rule_minutes,
                "explicit_time": _t(plan.arrival.explicit_time),
                "ai_time": _t(plan.arrival.ai_time),
                "notes": plan.arrival.details,
                "conflict_detail": m.conflict_detail,
                "merged_from": m.merged_from,
            },
        ))
    audit.log("upload_his", "import_batch", batch.id,
              {"file": file_name, "rows": len(res.rows), "planned": len(res.planned),
               "auto_rate": round(res.auto_rate, 3), "rules_version": res.rules_version},
              user_id=user_id, session=s)
    return batch.id


def _t(t: time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


def _ai_dict(ai: Any) -> dict[str, Any] | None:
    if ai is None:
        return None
    return {
        "explicit_arrival_time": _t(ai.explicit_arrival_time), "minutes_early": ai.minutes_early,
        "needs_fasting": ai.needs_fasting, "ga_weeks": ai.ga_weeks, "ga_days": ai.ga_days,
        "risk_flags": ai.risk_flags, "asks_confirmation": ai.asks_confirmation, "evidence": ai.evidence,
    }


@dataclass
class PlanView:
    plan_id: int
    appointment_id: int
    patient_id: int
    code: str
    pid: str
    full_name: str
    phone: str
    nationality: str
    appt_date: date
    start_time: time
    services: list[str]
    note: str
    masked_note: str | None
    arrival_time: time | None
    proposed_time: time | None
    method: str
    confidence: float | None
    status: str
    reasons: list[str]
    details: dict[str, Any]
    merged_from: list[int]
    extraction: dict[str, Any] | None
    warnings: list[str]


def plans_for_date(s: Session, d: date) -> list[PlanView]:
    q = (
        select(ArrivalPlan, Appointment, Patient)
        .join(Appointment, ArrivalPlan.appointment_id == Appointment.id)
        .join(Patient, Appointment.patient_id == Patient.id)
        .where(Appointment.appt_date == d)
        .order_by(Appointment.start_time, Patient.pid)
    )
    out = []
    for plan, a, p in s.execute(q).all():
        ne = s.scalar(select(NoteExtraction).where(NoteExtraction.appointment_id == a.id))
        out.append(PlanView(
            plan_id=plan.id, appointment_id=a.id, patient_id=p.id, code=p.code or "", pid=p.pid,
            full_name=patient_name(p), phone=patient_phone(p), nationality=p.nationality or "",
            appt_date=a.appt_date, start_time=a.start_time, services=list(a.services or []),
            note=decrypt(a.note_enc) or "", masked_note=ne.masked_note if ne else None,
            arrival_time=plan.arrival_time, proposed_time=plan.proposed_time, method=plan.method,
            confidence=plan.confidence, status=plan.status, reasons=list(plan.reasons or []),
            details=dict(plan.details or {}), merged_from=list(a.merged_from or []),
            extraction=({"ga_weeks": ne.ga_weeks, "ga_days": ne.ga_days, "risk_flags": ne.risk_flags,
                         "confidence": ne.confidence, "model": ne.model, "prompt_version": ne.prompt_version,
                         "ai": (ne.raw or {}).get("ai"), "votes": ne.votes,
                         "ga_inconsistent": (ne.raw or {}).get("ga_inconsistent", False)} if ne else None),
            warnings=list(a.row_warnings or []),
        ))
    return out


def confirm_plan(s: Session, plan_id: int, arrival: time | None, user_id: int | None, note: str = "") -> None:
    """Xác nhận giờ đến. arrival=None → giữ là "khám chờ" (chỉ cho lịch khám chờ)."""
    plan = s.get(ArrivalPlan, plan_id)
    assert plan is not None
    if arrival is None and plan.method != "waiting":
        raise ValueError("Chỉ lịch khám chờ mới được xác nhận không có giờ đến.")
    old = plan.arrival_time
    changed = old != arrival
    plan.arrival_time = arrival
    plan.status = "confirmed"
    if changed:
        plan.method = "manual"
    plan.confirmed_by = user_id
    plan.confirmed_at = now()
    audit.log("confirm_arrival", "arrival_plan", plan_id,
              {"old": _t(old), "new": _t(arrival), "proposed": _t(plan.proposed_time), "changed": changed,
               "reasons": plan.reasons, "note": note},
              user_id=user_id, session=s)

