"""Màn Lịch hẹn: tải file HIS, kiểm tra, giờ đến, xác nhận, xuất WorldSMS."""

from __future__ import annotations

import os
import re
import secrets
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select

from api.deps import CurrentUser, require
from api.util import file_response, jsonable, safe_name
from core import audit
from core.ai.llm_client import can_send_external, make_client_safe
from core.config import (
    DATA_DIR,
    TZ,
    llm_provider,
    load_his_columns,
    load_rules,
    ollama_model,
    parse_hhmm,
    work_dir,
)
from core.db import now, session_scope
from core.ingest.his_reader import read_raw
from core.models import Appointment, ImportBatch
from core.pipeline import process_his
from core.repo import PlanView, confirm_plan, plans_for_date, save_his_result
from core.rules.arrival import waiting_label
from core.rules.export_worldsms import (
    ExportLocked,
    ExportRow,
    build_exports,
    invalid_phone_rows,
    write_exports,
)
from core.rules.review import reason_label
from core.security import mask_phone

router = APIRouter(prefix="/api/appointments", tags=["appointments"])
AI_STATUS_LABELS = {
    "no_note": "Không gọi AI – lịch hẹn không có ghi chú.",
    "off": "Không gọi AI – lần tải này chỉ dùng quy tắc (tắt AI hoặc không kết nối được mô hình).",
    "rules_read": "Không gọi AI – quy tắc đã đọc hiểu hết ghi chú (chỉ gồm từ quen, giờ, tuần thai).",
    "sensitive": "Không gửi AI – ghi chú có nội dung nhạy cảm, không gửi ra API bên ngoài.",
    "mask_failed": "Không gửi AI – che dữ liệu cá nhân chưa sạch.",
    "failed": "Đã gọi AI nhưng không nhận được kết quả hợp lệ (lỗi kết nối, hết ngân sách hoặc AI trả sai).",
    "ok": "",
}
METHOD_VI = {"rule": "Quy tắc", "ai": "AI", "manual": "Người sửa", "waiting": "Khám chờ"}
STATUS_VI = {"auto": "Tự điền", "needs_review": "Cần xác nhận", "confirmed": "Đã xác nhận"}


def _waiting() -> str:
    return waiting_label(load_rules()["arrival"]) or "Khám chờ"


def _default_date() -> date:
    from datetime import datetime

    return datetime.now(TZ).date() + timedelta(days=1)


def _row(p: PlanView, waiting: str) -> dict:
    return {
        "plan_id": p.plan_id, "code": p.code, "full_name": p.full_name, "phone_masked": mask_phone(p.phone),
        "nationality": p.nationality, "start_time": p.start_time.strftime("%H:%M"), "services": p.services,
        "arrival_time": p.arrival_time.strftime("%H:%M") if p.arrival_time else None,
        "arrival_label": p.arrival_time.strftime("%H:%M") if p.arrival_time else (waiting if p.method == "waiting" else "—"),
        "method": p.method, "method_label": METHOD_VI.get(p.method, p.method),
        "status": p.status, "status_label": STATUS_VI.get(p.status, p.status),
        "reasons": [{"code": r, "label": reason_label(r, load_rules())} for r in p.reasons],
        "merged": len(p.merged_from) > 1,
        # File OH: loại khám + dịch vụ suy ra từ ghi chú, kèm căn cứ (config/visit_services.yaml)
        "visit_types": p.details.get("visit_types") or [],
        "planned_services": p.details.get("planned_services") or [],
        "basis": p.details.get("basis") or [],
        "fasting": bool(p.details.get("fasting")),
    }


_WORLDSMS_FILE = re.compile(r"^nhac_hen_(?:VN|NN)_(\d{4}-\d{2}-\d{2})\.xlsx$")


