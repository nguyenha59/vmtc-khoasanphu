"""Lịch gửi form trong CSDL: trạng thái thai phụ, token, xuất SMS, nhắc lại."""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from core import audit
from core.channels import record_send
from core.channels.base import SendResult, SmsChannel, SmsRow
from core.config import FormsConfig, Rules
from core.db import now
from core.models import Appointment, Arrival, FormResponse, FormSchedule, FormToken, Patient
from core.repo import patient_phone
from core.rules.form_scheduler import FormDecision, PatientState


def base_url() -> str:
    return os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def form_link(token: str) -> str:
    return f"{base_url()}/form?token={token}"


def build_states(s: Session, today: date) -> list[PatientState]:
    patients = s.scalars(select(Patient).where(Patient.consent_status == "granted")).all()
    ids = [p.id for p in patients]
    if not ids:
        return []
    last_visit = dict(s.execute(
        select(Appointment.patient_id, func.max(Appointment.appt_date))
        .where(Appointment.patient_id.in_(ids), Appointment.appt_date < today)
        .group_by(Appointment.patient_id)).all())
    # Ưu tiên ngày thực tế có đến khám nếu có danh sách khách đến
    pid_to_id = {p.pid: p.id for p in patients}
    for pid, d in s.execute(select(Arrival.pid, func.max(Arrival.visit_date))
                            .where(Arrival.pid.in_(list(pid_to_id)), Arrival.visit_date < today)
                            .group_by(Arrival.pid)).all():
        last_visit[pid_to_id[pid]] = d
    next_appt = dict(s.execute(
        select(Appointment.patient_id, func.min(Appointment.appt_date))
        .where(Appointment.patient_id.in_(ids), Appointment.appt_date >= today)
        .group_by(Appointment.patient_id)).all())
    sent: dict[int, list[tuple[str, date]]] = {}
    for pid_, code, d in s.execute(select(FormSchedule.patient_id, FormSchedule.form_code, FormSchedule.send_date)
                                   .where(FormSchedule.patient_id.in_(ids), FormSchedule.status == "exported")):
        sent.setdefault(pid_, []).append((code, d))
    answered: dict[int, set[str]] = {}
    for pid_, code in s.execute(select(FormResponse.patient_id, FormResponse.form_code)
                                .where(FormResponse.patient_id.in_(ids))):
        answered.setdefault(pid_, set()).add(code)
    return [
        PatientState(
            patient_id=p.id, code=p.code or "", consent=p.consent_status, edd=p.edd,
            delivery_date=p.delivery_date, last_visit=last_visit.get(p.id), next_appt=next_appt.get(p.id),
            modules=list(p.risk_groups or []), on_insulin=p.on_insulin, sent=sent.get(p.id, []),
            answered={c for c in answered.get(p.id, set()) if not c.startswith("M-")},
        )
        for p in patients
    ]


def save_plan(s: Session, decisions: list[FormDecision], today: date, rules_version: str) -> None:
    """Ghi lịch hôm nay (thay các dòng planned/skipped cũ của hôm nay)."""
    s.execute(delete(FormSchedule).where(FormSchedule.send_date == today,
                                         FormSchedule.status.in_(["planned", "skipped"])))
    exported = {(pid, code) for pid, code in s.execute(
        select(FormSchedule.patient_id, FormSchedule.form_code)
        .where(FormSchedule.send_date == today, FormSchedule.status == "exported"))}
    for d in decisions:
        if (d.patient_id, d.form_code) in exported:
            continue
        s.add(FormSchedule(patient_id=d.patient_id, send_date=today, form_code=d.form_code, reason=d.reason,
                           status="planned" if d.send else "skipped", skip_reason=d.skip_reason,
                           rules_version=rules_version))


DEMO_PREFIX = "demo_"


def is_demo_token(token: str | None) -> bool:
    """Đường dẫn do nhân viên tạo ở màn Form demo – câu trả lời ghi source='demo', không phải thai phụ gửi."""
    return bool(token) and str(token).startswith(DEMO_PREFIX)


def create_token(s: Session, patient_id: int, form_code: str, ttl_days: int, *, demo: bool = False) -> str:
    token = (DEMO_PREFIX if demo else "") + secrets.token_urlsafe(16)  # 22 ký tự ngẫu nhiên (+5 nếu demo; cột 32)
    t0 = now()
    s.add(FormToken(token=token, patient_id=patient_id, form_code=form_code, created_at=t0,
                    expires_at=t0 + timedelta(days=ttl_days)))
    return token


