"""Màn Lịch hẹn: tải file HIS, kiểm tra, giờ đến, xác nhận, xuất WorldSMS."""

from __future__ import annotations

import secrets
from datetime import date, time, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.deps import CurrentUser, require
from api.util import file_response, jsonable, safe_name
from core import audit
from core.ai.llm_client import OllamaClient, OpenAIClient, make_client
from core.config import DATA_DIR, TZ, llm_provider, load_his_columns, load_rules
from core.db import now, session_scope
from core.ingest.his_reader import read_raw
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
from core.rules.review import REASON_LABELS
from core.security import mask_phone

router = APIRouter(prefix="/api/appointments", tags=["appointments"])
EXPORT_DIR = DATA_DIR / "exports"
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
        "reasons": [{"code": r, "label": REASON_LABELS.get(r, r)} for r in p.reasons],
        "merged": len(p.merged_from) > 1,
    }


def _export_rows(plans: list[PlanView]) -> list[ExportRow]:
    return [ExportRow(phone=p.phone, full_name=p.full_name, pid=p.pid, appt_date=p.appt_date, start_time=p.start_time,
                      arrival_time=p.arrival_time, status=p.status, nationality=p.nationality,
                      waiting=p.method == "waiting" and p.arrival_time is None) for p in plans]


@router.get("")
def list_plans(d: date | None = None, user: CurrentUser = Depends(require("upload_his"))) -> dict:
    d = d or _default_date()
    rules = load_rules()
    waiting = _waiting()
    with session_scope() as s:
        plans = plans_for_date(s, d)
    bad = invalid_phone_rows(_export_rows(plans))
    exports = sorted(p.name for p in EXPORT_DIR.glob(f"*{d.isoformat()}*.xlsx")) if EXPORT_DIR.exists() else []
    provider = llm_provider(rules)
    return {
        "date": d.isoformat(), "waiting_label": waiting,
        "items": [_row(p, waiting) for p in plans],
        "counts": {"total": len(plans), "need": sum(p.status == "needs_review" for p in plans),
                   "auto": sum(p.status != "needs_review" for p in plans),
                   "merged": sum(len(p.merged_from) > 1 for p in plans)},
        "invalid_phones": [r.pid for r in bad],
        "exports": exports,
        "samples": [p.name for p in sorted((DATA_DIR / "synthetic").glob("his_*.xlsx"))],
        "llm": {"provider": provider, "model": rules["llm"]["model"] if provider == "ollama" else "mock"},
    }


@router.get("/plan/{plan_id}")
def plan_detail(plan_id: int, d: date, user: CurrentUser = Depends(require("upload_his"))) -> dict:
    waiting = _waiting()
    with session_scope() as s:
        p = next((x for x in plans_for_date(s, d) if x.plan_id == plan_id), None)
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
    uploads = DATA_DIR / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    # Thêm chuỗi ngẫu nhiên: hai người tải cùng một giây không ghi đè file của nhau.
    stored = uploads / f"{now():%Y%m%d_%H%M%S}_{secrets.token_hex(4)}_{fname}"
    stored.write_bytes(content)
    warning = None
    client = None
    if use_ai:
        client = make_client(rules)
        if isinstance(client, OllamaClient) and not client.available():
            warning = "Không kết nối được Ollama hoặc chưa tải mô hình – chỉ xử lý bằng quy tắc."
            client = None
        elif isinstance(client, OpenAIClient):
            warning = "Đang dùng OpenAI (API bên ngoài) – CHỈ được dùng với dữ liệu giả."

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
        p = next((x for x in plans_for_date(s, body.d) if x.plan_id == plan_id), None)
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
        elif p.method != "waiting":
            raise HTTPException(422, "Cần nhập giờ đến.")
        confirm_plan(s, plan_id, t, user.user_id)
    return {"ok": True}


@router.post("/plan/{plan_id}/called-doctor")
def called_doctor(plan_id: int, d: date, user: CurrentUser = Depends(require("confirm_arrival"))) -> dict:
    with session_scope() as s:
        p = next((x for x in plans_for_date(s, d) if x.plan_id == plan_id), None)
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
    paths = write_exports(exports, EXPORT_DIR)
    audit.log("export_worldsms", "date", d.isoformat(),
              {"files": [x.name for x in paths], "rows": {k: len(v) for k, v in exports.items()},
               "rules_version": rules.rules_version}, user_id=user.user_id)
    return {"files": [{"name": k, "rows": len(v)} for k, v in exports.items()]}


@router.get("/export/{name}")
def download_export(name: str, user: CurrentUser = Depends(require("export_worldsms"))):
    path = EXPORT_DIR / safe_name(name)
    if not path.is_file() or path.suffix != ".xlsx":
        raise HTTPException(404, "Không có file.")
    audit.log("download_export", "file", path.name, user_id=user.user_id)
    return file_response(path.read_bytes(), path.name)
