"""Lịch gửi form trong CSDL: trạng thái thai phụ, token, xuất SMS, nhắc lại."""

from __future__ import annotations

import os
import secrets
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from core import audit
from core.channels import record_send
from core.channels.base import SendResult, SmsChannel, SmsRow
from core.config import FormsConfig, Rules
from core.db import now
from core.models import (
    Appointment,
    Arrival,
    FormResponse,
    FormSchedule,
    FormToken,
    Patient,
    PatientProgram,
)
from core.repo import patient_phone
from core.rules.form_scheduler import FormDecision, PatientState


def base_url() -> str:
    return os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def form_link(token: str) -> str:
    return f"{base_url()}/form?token={token}"


def build_states(s: Session, today: date, rules: Rules | None = None) -> list[PatientState]:
    patients = s.scalars(select(Patient).where(Patient.consent_status == "granted")).all()
    if rules is not None and (rules.get("forms.eligibility") or {}).get("package_only"):
        # Chỉ khách theo gói hoặc bác sĩ chỉ định ngoài gói (core.patients.monitoring_eligible).
        progs = {pr.patient_id: pr for pr in s.scalars(
            select(PatientProgram).where(PatientProgram.patient_id.in_([p.id for p in patients])))}
        patients = [p for p in patients if (pr := progs.get(p.id)) is not None and (pr.in_package or pr.override_monitoring)]
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
    flags: dict[int, list[str]] = {}
    if rules is not None:  # nhóm nguy cơ chỉ cần cho module có lịch theo nhóm (vd. tiền sản giật → M-THA 3 lần/tuần)
        from core.patients import diagnoses_of, history_of
        from core.rules.personalize import find_flags

        for p in patients:
            if "M-THA" in (p.risk_groups or []):
                flags[p.id] = [f.flag for f in find_flags(history_of(s, p.id), diagnoses_of(s, p.id),
                                                          list(p.proposed_risk_flags or []), rules)]
    return [
        PatientState(
            patient_id=p.id, code=p.code or "", consent=p.consent_status, edd=p.edd, flags=flags.get(p.id, []),
            delivery_date=p.delivery_date, last_visit=last_visit.get(p.id), next_appt=next_appt.get(p.id),
            modules=list(p.risk_groups or []), on_insulin=p.on_insulin, sent=sent.get(p.id, []),
            answered={c for c in answered.get(p.id, set()) if not c.startswith(("M-", "P-"))},
        )
        for p in patients
    ]


