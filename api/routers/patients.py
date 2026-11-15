"""Quản lý hồ sơ thai phụ: danh sách, tạo hồ sơ (chọn theo gói), sửa chương trình theo dõi.

Phân quyền: xem danh sách = view_profile; tạo / sửa theo gói = manage_patient; chỉ định ngoài gói = override_monitoring.
Mọi thao tác ghi audit_log (không ghi tên / SĐT vào nhật ký).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.deps import CurrentUser, require
from core import audit
from core.ai.question_selector import ai_input_preview
from core.config import TZ, load_forms, load_rules
from core.db import session_scope
from core.forms import selection_context
from core.models import Appointment, Patient, PatientProgram
from core.patients import (
    NewPatient,
    PatientError,
    auto_apply_for,
    create_patient,
    form_plan_for,
    program_of,
    set_form_plan,
    set_program,
)
from core.repo import ga_on, patient_name, patient_phone, stage_text
from core.rules.form_scheduler import ga_at
from core.rules.personalize import plan_as_dict
from core.rules.question_select import as_dicts, select_questions
from core.security import can, mask_phone

router = APIRouter(prefix="/api/patients", tags=["patients"])


class CreateIn(BaseModel):
    pid: str = Field(max_length=20)
    full_name: str = Field(max_length=120)
    phone: str = Field(max_length=20)
    nationality: str = Field("Việt Nam", max_length=64)
    edd: date | None = None
    in_package: bool = False
    package_name: str | None = Field(None, max_length=128)


class ProgramIn(BaseModel):
    in_package: bool
    package_name: str | None = Field(None, max_length=128)
    override_monitoring: bool = False
    override_reason: str | None = Field(None, max_length=300)


def _today() -> date:
    return datetime.now(TZ).date()


@router.get("")
def list_patients(q: str = Query("", max_length=60), program: Literal["all", "package", "override", "none"] = "all",
                  page: int = Query(1, ge=1), size: int = Query(25, ge=5, le=100),
                  user: CurrentUser = Depends(require("view_profile"))) -> dict:
    """Danh sách hồ sơ thai phụ (đang mang thai hoặc sau sinh). Tìm theo mã, PID hoặc tên (tên mã hóa → so sau giải mã)."""
    today, key = _today(), q.strip().lower()
    with session_scope() as s:
        rows = s.execute(select(Patient, PatientProgram).outerjoin(PatientProgram, PatientProgram.patient_id == Patient.id)
                         .where(Patient.edd.is_not(None)).order_by(Patient.code)).all()
        next_appt = dict(s.execute(select(Appointment.patient_id, func.min(Appointment.appt_date))
                                   .where(Appointment.appt_date >= today).group_by(Appointment.patient_id)).all())
        items = []
        for p, pr in rows:
            in_pkg, ovr = bool(pr and pr.in_package), bool(pr and pr.override_monitoring)
            if program == "package" and not in_pkg or program == "override" and not ovr \
                    or program == "none" and (in_pkg or ovr):
                continue
            name = patient_name(p)
            if key and key not in (p.code or "").lower() and key not in p.pid.lower() and key not in name.lower():
                continue
            ga = ga_on(p, today)
            items.append({"patient_id": p.id, "code": p.code, "pid": p.pid, "full_name": name,
                          "phone_masked": mask_phone(patient_phone(p)),
                          "stage": f"Tuần {stage_text(p, today)}" if ga else stage_text(p, today),
                          "in_package": in_pkg, "package_name": pr.package_name if pr else None,
                          "override_monitoring": ovr, "consent": p.consent_status,
                          "next_appt": next_appt[p.id].isoformat() if p.id in next_appt else None})
        audit.log("view_patient_list", "patient", None, {"q": bool(key), "program": program, "n": len(items)},
                  user_id=user.user_id, session=s)
    total = len(items)
    start = (page - 1) * size
    return {"items": items[start:start + size], "total": total, "page": page, "size": size,
            "counts": {"package": sum(i["in_package"] for i in items)}}


@router.post("", status_code=201)
def create(body: CreateIn, user: CurrentUser = Depends(require("manage_patient"))) -> dict:
    with session_scope() as s:
        try:
            p = create_patient(s, NewPatient(**body.model_dump()), user.user_id, _today())
        except PatientError as e:
            raise HTTPException(409 if "đã có hồ sơ" in str(e) else 422, str(e)) from e
        audit.log("create_patient", "patient", p.code, {"in_package": body.in_package}, user_id=user.user_id, session=s)
        return {"patient_id": p.id, "code": p.code}


@router.put("/{patient_id}/program")
def update_program(patient_id: int, body: ProgramIn, user: CurrentUser = Depends(require("manage_patient"))) -> dict:
    with session_scope() as s:
        before = program_of(s, patient_id)
        try:
            after = set_program(s, patient_id, **body.model_dump(), user_id=user.user_id,
                                can_override=can(user.role, "override_monitoring"), today=_today())
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except PatientError as e:
            raise HTTPException(404 if "Không tìm thấy" in str(e) else 422, str(e)) from e
        code = s.scalar(select(Patient.code).where(Patient.id == patient_id))
        audit.log("update_patient_program", "patient", code,
                  {"before": {k: before[k] for k in ("in_package", "override_monitoring")},
                   "after": {k: after[k] for k in ("in_package", "override_monitoring")}},
                  user_id=user.user_id, session=s)
    return after


class FormPlanIn(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=20)
    insulin: bool = False


def _form_plan_payload(s, p: Patient, rules, forms_cfg) -> dict:  # type: ignore[no-untyped-def]
    today = _today()
    plan = form_plan_for(s, p, today, rules, forms_cfg)
    ga = ga_at(p.edd, today)
    window = next((c for c, w in rules["forms"]["windows"].items()
                   if ga is not None and tuple(w["from"]) <= ga <= tuple(w["to"])), None)
    preview, ai_input = None, None
    if window and p.delivery_date is None:
        # Xem trước bằng điểm QUY TẮC (không gọi AI khi chỉ mở hồ sơ). AI chấm thêm lúc tạo đường dẫn.
        ctx = selection_context(s, p, today, rules, forms_cfg)
        chosen = select_questions([window], ctx, rules, forms_cfg)
        ai_input = ai_input_preview([window], ctx, rules, forms_cfg)
        base = set(forms_cfg.forms[window]["questions"])
        preview = {"form": window, "name": forms_cfg.forms[window]["name"], "method": "rules",
                   "questions": [{**q, "added": q["code"] not in base} for q in as_dicts(chosen, forms_cfg)]}
    return {**plan_as_dict(plan), "confirmed": list(p.risk_groups or []), "insulin": bool(p.on_insulin),
            "question_text": {c: q["text"] for c, q in forms_cfg.questions.items()},
            "preview": preview, "ai_input": ai_input, "rules_version": rules.rules_version}


@router.get("/{patient_id}/form-plan")
def get_form_plan(patient_id: int, user: CurrentUser = Depends(require("view_profile"))) -> dict:
    """Bộ câu hỏi cá nhân hóa: đề xuất theo hồ sơ + AI, câu bị cổng chất lượng loại, form định kỳ sắp tới."""
    rules, forms_cfg = load_rules(), load_forms()
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None:
            raise HTTPException(404, "Không tìm thấy thai phụ.")
        auto_apply_for(s, p, _today(), rules, forms_cfg)  # căn cứ bác sĩ ghi → áp dụng ngay, màn hình khớp quy tắc
        out = _form_plan_payload(s, p, rules, forms_cfg)
        audit.log("view_form_plan", "patient", p.code, {"rules_version": rules.rules_version},
                  user_id=user.user_id, session=s)
    return {**out, "can_confirm": can(user.role, "form_schedule")}


@router.put("/{patient_id}/form-plan")
def confirm_form_plan(patient_id: int, body: FormPlanIn, user: CurrentUser = Depends(require("form_schedule"))) -> dict:
    """Điều dưỡng xác nhận bộ câu hỏi cho thai phụ (người dùng chốt 13/11/2026)."""
    rules, forms_cfg = load_rules(), load_forms()
    with session_scope() as s:
        p = s.get(Patient, patient_id)
        if p is None:
            raise HTTPException(404, "Không tìm thấy thai phụ.")
        try:
            before, after = set_form_plan(p, body.codes, body.insulin, rules)
        except PatientError as e:
            raise HTTPException(422, str(e)) from e
        audit.log("confirm_form_plan", "patient", p.code,
                  {"before": before, "after": after, "insulin": bool(p.on_insulin), "rules_version": rules.rules_version},
                  user_id=user.user_id, session=s)
        s.flush()
        out = _form_plan_payload(s, p, rules, forms_cfg)
    return {**out, "can_confirm": True}