def check_token(s: Session, token: str) -> tuple[FormToken | None, str | None]:
    t = s.get(FormToken, token) if token else None
    if t is None:
        return None, "Đường dẫn không hợp lệ. Vui lòng dùng đúng đường dẫn trong tin nhắn."
    if t.used_at is not None:
        return t, "Form này đã được gửi trước đó. Cảm ơn chị!"
    if t.expires_at < now():
        return t, "Đường dẫn đã hết hạn. Nếu có dấu hiệu bất thường, chị hãy gọi cho khoa."
    p = s.get(Patient, t.patient_id)
    if p is None or p.consent_status != "granted":
        return t, "Chị đã ngừng tham gia theo dõi qua form."
    return t, None


def export_form_sms(s: Session, today: date, rules: Rules, forms_cfg: FormsConfig, channel: SmsChannel,
                    user_id: int | None) -> SendResult:
    rows = s.scalars(select(FormSchedule).where(FormSchedule.send_date == today,
                                                FormSchedule.status == "planned")).all()
    sms: list[SmsRow] = []
    ttl = rules["forms"]["token_ttl_days"]
    for fs in rows:
        p = s.get(Patient, fs.patient_id)
        if p is None or p.consent_status != "granted":
            fs.status, fs.skip_reason = "cancelled", "Không còn đồng thuận"
            continue
        token = create_token(s, p.id, fs.form_code or "", ttl)
        fs.token = token
        fs.status = "exported"
        name = forms_cfg.forms.get(fs.form_code or "", {}).get("name", fs.form_code)
        sms.append(SmsRow(phone=patient_phone(p), patient_code=p.code or "",
                          params={"P": str(name), "P1": form_link(token),
                                  "P2": (today + timedelta(days=ttl)).strftime("%d/%m/%Y")}))
    result = channel.send_batch("gui_form", sms)
    record_send(s, result)
    audit.log("export_form_sms", "date", today.isoformat(),
              {"channel": result.channel, "rows": len(sms), "ok": result.n_ok, "blocked": result.n_blocked,
               "rules_version": rules.rules_version}, user_id=user_id, session=s)
    return result


def due_reminders(s: Session, at: datetime, hours: int) -> list[FormToken]:
    """Token chưa trả lời sau 48 giờ, còn hạn, chưa nhắc."""
    return list(s.scalars(select(FormToken).where(
        FormToken.used_at.is_(None), FormToken.reminded_at.is_(None),
        FormToken.created_at <= at - timedelta(hours=hours), FormToken.expires_at > at)))


def send_reminders(s: Session, rules: Rules, forms_cfg: FormsConfig, channel: SmsChannel,
                   user_id: int | None) -> SendResult:
    tokens = due_reminders(s, now(), rules["forms"]["no_reply_reminder_hours"])
    sms = []
    for t in tokens:
        p = s.get(Patient, t.patient_id)
        if p is None or p.consent_status != "granted":
            continue
        t.reminded_at = now()
        name = forms_cfg.forms.get(t.form_code, {}).get("name", t.form_code)
        sms.append(SmsRow(phone=patient_phone(p), patient_code=p.code or "",
                          params={"P": f"Nhắc: {name}", "P1": form_link(t.token),
                                  "P2": t.expires_at.strftime("%d/%m/%Y")}))
    result = channel.send_batch("nhac_form", sms)
    record_send(s, result)
    audit.log("send_form_reminders", "form_token", None, {"rows": len(sms)}, user_id=user_id, session=s)
    return result


def module_call_list(s: Session, at: datetime, hours: int) -> list[tuple[Patient, list[str]]]:
    """Thai phụ trong module bỏ 2 lần liên tiếp (không trả lời sau 48 giờ) → danh sách gọi."""
    out = []
    for p in s.scalars(select(Patient).where(Patient.consent_status == "granted")):
        if not p.risk_groups:
            continue
        toks = s.scalars(select(FormToken).where(FormToken.patient_id == p.id, FormToken.form_code.like("M-%"))
                         .order_by(FormToken.created_at.desc()).limit(2)).all()
        if len(toks) == 2 and all(t.used_at is None and t.created_at <= at - timedelta(hours=hours) for t in toks):
            out.append((p, [t.form_code for t in toks]))
    return out
