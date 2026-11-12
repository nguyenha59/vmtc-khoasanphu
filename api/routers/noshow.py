"""Màn Dự đoán bỏ hẹn: xếp hạng nguy cơ, yếu tố chính, danh sách mời đặt lại, nhập danh sách khách đến."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select

from api.deps import CurrentUser, require
from api.util import file_response, safe_name
from core import audit
from core.ai.noshow import (
    action_for,
    arrivals_imported,
    build_features,
    load_latest,
    load_visits,
    predict,
    reinvite_list,
)
from core.config import TZ, load_rules
from core.db import now, session_scope
from core.models import Appointment, NoshowPrediction, Patient
from core.repo import patient_phone
from core.reports import xlsx_bytes
from core.security import mask_phone

router = APIRouter(prefix="/api/noshow", tags=["noshow"])


def _today() -> date:
    return datetime.now(TZ).date()


def _compute(target: date) -> dict:
    """Dự đoán cho ngày target (lưu NoshowPrediction) + danh sách mời đặt lại hôm qua. Có SĐT đầy đủ – chỉ dùng nội bộ."""
    rules = load_rules()
    bundle = load_latest()
    yesterday = _today() - timedelta(days=1)
    with session_scope() as s:
        reinv = reinvite_list(s, yesterday, rules)
        imported = arrivals_imported(s, yesterday)
        n_appt = s.scalar(select(func.count(Appointment.id)).where(Appointment.appt_date == target)) or 0
        if bundle is None:
            return {"bundle": None, "rows": [], "reinvite": reinv, "n_appt": n_appt, "yesterday": yesterday,
                    "arrivals_imported": imported}
        visits = load_visits(s)
        feats = build_features(visits)
        day = feats[feats["appt_date"] == target]
        preds = predict(bundle, day) if len(day) else pd.DataFrame(columns=["appointment_id", "prob", "top_factors"])
        ids = [int(x) for x in preds["appointment_id"]]
        if ids:
            s.execute(delete(NoshowPrediction).where(NoshowPrediction.appointment_id.in_(ids)))
            for _, r in preds.iterrows():
                s.add(NoshowPrediction(appointment_id=int(r["appointment_id"]), prob=float(r["prob"]),
                                       top_factors=r["top_factors"], model_version=bundle["version"], created_at=now()))
        info = visits.set_index("appointment_id")
        pids = [int(info.loc[i, "patient_id"]) for i in ids] or [0]
        phones = {p.id: patient_phone(p) for p in s.scalars(select(Patient).where(Patient.id.in_(pids)))}
    rows = []
    for _, r in preds.sort_values("prob", ascending=False).iterrows():
        v = info.loc[r["appointment_id"]]
        phone = phones.get(int(v["patient_id"]), "")
        rows.append({"code": v["code"], "phone": phone, "phone_masked": mask_phone(phone),
                     "appt": f"{v['appt_date']:%d/%m} {v['start_time']:%H:%M}", "services": v["services"],
                     "prob": round(float(r["prob"]), 3), "factors": [f["text"] for f in r["top_factors"]],
                     "action": action_for(float(r["prob"]), rules)})
    return {"bundle": bundle, "rows": rows, "reinvite": reinv, "n_appt": n_appt, "yesterday": yesterday,
            "arrivals_imported": imported}


@router.get("")
def overview(d: date | None = None, user: CurrentUser = Depends(require("view_noshow"))) -> dict:
    target = d or _today() + timedelta(days=1)
    rules = load_rules()
    r = _compute(target)
    b = r["bundle"]
    rows = [{k: v for k, v in x.items() if k != "phone"} for x in r["rows"]]
    reinv = [{k: v for k, v in x.items() if k not in ("phone", "pid", "patient_id")} for x in r["reinvite"]]
    return {
        "date": target.isoformat(), "yesterday": r["yesterday"].isoformat(), "has_model": b is not None,
        "model": None if b is None else {"version": b["version"], "auc_lgb": b["metrics"]["lightgbm"]["roc_auc"],
                                         "auc_lr": b["metrics"]["logistic"]["roc_auc"]},
        "call_threshold": rules["noshow"]["call_threshold"], "sms_threshold": rules["noshow"]["sms_threshold"],
        "n_appt": r["n_appt"], "rows": rows, "reinvite": reinv, "arrivals_imported": r["arrivals_imported"],
        "high_risk": sum(x["prob"] >= rules["noshow"]["call_threshold"] for x in rows),
    }


@router.get("/call-list.xlsx")
def call_list(d: date, user: CurrentUser = Depends(require("view_noshow"))):
    r = _compute(d)
    call = [x for x in r["rows"] if x["action"] != "—"]
    audit.log("export_noshow_call_list", "date", d.isoformat(), {"rows": len(call)}, user_id=user.user_id)
    data = xlsx_bytes([{"Số điện thoại": x["phone"], "Mã": x["code"], "Lịch hẹn": x["appt"], "Đề xuất": x["action"],
                        "Yếu tố": " · ".join(x["factors"])} for x in call])
    return file_response(data, f"goi_xac_nhan_{d.isoformat()}.xlsx")


@router.get("/reinvite.xlsx")
def reinvite(user: CurrentUser = Depends(require("view_noshow"))):
    yesterday = _today() - timedelta(days=1)
    with session_scope() as s:
        reinv = reinvite_list(s, yesterday, load_rules())
    audit.log("export_reinvite", "date", yesterday.isoformat(), {"rows": len(reinv)}, user_id=user.user_id)
    data = xlsx_bytes([{"Số điện thoại": x["phone"], "Mã": x["code"], "Lịch cũ": x["appt"], "Ưu tiên": x["priority"]}
                       for x in reinv])
    return file_response(data, f"moi_dat_lai_{yesterday.isoformat()}.xlsx")


@router.post("/arrivals")
async def upload_arrivals(file: UploadFile = File(...), user: CurrentUser = Depends(require("view_noshow"))) -> dict:
    from core.ingest.arrivals_reader import import_arrivals

    name = safe_name(file.filename or "arrivals.xlsx")
    buf = io.BytesIO(await file.read())
    buf.name = name  # read_arrivals chọn CSV/Excel theo tên
    try:
        with session_scope() as s:
            n = import_arrivals(s, buf, name, user.user_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"rows": n}


@router.post("/retrain")
async def retrain(user: CurrentUser = Depends(require("retrain_model"))) -> dict:
    from scripts.train_noshow import run as train_run

    out = await run_in_threadpool(train_run, user.user_id)
    return {"version": out["version"], "auc_lgb": out["lightgbm"]["roc_auc"], "auc_lr": out["logistic"]["roc_auc"]}