def _stale_export(s: Any, d: date, path: Path, plans: list[PlanView] | None = None) -> str | None:
    """File WorldSMS của ngày d còn đúng với lịch HIỆN TẠI không. Lịch đang có ca cần xác nhận, hoặc file tạo trước
    lần nhập HIS mới nhất của ngày đó → file cũ (giờ hẹn / giờ đến có thể đã đổi), không đưa ra để gửi."""
    rows = _export_rows(plans if plans is not None else plans_for_date(s, d))
    if any(r.status == "needs_review" or (r.arrival_time is None and not r.waiting) for r in rows):
        return "Lịch ngày này đang có ca cần xác nhận – file đã xuất trước đó không còn đúng. Xác nhận rồi xuất lại."
    latest = s.scalar(select(func.max(ImportBatch.created_at)).join(Appointment, Appointment.batch_id == ImportBatch.id)
                      .where(Appointment.appt_date == d))
    made = datetime.fromtimestamp(path.stat().st_mtime, TZ).replace(tzinfo=None)
    if latest is not None and made < latest:
        return "File được tạo trước lần nhập HIS mới nhất của ngày này – xuất lại file mới."
    return None


def _export_rows(plans: list[PlanView]) -> list[ExportRow]:
    return [ExportRow(phone=p.phone, full_name=p.full_name, pid=p.pid, appt_date=p.appt_date, start_time=p.start_time,
                      arrival_time=p.arrival_time, status=p.status, nationality=p.nationality,
                      waiting=p.method == "waiting" and p.arrival_time is None) for p in plans]


@router.get("")
def list_plans(d: date | None = None, user: CurrentUser = Depends(require("upload_his"))) -> dict:
    d = d or _default_date()
    rules = load_rules()
    waiting = _waiting()
    export_dir = work_dir("exports")
    with session_scope() as s:
        plans = plans_for_date(s, d)
        # Chỉ đưa ra file còn khớp lịch hiện tại (file cũ sau khi nhập lại HIS / còn ca cần xác nhận bị ẩn).
        exports = sorted(p.name for p in export_dir.glob(f"*{d.isoformat()}*.xlsx")
                         if not (_WORLDSMS_FILE.match(p.name) and _stale_export(s, d, p, plans)))             if export_dir.exists() else []
    bad = invalid_phone_rows(_export_rows(plans))
    provider = llm_provider(rules)
    model = "mock"
    if provider in ("ollama", "hybrid"):
        model = ollama_model(rules)
    if provider in ("openai", "hybrid"):
        cloud_model = os.environ.get("OPENAI_MODEL") or rules.get("llm.openai_model")
        model = f"{cloud_model} / {model}" if provider == "hybrid" else cloud_model
    return {
        "date": d.isoformat(), "waiting_label": waiting,
        "items": [_row(p, waiting) for p in plans],
        "counts": {"total": len(plans), "need": sum(p.status == "needs_review" for p in plans),
                   "auto": sum(p.status != "needs_review" for p in plans),
                   "merged": sum(len(p.merged_from) > 1 for p in plans)},
        "invalid_phones": [r.pid for r in bad],
        "exports": exports,
        "samples": [p.name for p in sorted((DATA_DIR / "synthetic").glob("his_*.xlsx"))],
        "llm": {"provider": provider, "model": model},
    }


@router.get("/plan/{plan_id}")
def plan_detail(plan_id: int, d: date, user: CurrentUser = Depends(require("upload_his"))) -> dict:
    waiting = _waiting()
    with session_scope() as s:
        p = next(iter(plans_for_date(s, d, plan_id)), None)
        if p is None:
            raise HTTPException(404, "Không tìm thấy lịch hẹn.")
        audit.log("view_appointment", "arrival_plan", plan_id, user_id=user.user_id, session=s)
    ex = p.extraction or {}
    return {
        **_row(p, waiting), "pid": p.pid, "merged_from": p.merged_from,
        "conflict_detail": p.details.get("conflict_detail", ""), "masked_note": p.masked_note or "",
        "rule_applied": p.details.get("rule_applied") or "", "notes": p.details.get("notes", []),
        "ai_status": p.details.get("ai_status"),
        "ai_status_label": AI_STATUS_LABELS.get(p.details.get("ai_status") or "", "Không gọi AI."),
        "warnings": p.warnings, "ai": jsonable(ex.get("ai")), "confidence": ex.get("confidence"),
        "model": ex.get("model"), "prompt_version": ex.get("prompt_version"), "ga_inconsistent": bool(ex.get("ga_inconsistent")),
        "default_time": (p.arrival_time or p.start_time).strftime("%H:%M"),
    }


