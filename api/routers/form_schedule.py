"""Màn Lịch gửi form: ai nhận form gì, vì sao; ai không nhận; xuất SMS; đề xuất module; nhắc lại."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import CurrentUser, require
from api.util import XLSX, file_response
from core import audit
from core.channels import get_sms_channel
from core.config import DATA_DIR, TZ, load_forms, load_rules
from core.db import now, session_scope
from core.forms import (
    build_states,
    due_reminders,
    export_form_sms,
    module_call_list,
    save_plan,
    send_reminders,
)
from core.models import Patient
from core.rules.form_scheduler import FormDecision, ga_text, plan_forms, suggested_modules

router = APIRouter(prefix="/api/form-schedule", tags=["form-schedule"])


def _decision(d: FormDecision) -> dict:
    return {"code": d.code, "ga": ga_text(d.ga), "form": d.form_code, "kind": d.kind, "reason": d.reason,
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
        decisions = plan_forms(build_states(s, today, rules), today, rules)
        proposals = [
            {"patient_id": p.id, "code": p.code, "flags": list(p.proposed_risk_flags or []),
             "modules": suggested_modules(p.proposed_risk_flags or []), "current": list(p.risk_groups or [])}
            for p in s.scalars(select(Patient).where(Patient.consent_status == "granted"))
            if p.proposed_risk_flags and set(suggested_modules(p.proposed_risk_flags)) - set(p.risk_groups or [])
        ]
        reminders = len(due_reminders(s, now(), fc["no_reply_reminder_hours"]))
        call_list = [{"code": p.code, "forms": codes} for p, codes in module_call_list(s, now(), fc["no_reply_reminder_hours"])]
    send = [d for d in decisions if d.send]
    skip = [d for d in decisions if not d.send]
    return {
        "today": today.isoformat(), "rules_version": rules.rules_version,
        "policy": {"min_gap_days": fc["min_gap_days"], "skip_if_visit_within_days": fc["skip_if_visit_within_days"],
                   "stop_routine_from_ga_weeks": fc["stop_routine_from_ga_weeks"], "resend_block_days": fc["resend_block_days"],
                   "postpartum_days": list(fc["postpartum_days"].values()), "reminder_hours": fc["no_reply_reminder_hours"]},
        "counts": {"routine": sum(d.kind == "routine" for d in send), "m_tha": sum(d.form_code == "M-THA" for d in send),
                   "m_dtd": sum(d.form_code == "M-DTD" for d in send), "postpartum": sum(d.kind == "postpartum" for d in send),
                   "skip": len(skip), "proposals": len(proposals)},
        "send": [_decision(d) for d in send], "skip": [_decision(d) for d in skip],
        "proposals": proposals, "reminders_due": reminders, "call_list": call_list,
    }


def _store(name: str, data: bytes) -> str:
    out = DATA_DIR / "exports"
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(data)
    return name


@router.post("/export")
def export(user: CurrentUser = Depends(require("export_form_sms"))) -> dict:
    rules, forms_cfg = load_rules(), load_forms()
    today = _today()
    with session_scope() as s:
        decisions = plan_forms(build_states(s, today, rules), today, rules)
        if not any(d.send for d in decisions):
            raise HTTPException(409, "Hôm nay không có form cần gửi.")
        save_plan(s, decisions, today, rules.rules_version)
        r = export_form_sms(s, today, rules, forms_cfg, get_sms_channel(rules), user.user_id)
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

    path = DATA_DIR / "exports" / safe_name(name)
    if not path.is_file() or path.suffix != ".xlsx":
        raise HTTPException(404, "Không có file.")
    audit.log("download_export", "file", path.name, user_id=user.user_id)
    return file_response(path.read_bytes(), path.name, XLSX)


class ModuleIn(BaseModel):
    insulin: bool = False


@router.post("/proposals/{patient_id}/confirm")
def confirm_module(patient_id: int, body: ModuleIn, user: CurrentUser = Depends(require("form_schedule"))) -> dict:
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None or not p.proposed_risk_flags:
            raise HTTPException(404, "Không có đề xuất cho thai phụ này.")
        mods = suggested_modules(p.proposed_risk_flags)
        p.risk_groups = sorted(set(p.risk_groups or []) | set(mods))
        p.on_insulin = bool(body.insulin) if "M-DTD" in mods else bool(p.on_insulin)
        audit.log("confirm_module", "patient", p.code, {"modules": p.risk_groups, "insulin": p.on_insulin},
                  user_id=user.user_id, session=s)
    return {"ok": True}
