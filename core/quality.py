"""Chỉ số chất lượng AI từ dữ liệu người sửa (docs/06 màn 9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import (
    Alert,
    Appointment,
    ArrivalPlan,
    Assessment,
    EvalRun,
    NoteExtraction,
    Patient,
)


def _alert_agrees(level: str, final: str) -> bool:
    if final == "false_alarm":
        return False
    if level == "red":
        return final == "keep_red"
    return True  # vàng: người xác nhận cần liên hệ


def human_agreement(s: Session, since: datetime | None = None) -> dict[str, Any]:
    plans = s.scalars(select(ArrivalPlan)).all()
    reviewed = [p for p in plans if p.status == "confirmed"]
    agree = [p for p in reviewed if p.method != "manual"]
    alerts = s.scalars(select(Alert).where(Alert.final_level.is_not(None))).all()
    a_agree = [a for a in alerts if _alert_agrees(a.level, a.final_level or "")]
    return {
        "arrival_total": len(plans),
        "arrival_reviewed": len(reviewed),
        "arrival_agreement": round(len(agree) / len(reviewed), 4) if reviewed else None,
        "transfer_rate": round(sum(1 for p in plans if p.reasons) / len(plans), 4) if plans else None,
        "alert_reviewed": len(alerts),
        "alert_agreement": round(len(a_agree) / len(alerts), 4) if alerts else None,
    }


def weekly_table(s: Session) -> pd.DataFrame:
    rows = []
    q =(select(ArrivalPlan.status, ArrivalPlan.method, ArrivalPlan.reasons, ArrivalPlan.rules_version,
                Appointment.appt_date, NoteExtraction.prompt_version)
         .join(Appointment, ArrivalPlan.appointment_id == Appointment.id)
         .outerjoin(NoteExtraction, NoteExtraction.appointment_id == Appointment.id))
    for status, method, reasons, rv, d, pv in s.execute(q).all():
        rows.append({"week": f"{d.isocalendar().year}-W{d.isocalendar().week:02d}", "rules_version": rv,
                     "prompt_version": pv or "(không AI)", "reviewed": status == "confirmed",
                     "agree": status == "confirmed" and method != "manual", "transfer": bool(reasons)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    g = df.groupby(["week", "prompt_version", "rules_version"])
    out = pd.DataFrame({
        "Số lịch": g.size(),
        "Tỷ lệ chuyển người": g["transfer"].mean().round(3),
        "Đã xác nhận": g["reviewed"].sum(),
        "AI/quy tắc khớp người": (g["agree"].sum() / g["reviewed"].sum().where(g["reviewed"].sum() > 0)).round(3),
    }).reset_index()
    return out.rename(columns={"week": "Tuần", "prompt_version": "Prompt", "rules_version": "Quy tắc"})


def recent_errors(s: Session, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    q = (select(ArrivalPlan, Appointment, Patient).join(Appointment, ArrivalPlan.appointment_id == Appointment.id)
         .join(Patient, Appointment.patient_id == Patient.id)
         .where(ArrivalPlan.method == "manual").order_by(ArrivalPlan.confirmed_at.desc()).limit(limit))
    for plan, _a, p in s.execute(q).all():
        out.append({"Thời điểm": plan.confirmed_at, "Loại": "Giờ đến", "Mã": p.code,
                    "AI/quy tắc": plan.proposed_time.strftime("%H:%M") if plan.proposed_time else "—",
                    "Người sửa": plan.arrival_time.strftime("%H:%M") if plan.arrival_time else "—",
                    "Lý do": ", ".join(plan.reasons or [])})
    q2 = (select(Alert, Assessment).join(Assessment, Alert.assessment_id == Assessment.id)
          .where(Alert.final_level.in_(["false_alarm", "down_yellow"])).order_by(Alert.handled_at.desc()).limit(limit))
    for a, asm in s.execute(q2).all():
        out.append({"Thời điểm": a.handled_at, "Loại": "Cảnh báo", "Mã": f"#{a.id}",
                    "AI/quy tắc": f"{a.level} (AI {asm.ai_level})", "Người sửa": a.final_level,
                    "Lý do": (asm.summary or "")[:80]})
    out.sort(key=lambda r: r["Thời điểm"] or datetime.min, reverse=True)
    return out[:limit]


def latest_eval(s: Session, dataset: str, include_mock: bool = False) -> EvalRun | None:
    """Lần chạy mới nhất. Mặc định bỏ qua lần chạy mock (mock chỉ kiểm tra đường ống, không đo chất lượng AI);
    nếu chưa có lần chạy thật nào thì mới dùng mock."""
    q = select(EvalRun).where(EvalRun.dataset == dataset).order_by(EvalRun.ts.desc())
    if not include_mock:
        real = s.scalar(q.where(EvalRun.model != "mock").limit(1))
        if real is not None:
            return real
    return s.scalar(q.limit(1))


def eval_table(s: Session) -> pd.DataFrame:
    rows = []
    for r in s.scalars(select(EvalRun).order_by(EvalRun.ts.desc()).limit(100)):
        m = r.metrics or {}
        if r.dataset == "forms":
            key = {"Độ nhạy đỏ": m.get("rules_plus_ai", {}).get("red_sensitivity"),
                   "Red-team": m.get("redteam", {}).get("red_sensitivity"),
                   "Báo nhầm": m.get("rules_plus_ai", {}).get("false_alarm_rate"),
                   "AI bắt thêm đỏ": m.get("extra_red_caught_by_ai")}
        elif r.dataset == "notes":
            key = {"Tự điền": m.get("auto_rate"), "Đúng trong tự điền": m.get("arrival_accuracy_auto"),
                   "Tuần thai đúng": m.get("ga_accuracy")}
        else:
            key = {"AUC LightGBM": m.get("lightgbm", {}).get("roc_auc"), "AUC Logistic": m.get("logistic", {}).get("roc_auc")}
        rows.append({"Thời điểm": r.ts, "Tuần": f"{r.ts.isocalendar().year}-W{r.ts.isocalendar().week:02d}",
                     "Bộ dữ liệu": r.dataset, "Prompt": r.prompt_version or "—", "Quy tắc": r.rules_version,
                     "Mô hình": r.model, **{k: v for k, v in key.items()}})
    return pd.DataFrame(rows)


def blocking_gate(s: Session) -> tuple[list[str], list[str]]:
    """Chỉ tiêu BẮT BUỘC (eval_targets.yaml, blocking) của lần đánh giá mới nhất mỗi bộ dữ liệu.

    Trả (đo được mà trượt, chưa có số liệu). Dùng trước khi kích hoạt phiên bản quy tắc mới: trượt → chặn duyệt;
    chưa có số liệu → vẫn duyệt được nhưng ghi nhật ký (người dùng chốt 16/11/2026)."""
    from core.ai.scorecard import load_targets, scorecard

    targets = load_targets()
    failed, unmeasured = [], []
    for ds in ("notes", "forms", "noshow"):
        r = latest_eval(s, ds)
        for c in scorecard(ds, (r.metrics or {}) if r else {}, targets):
            if not c.blocking:
                continue
            if c.passed is False:
                failed.append(f"{ds}: {c.label}")
            elif c.passed is None:
                unmeasured.append(f"{ds}: {c.label}")
    return failed, unmeasured