@router.post("/upload")
async def upload(d: date = Form(...), use_ai: bool = Form(True), sample: str | None = Form(None),
                 file: UploadFile | None = File(None), user: CurrentUser = Depends(require("upload_his"))) -> dict:
    rules = load_rules()
    if file is not None and file.filename:
        fname, content = safe_name(file.filename), await file.read()
    elif sample:
        path = DATA_DIR / "synthetic" / safe_name(sample)
        if not path.is_file() or not path.name.startswith("his_"):
            raise HTTPException(404, "Không có file mẫu này.")
        fname, content = path.name, path.read_bytes()
    else:
        raise HTTPException(422, "Chọn file HIS hoặc file mẫu.")
    from core.safe_files import UnsafeFile, check_xlsx_bytes

    try:
        check_xlsx_bytes(content)
    except UnsafeFile as e:
        audit.log("upload_his_unsafe", "file", fname, {"reason": str(e)}, user_id=user.user_id)
        raise HTTPException(422, str(e)) from e
    uploads = work_dir("uploads")
    uploads.mkdir(parents=True, exist_ok=True)
    # Thêm chuỗi ngẫu nhiên: hai người tải cùng một giây không ghi đè file của nhau.
    stored = uploads / f"{now():%Y%m%d_%H%M%S}_{secrets.token_hex(4)}_{fname}"
    stored.write_bytes(content)
    warning = None
    client = None
    if use_ai:
        client, warning = make_client_safe(rules)
        if can_send_external(client):
            warning = "Có sử dụng OpenAI (API bên ngoài) – CHỈ được dùng với dữ liệu giả."

    def work():  # chạy ngoài vòng sự kiện (đọc Excel + gọi AI có thể lâu)
        try:
            raw = read_raw(stored)
        except Exception as e:  # noqa: BLE001 – file hỏng / sai định dạng: báo người dùng, không lỗi 500
            raise HTTPException(422, "Không đọc được file. Kiểm tra lại file xuất từ HIS (.xlsx).") from e
        return process_his(raw, load_his_columns(), rules, d, client=client)

    res = await run_in_threadpool(work)
    report = jsonable(res.report.as_dicts())
    if not res.ok:
        audit.log("upload_his_rejected", "file", fname, {"errors": report[:5]}, user_id=user.user_id)
        return {"ok": False, "report": report, "warning": warning}
    with session_scope() as s:
        save_his_result(s, res, fname, d, user.user_id)
    return {"ok": True, "report": report, "warning": warning,
            "message": f"Đã xử lý {len(res.rows)} dòng → {len(res.planned)} lịch hẹn; bỏ {len(res.dropped)} dòng khác khoa; "
                       f"tự điền {res.auto_rate:.0%}."}


class ConfirmIn(BaseModel):
    d: date
    arrival_time: str | None = None  # "HH:MM"; None = giữ "Khám chờ"


@router.post("/plan/{plan_id}/confirm")
def confirm(plan_id: int, body: ConfirmIn, user: CurrentUser = Depends(require("confirm_arrival"))) -> dict:
    with session_scope() as s:
        p = next(iter(plans_for_date(s, body.d, plan_id)), None)
        if p is None:
            raise HTTPException(404, "Không tìm thấy lịch hẹn.")
        t = None
        if body.arrival_time:
            try:
                hh, mm = body.arrival_time.split(":")
                t = time(int(hh), int(mm))
            except ValueError as e:
                raise HTTPException(422, "Giờ đến không hợp lệ.") from e
            if t > p.start_time:
                raise HTTPException(422, "Giờ đến phải trước hoặc bằng giờ hẹn.")
            # Gõ nhầm "00:05" → tin nhắn hẹn khách đến nửa đêm. Không sớm hơn giờ mở cửa (rules.yaml), trừ khi
            # chính giờ hẹn sớm hơn giờ mở cửa.
            earliest = min(parse_hhmm(load_rules()["arrival"]["earliest_arrival"]), p.start_time)
            if t < earliest:
                raise HTTPException(422, f"Giờ đến không được sớm hơn {earliest:%H:%M} (giờ mở cửa khoa).")
        elif p.method != "waiting":
            raise HTTPException(422, "Cần nhập giờ đến.")
        confirm_plan(s, plan_id, t, user.user_id)
    return {"ok": True}