def save_plan(s: Session, decisions: list[FormDecision], today: date, rules_version: str) -> None:
    """Ghi lịch hôm nay (thay các dòng planned/skipped cũ của hôm nay)."""
    s.execute(delete(FormSchedule).where(FormSchedule.send_date == today,
                                         FormSchedule.status.in_(["planned", "skipped", "merged"])))
    exported = {(pid, code) for pid, code in s.execute(
        select(FormSchedule.patient_id, FormSchedule.form_code)
        .where(FormSchedule.send_date == today, FormSchedule.status == "exported"))}
    for d in decisions:
        if (d.patient_id, d.form_code) in exported:
            continue
        status = "planned" if d.send else ("merged" if d.merged_into else "skipped")
        s.add(FormSchedule(patient_id=d.patient_id, send_date=today, form_code=d.form_code, reason=d.reason,
                           status=status, skip_reason=d.skip_reason,
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


def token_form_codes(s: Session, t: FormToken) -> list[str]:
    """Các form trong một đường dẫn: form chính + form gộp cùng ngày (cùng token)."""
    merged = s.scalars(select(FormSchedule.form_code).where(FormSchedule.token == t.token,
                                                            FormSchedule.form_code != t.form_code)).all()
    return [t.form_code, *[c for c in merged if c]]


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
    from core.ai.question_selector import selector_client

    selector = selector_client(rules) if rows else None  # AI chấm điểm chọn câu (None = chỉ quy tắc)
    for fs in rows:
        p = s.get(Patient, fs.patient_id)
        if p is None or p.consent_status != "granted":
            fs.status, fs.skip_reason = "cancelled", "Không còn đồng thuận"
            continue
        token = create_token(s, p.id, fs.form_code or "", ttl)
        fs.token = token
        fs.status = "exported"
        # Form gộp cùng ngày dùng chung đường dẫn này (1 SMS) và vẫn tính là đã gửi cho nhịp gửi lần sau.
        merged = s.scalars(select(FormSchedule).where(FormSchedule.patient_id == p.id, FormSchedule.send_date == today,
                                                      FormSchedule.status == "merged")).all()
        for m in merged:
            m.token, m.status = token, "exported"
        snapshot_selection(s, p, token, [fs.form_code or "", *[m.form_code or "" for m in merged]], rules, forms_cfg,
                           selector)
        name = " + ".join(str(forms_cfg.forms.get(c or "", {}).get("name", c))
                          for c in [fs.form_code, *[m.form_code for m in merged]])
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
    """Thai phụ trong module bỏ 2 lần liên tiếp (không trả lời sau 48 giờ) → danh sách gọi.
    Tính cả đường dẫn gộp có module (form chính là F… nhưng có M-… gộp vào)."""
    out = []
    for p in s.scalars(select(Patient).where(Patient.consent_status == "granted")):
        if not any(c.startswith("M-") for c in (p.risk_groups or [])):
            continue
        toks = [t for t in s.scalars(select(FormToken).where(FormToken.patient_id == p.id,
                                                             ~FormToken.token.like(f"{DEMO_PREFIX}%"))
                                     .order_by(FormToken.created_at.desc()).limit(10))
                if any(c.startswith("M-") for c in token_form_codes(s, t))][:2]
        if len(toks) == 2 and all(t.used_at is None and t.created_at <= at - timedelta(hours=hours) for t in toks):
            out.append((p, ["+".join(token_form_codes(s, t)) for t in toks]))
    return out


def no_reply_call_list(s: Session, at: datetime, rules: Rules, days: int = 7) -> list[tuple[Patient, list[str]]]:
    """docs/13: form mốc / bộ theo tình trạng KHÔNG trả lời dù đã nhắc, đã hết hạn (trong N ngày gần đây),
    thai phụ có nguy cơ (đã có module / bộ theo tình trạng) → điều dưỡng gọi. Nguy cơ thấp: chỉ ghi nhận."""
    if not rules.get("forms.no_reply_call_if_risk", False):
        return []
    out = []
    for t in s.scalars(select(FormToken).where(FormToken.used_at.is_(None), FormToken.reminded_at.is_not(None),
                                               FormToken.expires_at < at, FormToken.expires_at >= at - timedelta(days=days),
                                               ~FormToken.token.like(f"{DEMO_PREFIX}%"))):
        codes = token_form_codes(s, t)
        if not any(c in rules["forms"]["windows"] or c.startswith("P-") for c in codes):
            continue
        p = s.get(Patient, t.patient_id)
        if p is not None and p.consent_status == "granted" and p.risk_groups:
            out.append((p, ["+".join(codes)]))
    return out


# ---------------------------------------------------------------- chọn câu hỏi cá nhân hóa (docs/13 mục 5c)
def selection_context(s: Session, p: Patient, today: date, rules: Rules, forms_cfg: FormsConfig,
                      *, as_of: datetime | None = None) -> Any:
    """Ngữ cảnh KHÔNG định danh để chọn câu: tuần thai, nhóm nguy cơ, câu trả lời bất thường + phần tự viết gần đây."""
    from core.models import Assessment
    from core.patients import form_plan_for, pregnancy_start
    from core.repo import ga_on
    from core.rules.ai_context import context_as_of
    from core.rules.question_select import Context

    until = context_as_of(today, as_of)
    plan = form_plan_for(s, p, today, rules, forms_cfg, as_of=until)
    days = int((rules.get("personalization.selection") or {}).get("recent_answer_days", 30))
    since = datetime.combine(today, datetime.min.time()) - timedelta(days=days)
    if (start := pregnancy_start(p)) is not None:
        since = max(since, datetime.combine(start, datetime.min.time()))
    responses = s.scalars(select(FormResponse).where(FormResponse.patient_id == p.id,
                         FormResponse.source != "demo", FormResponse.submitted_at >= since,
                         FormResponse.submitted_at <= until)
                         .order_by(FormResponse.submitted_at.desc(), FormResponse.id.desc())).all()
    flagged: dict[str, str] = {}
    concepts: list[str] = []
    seen_questions: set[str] = set()
    seen_concepts: set[str] = set()
    for response in responses:
        newer_questions = set(seen_questions)
        for code, value in (response.answers or {}).items():
            if forms_cfg.questions.get(code, {}).get("type") != "yesno" or code in seen_questions:
                continue
            # An omitted question is not an answer resolving that concern.
            if str(value) not in ("Có", "Không chắc", "Không"):
                continue
            seen_questions.add(code)
            if str(value) in ("Có", "Không chắc"):
                flagged[code] = str(value)
        asm = s.scalar(select(Assessment).where(Assessment.response_id == response.id))
        for f in ((asm.findings or {}).get("ai", []) if asm else []):
            code = str(f.get("concept", ""))
            question = (rules.get("personalization.concept_questions") or {}).get(code)
            if code in seen_concepts or (question in newer_questions and question not in flagged):
                continue
            seen_concepts.add(code)
            if f.get("present") and f.get("experiencer") == "patient" and f.get("temporality") != "past_resolved":
                concepts.append(code)
    return Context(ga=ga_on(p, today), flags=[f.flag for f in plan.flags], confirmed=list(p.risk_groups or []),
                   flagged_answers=flagged, recent_concepts=list(dict.fromkeys(concepts)),
                   clinical=clinical_context(s, p, today, rules, as_of=until),
                   postpartum=p.delivery_date is not None and p.delivery_date <= today)


def clinical_context(s: Session, p: Patient, today: date, rules: Rules,
                     *, as_of: datetime | None = None) -> dict[str, Any]:
    """Gom số đo (tại nhà + tại khoa), xử trí lần khám gần nhất, tiền sử, lịch khám → tóm tắt đã phân loại."""
    from core.models import Appointment, VitalReading
    from core.patients import history_of, pregnancy_start, visit_results
    from core.rules.ai_context import clinical_summary, context_as_of

    until = context_as_of(today, as_of)
    start = pregnancy_start(p)
    readings: dict[str, list[tuple[datetime, float]]] = {}
    real_responses = select(FormResponse.id).where(FormResponse.source != "demo")
    query = select(VitalReading).where(VitalReading.patient_id == p.id, VitalReading.measured_at <= until,
                  or_(VitalReading.source_response_id.is_(None), VitalReading.source_response_id.in_(real_responses)))
    if start is not None:
        query = query.where(VitalReading.measured_at >= datetime.combine(start, datetime.min.time()))
    for v in s.scalars(query):
        readings.setdefault(v.kind, []).append((v.measured_at, float(v.value)))
    appointments = select(Appointment).where(Appointment.patient_id == p.id)
    if start is not None:
        appointments = appointments.where(Appointment.appt_date >= start)
    appts = list(s.scalars(appointments.order_by(Appointment.appt_date, Appointment.start_time, Appointment.id)))
    results = visit_results(s, [a.id for a in appts if datetime.combine(a.appt_date, a.start_time) <= until], as_of=until)
    visited = [a for a in appts if a.id in results]
    lab_kinds: dict[str, str] = ((rules.get("personalization") or {}).get("ai_context") or {}).get("lab_kinds", {})
    lab_labels: dict[str, str] = {}
    measurement_days = int((rules.get("personalization.ai_context") or {}).get("measurement_days", 14))
    for a in visited:  # huyết áp đo tại khoa
        vit = results[a.id].get("vitals") or {}
        at = datetime.combine(a.appt_date, a.start_time)
        for kind in ("bp_sys", "bp_dia"):
            if isinstance(vit.get(kind), (int, float)):
                readings.setdefault(kind, []).append((at, float(vit[kind])))
        for lab in results[a.id].get("labs") or []:  # đường huyết xét nghiệm tại khoa
            code = str(lab.get("code", ""))
            if code.startswith("OGTT_"):
                if (until.date() - a.appt_date).days <= measurement_days:
                    flag = str(lab.get("flag", ""))
                    reference = lab.get("ref")
                    numeric_ref = (isinstance(reference, list) and len(reference) == 2
                                   and all(v is None or isinstance(v, (int, float)) for v in reference))
                    state = {"H": "cao", "L": "thấp"}.get(
                        flag, "trong khoảng tham chiếu" if not flag and numeric_ref else "chưa phân loại")
                    ref_label = ""
                    if numeric_ref:
                        from core.clinical import ref_text

                        ref_label = f"; khoảng tham chiếu {ref_text(reference)}"
                    unit = str(lab.get("unit", ""))
                    unit_label = f" ({unit})" if unit in ("mmol/L", "mg/dL") else ""
                    lab_labels[code] = f"NPDN 75 g {code}{unit_label}: {state} theo phòng xét nghiệm{ref_label}"
                continue  # Preserve test context and LIS flag; never apply home postmeal targets to OGTT.
            kind = lab_kinds.get(code)
            if kind and isinstance(lab.get("value"), (int, float)):
                if lab.get("unit") != "mmol/L":
                    if (until.date() - a.appt_date).days <= measurement_days:
                        lab_labels[code] = f"Xét nghiệm {code}: chưa phân loại — cần xác nhận đơn vị mmol/L"
                    continue
                readings.setdefault(kind, []).append((at, float(lab["value"])))
    last = visited[-1] if visited else None
    nxt = next((a.appt_date for a in appts if a.appt_date > today and a.id not in results), None)
    return clinical_summary(readings=readings, on_insulin=bool(p.on_insulin),
                            latest_plan=list(results[last.id].get("plan") or []) if last else [],
                             history=history_of(s, p.id, as_of=until), last_visit=last.appt_date if last else None,
                             next_visit=nxt, today=today, rules=rules, as_of=until, lab_labels=list(lab_labels.values()))


def snapshot_selection(s: Session, p: Patient, token: str, form_codes: list[str], rules: Rules,
                       forms_cfg: FormsConfig, client: Any = None) -> Any:
    """Chọn + xếp câu cho đường dẫn và LƯU lại (AI nếu có, lỗi → quy tắc). Ghi audit."""
    from core.ai.question_selector import ai_scores
    from core.models import QuestionSelection
    from core.rules.question_select import as_dicts, select_questions

    ctx = selection_context(s, p, now().date(), rules, forms_cfg)
    ai = ai_scores(form_codes, ctx, rules, forms_cfg, client)
    chosen = select_questions(form_codes, ctx, rules, forms_cfg, ai.scores if ai.ok else None)
    row = QuestionSelection(token=token, patient_id=p.id, created_at=now(), method="ai" if ai.ok else "rules",
                            questions=as_dicts(chosen, forms_cfg), model=ai.model, prompt_version=ai.prompt_version,
                            rules_version=rules.rules_version, confidence=ai.confidence, raw=ai.raw, error=ai.error)
    s.add(row)
    audit.log("select_questions", "patient", p.code,
              {"forms": form_codes, "n": len(chosen), "method": row.method, "model": ai.model,
               "rules_version": rules.rules_version}, session=s)
    return row


def selection_for(s: Session, token: str) -> list[dict[str, Any]] | None:
    from core.models import QuestionSelection

    row = s.get(QuestionSelection, token)
    return list(row.questions) if row is not None else None
