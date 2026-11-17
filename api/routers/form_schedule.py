"""Màn Lịch gửi form: ai nhận form gì, vì sao; ai không nhận; xuất SMS; đề xuất module; nhắc lại."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.deps import CurrentUser, require
from api.util import XLSX, file_response
from core import audit
from core.ai.question_selector import selector_client
from core.channels import get_sms_channel
from core.config import TZ, load_forms, load_rules, work_dir
from core.db import now, session_scope
from core.forms import (
    build_states,
    due_reminders,
    export_form_sms,
    module_call_list,
    no_reply_call_list,
    precompute_selections,
    save_plan,
    send_reminders,
)
from core.models import Appointment, Patient
from core.patients import (
    auto_apply_form_plans,
    note_evidence,
    pending_plan_items,
    record_plan_declines,
)
from core.repo import stage_text
from core.rules.form_scheduler import FormDecision, ga_text, plan_forms, schedule_text
from core.rules.personalize import SOURCE_VI

router = APIRouter(prefix="/api/form-schedule", tags=["form-schedule"])


def _decision(d: FormDecision, merged: list[FormDecision] | None = None) -> dict:
    form = " + ".join([d.form_code or "", *[m.form_code or "" for m in merged or []]])
    reason = "; ".join([d.reason, *[m.reason for m in merged or []]])
    return {"code": d.code, "ga": ga_text(d.ga), "form": form, "kind": d.kind, "reason": reason,
            "skip_reason": d.skip_reason or "", "gap_days": d.gap_days,
            "last_visit": d.last_visit.isoformat() if d.last_visit else None,
            "next_appt": d.next_appt.isoformat() if d.next_appt else None}


def _today():
    return datetime.now(TZ).date()


@router.get("")
def overview(user: CurrentUser = Depends(require("form_schedule"))) -> dict:
    rules = load_rules()
    fc = rules["forms"]
    today = _today()
    with session_scope() as s:
        # Xem trước GIỐNG lúc xuất SMS (xuất áp dụng bộ câu có căn cứ bác sĩ trước rồi mới lập kế hoạch): áp dụng tạm
        # trong giao dịch này rồi HOÀN TÁC ở cuối – màn xem không để lại thay đổi nào (không SMS, không gọi AI).
        auto = auto_apply_form_plans(s, today, rules, load_forms())
        decisions = plan_forms(build_states(s, today, rules), today, rules)
        proposals = _proposals(s, rules, today)
        reminders = len(due_reminders(s, now(), fc["no_reply_reminder_hours"]))
        call_list = [{"code": p.code, "forms": codes, "why": "Bỏ 2 lần báo module liên tiếp"}
                     for p, codes in module_call_list(s, now(), fc["no_reply_reminder_hours"])]
        call_list += [{"code": p.code, "forms": codes, "why": "Không trả lời form mốc dù đã nhắc (có nguy cơ)"}
                      for p, codes in no_reply_call_list(s, now(), rules)]
        s.rollback()  # hoàn tác phần áp dụng tạm ở trên – màn xem không ghi gì
    send = [d for d in decisions if d.send]
    skip = [d for d in decisions if not d.send and not d.merged_into]
    merged: dict[tuple[int, str | None], list[FormDecision]] = {}
    for d in decisions:
        if d.merged_into:
            merged.setdefault((d.patient_id, d.merged_into), []).append(d)
    covered = send + [d for v in merged.values() for d in v]  # form có câu được hỏi hôm nay (kể cả đã gộp)
    return {
        "today": today.isoformat(), "rules_version": rules.rules_version,
        "policy": {"min_gap_days": fc["min_gap_days"], "skip_if_visit_within_days": fc["skip_if_visit_within_days"],
                   "stop_routine_from_ga_weeks": fc["stop_routine_from_ga_weeks"], "resend_block_days": fc["resend_block_days"],
                   "postpartum_days": list(fc["postpartum_days"].values()), "reminder_hours": fc["no_reply_reminder_hours"],
                   # Lấy từ rules.yaml – giao diện không viết cứng lịch gửi (đổi cấu hình thì chữ tự đổi theo)
                   "resend_routine": bool(fc.get("resend_routine", True)),
                   "skip_if_visited_within_days": fc.get("skip_if_visited_within_days"),
                   "module_schedule": {m: schedule_text(m, c) for m, c in fc["modules"].items() if m.startswith("M-")},
                   "insulin_schedule": schedule_text("M-DTD", fc["modules"].get("M-DTD", {}), True),
                   "preeclampsia_schedule": schedule_text("M-THA", fc["modules"].get("M-THA", {}), False, ["PREECLAMPSIA"]),
                   "pack_mid_gap_days": (fc.get("packs") or {}).get("mid_gap_days")},
        "counts": {"routine": sum(d.kind == "routine" for d in covered), "m_tha": sum(d.form_code == "M-THA" for d in covered),
                   "m_dtd": sum(d.form_code == "M-DTD" for d in covered),
                   "packs": sum((d.form_code or "").startswith("P-") for d in covered),
                   "postpartum": sum(d.kind == "postpartum" for d in covered),
                   "links": len(send), "merged": len(covered) - len(send),
                   "skip": len(skip), "proposals": len(proposals)},
        "send": [_decision(d, merged.get((d.patient_id, d.form_code))) for d in send],
        "skip": [_decision(d) for d in skip],
        "proposals": proposals, "reminders_due": reminders, "call_list": call_list,
        "auto_pending": [{"code": c, "packs": a} for c, a in auto],
    }


def _store(name: str, data: bytes) -> str:
    out = work_dir("exports")
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(data)
    return name


@router.post("/export")
def export(user: CurrentUser = Depends(require("export_form_sms"))) -> dict:
    rules, forms_cfg = load_rules(), load_forms()
    today = _today()
    # 1) Giao dịch ngắn: áp dụng bộ câu có căn cứ bác sĩ + lưu kế hoạch gửi hôm nay (chốt ngay).
    with session_scope() as s:
        auto_apply_form_plans(s, today, rules, forms_cfg)
        decisions = plan_forms(build_states(s, today, rules), today, rules)
        if not any(d.send for d in decisions):
            raise HTTPException(409, "Hôm nay không có form cần gửi.")
        save_plan(s, decisions, today, rules.rules_version)
    # 2) CHỈ ĐỌC, không giữ khóa ghi: chọn câu (có thể gọi AI vài phút).
    selector = selector_client(rules)
    with session_scope() as s:
        pre = precompute_selections(s, today, rules, forms_cfg, selector)
    # 3) Giao dịch ngắn: tạo đường dẫn, gửi SMS. Lỗi ở đây → kế hoạch (bước 1) vẫn còn, bấm xuất lại được.
    with session_scope() as s:
        r = export_form_sms(s, today, rules, forms_cfg, get_sms_channel(rules), user.user_id,
                            precomputed=pre, selector=selector)
    name = _store(r.file_path.name if r.file_path else f"gui_form_{today.isoformat()}.xlsx", r.file_bytes) if r.file_bytes else None
    return {"channel": r.channel, "ok": r.n_ok, "blocked": r.n_blocked, "file": name}


@router.post("/reminders")
def reminders(user: CurrentUser = Depends(require("export_form_sms"))) -> dict:
    rules, forms_cfg = load_rules(), load_forms()
    with session_scope() as s:
        r = send_reminders(s, rules, forms_cfg, get_sms_channel(rules), user.user_id)
    name = _store(r.file_path.name if r.file_path else f"nhac_{_today().isoformat()}.xlsx", r.file_bytes) if r.file_bytes else None
    return {"ok": r.n_ok, "file": name}


@router.get("/file/{name}")
def download(name: str, user: CurrentUser = Depends(require("export_form_sms"))):
    from api.util import safe_name

    path = work_dir("exports") / safe_name(name)
    if not path.is_file() or path.suffix != ".xlsx":
        raise HTTPException(404, "Không có file.")
    audit.log("download_export", "file", path.name, user_id=user.user_id)
    return file_response(path.read_bytes(), path.name, XLSX)


def _new_items(s, p: Patient, rules, today) -> tuple[list[str], list[str]]:  # type: ignore[no-untyped-def]
    """(tên nhóm nguy cơ, mã module / bộ câu đang chờ điều dưỡng xác nhận) – tự nhận từ hồ sơ + AI (docs/04 B3b)."""
    plan, items = pending_plan_items(s, p, today, rules, load_forms())
    names = {f.flag: f.name for f in plan.flags}
    return sorted({names.get(f, f) for i in items for f in i.flags}), [i.code for i in items]


def _evidence(s, p: Patient, flag, rules) -> list[dict]:  # type: ignore[no-untyped-def]
    """Căn cứ của một nhóm nguy cơ: nguồn (chẩn đoán / nhóm nguy cơ / tiền sử / AI đọc ghi chú) + nội dung để kiểm.
    Nguồn AI: kèm câu ghi chú gốc ĐÃ CHE, ngày hẹn, độ tin cậy."""
    out = []
    for e in flag.evidence:
        row = {"source": e.source, "label": SOURCE_VI.get(e.source, e.source), "text": e.text}
        if e.source == "ai_note":
            found = note_evidence(s, p.id, e.text)
            row["text"] = found["note"] if found and found["note"] else f"AI gắn cờ {e.text} (không còn ghi chú gốc)"
            row |= {"date": found["date"], "confidence": found["confidence"], "model": found["model"]} if found else {}
        out.append(row)
    return out


def _last_next(s, p: Patient, today) -> tuple[str | None, str | None]:  # type: ignore[no-untyped-def]
    last = s.scalar(select(func.max(Appointment.appt_date)).where(Appointment.patient_id == p.id,
                                                                 Appointment.appt_date < today))
    nxt = s.scalar(select(func.min(Appointment.appt_date)).where(Appointment.patient_id == p.id,
                                                                Appointment.appt_date >= today))
    return (last.isoformat() if last else None), (nxt.isoformat() if nxt else None)


def _proposals(s, rules, today) -> list[dict]:  # type: ignore[no-untyped-def]
    """Đề xuất TỰ ĐỘNG hiện mỗi ngày: thai phụ đã đồng ý có module / bộ câu hỏi mới nhận ra từ chẩn đoán ICD-10,
    nhóm nguy cơ, tiền sử hoặc AI đọc ghi chú – chưa được điều dưỡng xác nhận, chưa bị gỡ / từ chối.
    Mỗi đề xuất kèm hồ sơ tóm tắt và CĂN CỨ (nguồn + nội dung) để điều dưỡng kiểm trước khi xác nhận."""
    forms_cfg = load_forms()
    module_names = {c: str(forms_cfg.forms.get(c, {}).get("name", c)) for c in rules["forms"]["modules"]}
    out = []
    for p in s.scalars(select(Patient).where(Patient.consent_status == "granted", Patient.edd.is_not(None),
                                             Patient.delivery_date.is_(None))):
        plan, items = pending_plan_items(s, p, today, rules, forms_cfg)
        if not items:
            continue
        flags = {f.flag: f for f in plan.flags}
        last, nxt = _last_next(s, p, today)
        out.append({
            "patient_id": p.id, "code": p.code, "stage": stage_text(p, today), "last_visit": last, "next_appt": nxt,
            "current": [module_names.get(c, c) for c in p.risk_groups or []],
            "flags": sorted({flags[f].name for i in items for f in i.flags if f in flags}),
            "modules": [i.code for i in items],
            "items": [{"code": i.code, "name": i.name, "kind": i.kind, "schedule": i.schedule,
                       "n_questions": len(i.questions),
                       "flags": [{"flag": f, "name": flags[f].name, "evidence": _evidence(s, p, flags[f], rules)}
                                 for f in i.flags if f in flags]} for i in items],
        })
    return out


class ModuleIn(BaseModel):
    insulin: bool = False
    # Mã điều dưỡng đã xem trên màn hình. None (client cũ) = mọi mục đang chờ.
    codes: list[str] | None = None


class DeclineIn(BaseModel):
    codes: list[str]
    reason: str = Field(min_length=3, max_length=300)


def _chosen(s, p: Patient | None, codes: list[str] | None, rules) -> list[str]:  # type: ignore[no-untyped-def]
    """Mã đang chờ ∩ mã điều dưỡng chọn. Không còn mục nào (đề xuất đã đổi / đã xử lý) → 409 để tải lại."""
    if p is None:
        raise HTTPException(404, "Không tìm thấy thai phụ.")
    pending = _new_items(s, p, rules, _today())[1]
    chosen = pending if codes is None else [c for c in pending if c in set(codes)]
    if not chosen:
        raise HTTPException(409, "Đề xuất đã thay đổi hoặc đã được xử lý – tải lại trang.")
    return chosen


@router.post("/proposals/{patient_id}/confirm")
def confirm_module(patient_id: int, body: ModuleIn, user: CurrentUser = Depends(require("form_schedule"))) -> dict:
    """Điều dưỡng xác nhận ĐÚNG các mục đã xem (body.codes) – không nhận thêm mục mới phát sinh sau khi tải trang."""
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        rules = load_rules()
        mods = _chosen(s, p, body.codes, rules)
        p.risk_groups = sorted(set(p.risk_groups or []) | set(mods))
        p.on_insulin = bool(body.insulin) if "M-DTD" in mods else bool(p.on_insulin)
        audit.log("confirm_module", "patient", p.code, {"confirmed": mods, "modules": p.risk_groups,
                                                         "insulin": p.on_insulin, "rules_version": rules.rules_version},
                  user_id=user.user_id, session=s)
    return {"ok": True, "confirmed": mods}


@router.post("/proposals/{patient_id}/decline")
def decline_module(patient_id: int, body: DeclineIn, user: CurrentUser = Depends(require("form_schedule"))) -> dict:
    """Điều dưỡng không đồng ý đề xuất (kèm lý do): không đề xuất lại trong LẦN MANG THAI này (như gỡ ở Hồ sơ)."""
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        rules = load_rules()
        codes = _chosen(s, p, body.codes, rules)
        current = list(p.risk_groups or [])
        record_plan_declines(s, p, sorted(set(current) | set(codes)), current, user.user_id, now())
        audit.log("decline_module", "patient", p.code, {"declined": codes, "reason": body.reason.strip(),
                                                         "rules_version": rules.rules_version},
                  user_id=user.user_id, session=s)
    return {"ok": True, "declined": codes}