@router.post("/plan/{plan_id}/called-doctor")
def called_doctor(plan_id: int, d: date, user: CurrentUser = Depends(require("confirm_arrival"))) -> dict:
    with session_scope() as s:
        p = next(iter(plans_for_date(s, d, plan_id)), None)
        if p is None:
            raise HTTPException(404, "Không tìm thấy lịch hẹn.")
        audit.log("called_doctor", "arrival_plan", plan_id, {"reasons": p.reasons}, user_id=user.user_id, session=s)
    return {"ok": True}


@router.post("/export")
def export(d: date, user: CurrentUser = Depends(require("export_worldsms"))) -> dict:
    rules = load_rules()
    with session_scope() as s:
        plans = plans_for_date(s, d)
    try:
        hc = load_his_columns()
        exports = build_exports(_export_rows(plans), d, _waiting(), columns=hc.worldsms_columns,
                                phone_format=hc.worldsms_phone_format, vn_aliases=hc.vietnam_aliases)
    except ExportLocked as e:
        raise HTTPException(409, str(e)) from e
    paths = write_exports(exports, work_dir("exports"))
    audit.log("export_worldsms", "date", d.isoformat(),
              {"files": [x.name for x in paths], "rows": {k: len(v) for k, v in exports.items()},
               "rules_version": rules.rules_version}, user_id=user.user_id)
    return {"files": [{"name": k, "rows": len(v)} for k, v in exports.items()]}


@router.get("/export/{name}")
def download_export(name: str, user: CurrentUser = Depends(require("export_worldsms"))):
    path = work_dir("exports") / safe_name(name)
    if not path.is_file() or path.suffix != ".xlsx":
        raise HTTPException(404, "Không có file.")
    if (m := _WORLDSMS_FILE.match(path.name)) is not None:
        with session_scope() as s:
            why = _stale_export(s, date.fromisoformat(m.group(1)), path)
        if why:
            raise HTTPException(409, why)
    audit.log("download_export", "file", path.name, user_id=user.user_id)
    return file_response(path.read_bytes(), path.name)


@router.get("/arrivals-xlsx")
def arrivals_xlsx(d: date, user: CurrentUser = Depends(require("export_worldsms"))):
    """Bảng giờ đến của một ngày (file OH + loại khám, dịch vụ, giờ đến, căn cứ) cho lễ tân / điều dưỡng."""
    import io

    import pandas as pd

    rules = load_rules()
    with session_scope() as s:
        plans = plans_for_date(s, d)
    rows = []
    for p in plans:
        det = p.details or {}
        mins = ""
        if p.arrival_time is not None:
            mins = (p.start_time.hour * 60 + p.start_time.minute) - (p.arrival_time.hour * 60 + p.arrival_time.minute)
        rows.append({
            "HN": p.pid, "Họ tên": p.full_name, "SĐT": mask_phone(p.phone),
            "Giờ hẹn (gặp bác sĩ)": p.start_time.strftime("%H:%M"),
            "Ghi chú (đã che)": p.masked_note or "",
            "Loại khám": " · ".join(det.get("visit_types") or []),
            "Dịch vụ dự kiến": ", ".join(p.services),
            "Đến trước (phút)": mins,
            "Giờ đến": p.arrival_time.strftime("%H:%M") if p.arrival_time else (_waiting() if p.method == "waiting" else ""),
            "Trạng thái": STATUS_VI.get(p.status, p.status),
            "Lý do cần xác nhận": "; ".join(reason_label(r, rules) for r in p.reasons),
            "Nhịn ăn": "Có" if det.get("fasting") else "",
            "Cách tính": "\n".join([det.get("rule_applied") or "", *(det.get("notes") or [])]).strip(),
            "Căn cứ (nguồn)": "\n".join(det.get("basis") or []),
            "Phiên bản danh mục": det.get("knowledge_version") or "",
        })
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, index=False, sheet_name="Giờ đến")
        ws = w.book["Giờ đến"]
        ws.freeze_panes = "A2"
        for col, width in zip("ABCDEFGHIJKLMNO", (12, 22, 14, 12, 40, 26, 40, 10, 10, 14, 45, 8, 60, 60, 10), strict=True):
            ws.column_dimensions[col].width = width
    audit.log("download_arrivals_xlsx", "date", d.isoformat(), {"rows": len(rows)}, user_id=user.user_id)
    return file_response(buf.getvalue(), f"gio_den_{d.isoformat()}.xlsx")
