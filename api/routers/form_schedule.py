"""Màn Lịch gửi form: ai nhận form gì, vì sao; ai không nhận; xuất SMS; đề xuất module; nhắc lại."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

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
from core.models import Patient
from core.patients import auto_apply_form_plans, form_plan_for
from core.rules.form_scheduler import FormDecision, ga_text, plan_forms, schedule_text

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
    """(nhóm nguy cơ, mã module / bộ câu MỚI cần điều dưỡng xác nhận) – tự nhận từ hồ sơ + AI (docs/04 B3b)."""
    plan = form_plan_for(s, p, today, rules, load_forms())
    new = [i.code for i in plan.items if not i.confirmed and i.questions]
    names = {f.flag: f.name for f in plan.flags}
    flags = sorted({names.get(f, f) for i in plan.items if i.code in new for f in i.flags})
    return flags, new


def _proposals(s, rules, today) -> list[dict]:  # type: ignore[no-untyped-def]
    """Đề xuất TỰ ĐỘNG hiện mỗi ngày: thai phụ đã đồng ý có module / bộ câu hỏi mới nhận ra từ chẩn đoán ICD-10,
    nhóm nguy cơ, tiền sử hoặc AI đọc ghi chú – chưa được điều dưỡng xác nhận."""
    out = []
    for p in s.scalars(select(Patient).where(Patient.consent_status == "granted", Patient.edd.is_not(None),
                                             Patient.delivery_date.is_(None))):
        flags, new = _new_items(s, p, rules, today)
        if new:
            out.append({"patient_id": p.id, "code": p.code, "flags": flags, "modules": new,
                        "current": list(p.risk_groups or [])})
    return out


class ModuleIn(BaseModel):
    insulin: bool = False


@router.post("/proposals/{patient_id}/confirm")
def confirm_module(patient_id: int, body: ModuleIn, user: CurrentUser = Depends(require("form_schedule"))) -> dict:
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        rules = load_rules()
        mods = _new_items(s, p, rules, _today())[1] if p is not None else []
        if p is None or not mods:
            raise HTTPException(404, "Không có đề xuất cho thai phụ này.")
        p.risk_groups = sorted(set(p.risk_groups or []) | set(mods))
        p.on_insulin = bool(body.insulin) if "M-DTD" in mods else bool(p.on_insulin)
        audit.log("confirm_module", "patient", p.code, {"modules": p.risk_groups, "insulin": p.on_insulin},
                  user_id=user.user_id, session=s)
    return {"ok": True}
