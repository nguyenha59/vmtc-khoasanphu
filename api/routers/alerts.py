"""Màn Cảnh báo: danh sách, chi tiết 3 lớp, gọi (hiện số – ghi nhật ký), báo bác sĩ, ghi xử trí."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.charts import series_for
from api.deps import CurrentUser, require
from core import audit
from core.alerts import (
    DOCTOR_DECISIONS,
    FINAL_LEVELS,
    STATUS_VI,
    HandleError,
    ack_downgrade_reviews,
    alert_rows,
    close_alert,
    escalate_alert,
    handle_alert,
    history_for,
    reopen_from_review,
    send_yellow_digest,
)
from core.channels import get_alert_channel
from core.config import load_forms, load_rules
from core.db import session_scope
from core.models import Alert, AlertReview, Assessment, FormResponse, Patient, User
from core.red_basis import explain
from core.repo import patient_phone, stage_text
from core.security import can, decrypt, mask_phone

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _iso(d: Any) -> str | None:
    return d.isoformat() if d else None


LEVEL_RANK = {"red": 0, "yellow": 1, "green": 2}


def _reasons(asm: Assessment) -> list[str]:
    """Các lý do ngắn gọn (đã lưu khi đánh giá) – 'cảnh báo về cái gì'."""
    f = asm.findings or {}
    out: list[str] = []
    for r in f.get("reasons") or [x["reason"] for x in f.get("rule", [])]:
        if r and r not in out:
            out.append(r)
    for x in f.get("ai", []):
        if x.get("present") and x.get("level") in ("red", "yellow") and x.get("name") not in out:
            out.append(x["name"])
    return out


def _summary_item(a: Alert, asm: Assessment, resp: FormResponse, p: Patient) -> dict[str, Any]:
    return {"id": a.id, "level": a.level, "status": a.status, "status_label": STATUS_VI.get(a.status, a.status),
            "code": p.code, "ga": stage_text(p, resp.submitted_at.date()), "form": resp.form_code,
            "created_at": _iso(a.created_at), "summary": (asm.summary or "")[:200], "reasons": _reasons(asm),
            "demo": resp.source == "demo"}


BP_SLOTS = {"BP_AM": "Huyết áp buổi sáng", "BP_PM": "Huyết áp buổi tối", "BP": "Huyết áp"}
COMBO_QUESTIONS = {"FEVER_PLUS": ["C4", "C2", "C3"], "EDEMA_PLUS": ["C7", "C6"]}


TREND_TITLES = {"bp_sys": "Huyết áp tâm thu tăng so với nền của thai phụ",
                "bp_dia": "Huyết áp tâm trương tăng so với nền của thai phụ", "weight": "Tăng cân nhanh"}


def _combo_concepts(code: str, rules: Any) -> set[str]:
    for c in rules.get("text_combos") or []:
        if c.get("code") == code:
            return set(c.get("all", [])) | set(c.get("any_of", []))
    return {code.removeprefix("TXT_").removesuffix("_BP")}


def _why(f: dict[str, Any], answers: dict[str, Any], questions: dict[str, Any],
         rules: Any = None) -> list[dict[str, Any]]:
    """Giải thích từng lý do: lấy từ câu trả lời nào / xu hướng nào / câu tự viết nào + căn cứ (nguồn y khoa).

    Chỉ đọc kết quả đã lưu; căn cứ đọc từ cấu hình hiện hành (red_basis).
    """
    rules = rules or load_rules()
    out: list[dict[str, Any]] = []
    for x in f.get("rule", []):
        code = x.get("code", "")
        q = questions.get(code, {}).get("text")
        detail = f"{q} → trả lời: {answers[code]}" if q and code in answers else ""
        if not detail and code in BP_SLOTS:  # số đo huyết áp: hiện đúng số thai phụ nhập
            sv, dv = answers.get(f"{code}_SYS"), answers.get(f"{code}_DIA")
            if sv not in (None, "") or dv not in (None, ""):
                detail = f"{BP_SLOTS[code]} → trả lời: {sv if sv not in (None, '') else '—'}/{dv if dv not in (None, '') else '—'} mmHg"
        if not detail and code.startswith("TXT_"):  # kết hợp trong phần tự viết: trích đúng các câu thai phụ viết
            quotes = [a["evidence"] for a in f.get("ai", []) if a.get("evidence") and a.get("present")
                      and a.get("level") in ("red", "yellow") and a.get("concept") in _combo_concepts(code, rules)]
            detail = " · ".join(f"“{q}”" for q in dict.fromkeys(quotes))
        if not detail and code in COMBO_QUESTIONS:
            detail = " + ".join(f"{questions.get(c, {}).get('text', c)} → {answers.get(c, '—')}" for c in COMBO_QUESTIONS[code])
        source = ("Số đo trong phần tự viết" if code.startswith("TEXT_")
                  else "Kết hợp triệu chứng trong phần tự viết" if code.startswith("TXT_") else "Câu trả lời trong form")
        basis = (explain("rule", "UNSURE", rules)
                 if str(x.get("reason", "")).startswith("Không chắc") else explain("rule", code, rules))
        out.append({"level": x.get("level", "yellow"), "source": source, "title": x["reason"], "detail": detail,
                    **basis})
    for x in f.get("trend", []):
        if x.get("level") in ("red", "yellow"):
            out.append({"level": x["level"], "source": "Xu hướng so với các lần trước",
                        "title": TREND_TITLES.get(x["kind"], x["kind"]),
                        "detail": x.get("reason") or "", "basis": "Theo dõi xu hướng EWMA so với nền của chính thai phụ",
                        "basis_codes": []})
    for x in f.get("ai", []):
        if x.get("present") and x.get("level") in ("red", "yellow"):
            source = ("Quy tắc đọc phần tự viết" if x.get("source") == "lexicon"
                      else "Đọc tự viết, đã đối chiếu quy tắc" if "rule" in x.get("source", "")
                      else "AI đọc phần tự viết")
            title = x["name"]
            if x.get("concept") == "HEADACHE" and x.get("severity") == "mild" and x["level"] == "red":
                source, title = "Cần xác minh mức độ theo cấu hình", "Đau đầu: văn bản mô tả mức nhẹ"
            if "unverified" in str(x.get("source", "")):
                source = "AI nghi ngờ – quy tắc không thấy từ khóa, cần gọi xác minh"
            if x.get("assertion_status") == "uncertain":
                source = "Cần gọi xác minh – thông tin đọc/nghe được"
            out.append({"level": x["level"], "source": source, "title": title,
                        "detail": f"“{x['evidence']}”" if x.get("evidence") else "",
                        **explain("concept", x.get("concept", ""), rules)})
    for code in f.get("implausible") or []:
        out.append({"level": "yellow", "source": "Số liệu bất thường", "title": "Nghi nhập sai số",
                    "detail": questions.get(code, {}).get("text", code)})
    out.sort(key=lambda r: LEVEL_RANK.get(r["level"], 3))
    return out


@router.get("")
def list_alerts(include_closed: bool = False, user: CurrentUser = Depends(require("view_alerts"))) -> dict:
    with session_scope() as s:
        items = [_summary_item(*r) for r in alert_rows(s, include_closed=include_closed)[:300]]
        n_reviews = s.scalar(select(func.count(AlertReview.id)).where(
            AlertReview.kind == "downgrade_review", AlertReview.status == "pending")) or 0
    # Gộp theo thai phụ: số cảnh báo chưa đóng của cùng một người (giao diện hiện 1 dòng / người)
    per_patient = Counter(i["code"] for i in items if i["status"] != "closed")
    for i in items:
        i["patient_open"] = per_patient.get(i["code"], 0)
    rules = load_rules()
    return {"items": items,
            "counts": {"red": sum(1 for i in items if i["level"] == "red" and i["status"] == "open"),
                       "yellow": sum(1 for i in items if i["level"] == "yellow" and i["status"] == "open"),
                       "awaiting_doctor": sum(1 for i in items if i["status"] == "escalated"),
                       "downgrade_reviews": n_reviews},
            "digest_times": rules["channels"]["yellow_digest_times"],
            "final_levels": FINAL_LEVELS, "decisions": DOCTOR_DECISIONS}


# ---------------------------------------------------------------- bác sĩ xem lại ca đỏ bị hạ mức (đặt trước /{alert_id})
@router.get("/reviews")
def downgrade_reviews(user: CurrentUser = Depends(require("review_downgrades"))) -> dict:
    with session_scope() as s:
        rows = s.execute(
            select(AlertReview, Alert, Assessment, FormResponse, Patient)
            .join(Alert, AlertReview.alert_id == Alert.id)
            .join(Assessment, Alert.assessment_id == Assessment.id)
            .join(FormResponse, Assessment.response_id == FormResponse.id)
            .join(Patient, FormResponse.patient_id == Patient.id)
            .where(AlertReview.kind == "downgrade_review", AlertReview.status == "pending")
            .order_by(AlertReview.created_at)
        ).all()
        names = {u.id: u.name for u in s.scalars(select(User))}
        items = [{"review_id": r.id, "alert_id": a.id, "code": p.code, "reasons": _reasons(asm),
                  "final_level": a.final_level, "final_label": FINAL_LEVELS.get(a.final_level or "", ""),
                  "note": a.note or "", "handled_at": _iso(a.handled_at), "by": names.get(a.handled_by or 0, "")}
                 for r, a, asm, _resp, p in rows]
    return {"items": items, "sample_rate": load_rules()["alert_review"]["downgrade_sample_rate"]}


class AckIn(BaseModel):
    review_ids: list[int] = Field(default_factory=list, max_length=500)


@router.post("/reviews/ack")
def ack_reviews(body: AckIn, user: CurrentUser = Depends(require("review_downgrades"))) -> dict:
    with session_scope() as s:
        n = ack_downgrade_reviews(s, body.review_ids, user.user_id)
    return {"acked": n}


@router.post("/reviews/{review_id}/reopen")
def reopen_review(review_id: int, user: CurrentUser = Depends(require("review_downgrades"))) -> dict:
    try:
        with session_scope() as s:
            alert_id = reopen_from_review(s, review_id, user.user_id)
    except HandleError as e:
        raise HTTPException(409, str(e)) from e
    return {"alert_id": alert_id}


def _load(s: Any, alert_id: int) -> tuple[Alert, Assessment, FormResponse, Patient]:
    a = s.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "Không tìm thấy cảnh báo.")
    asm = s.get(Assessment, a.assessment_id)
    resp = s.get(FormResponse, asm.response_id)
    p = s.get(Patient, resp.patient_id)
    return a, asm, resp, p


@router.get("/{alert_id}")
def alert_detail(alert_id: int, user: CurrentUser = Depends(require("view_alerts"))) -> dict:
    rules, forms_cfg = load_rules(), load_forms()
    with session_scope() as s:
        a, asm, resp, p = _load(s, alert_id)
        f = asm.findings or {}
        qs = forms_cfg.questions
        answers = [{"key": k, "text": qs.get(k, {}).get("text", forms_cfg.epds["items"].get(k, k)), "value": v}
                   for k, v in (resp.answers or {}).items()]
        out = {
            **_summary_item(a, asm, resp, p),
            "summary": asm.summary or "",
            "why": _why(f, resp.answers or {}, qs, rules),
            "ai_escalated": bool(f.get("ai_escalated")),
            "layers": {
                "rule": {"level": asm.rule_level, "reasons": [x["reason"] for x in f.get("rule", [])],
                         "implausible": f.get("implausible") or []},
                "trend": {"level": asm.trend_level,
                          "items": [{"kind": x["kind"], "reason": x["reason"] or "ổn định"} for x in f.get("trend", [])]},
                "ai": {"level": asm.ai_level, "confidence": asm.ai_confidence, "needs_human": bool(f.get("needs_human")),
                       "skipped": ((asm.raw or {}).get("llm") or {}).get("llm_status") == "skipped_confirmed_red",
                       "findings": [{"name": x["name"], "present": x["present"], "temporality": x["temporality"],
                                     "experiencer": x["experiencer"], "evidence": x["evidence"]} for x in f.get("ai", [])],
                       "model": asm.model, "prompt_version": asm.prompt_version, "rules_version": asm.rules_version},
            },
            "answers": answers,
            "free_text": decrypt(resp.free_text_enc) or "",
            "note": a.note or "", "final_level": a.final_level,
            "phone_masked": mask_phone(patient_phone(p)),
            "submitted_at": _iso(resp.submitted_at),
            "charts": series_for(history_for(s, p.id, days=45), rules),
            "decisions": DOCTOR_DECISIONS,
            "doctor_decision": _doctor_decision(s, alert_id),
            # Cảnh báo khác chưa đóng của cùng thai phụ → xử trí chung một lần gọi
            "siblings": [{"id": x.id, "level": x.level, "status": x.status, "form": r.form_code,
                          "reasons": _reasons(xa), "created_at": _iso(x.created_at)}
                         for x, xa, r, _p in alert_rows(s) if _p.id == p.id and x.id != alert_id],
        }
        audit.log("view_alert", "alert", alert_id, {"patient": p.code}, user_id=user.user_id, session=s)
    return out


def _doctor_decision(s: Any, alert_id: int) -> dict[str, Any] | None:
    r = s.scalar(select(AlertReview).where(AlertReview.alert_id == alert_id, AlertReview.kind == "doctor_decision")
                 .order_by(AlertReview.id.desc()))
    if r is None:
        return None
    return {"status": r.status, "decision": r.decision, "label": DOCTOR_DECISIONS.get(r.decision or "", ""),
            "note": r.note or "", "at": _iso(r.reviewed_at)}


@router.post("/{alert_id}/reveal-phone")
def reveal_phone(alert_id: int, user: CurrentUser = Depends(require("reveal_phone"))) -> dict:
    with session_scope() as s:
        a, _asm, _resp, p = _load(s, alert_id)
        phone = patient_phone(p)
        audit.log("reveal_phone", "patient", p.code, {"alert_id": alert_id}, user_id=user.user_id, session=s)
        if a.status == "open":
            handle_alert(s, alert_id, user.user_id, status="called", note=a.note or "", final_level=None)
    return {"phone": phone}


@router.post("/{alert_id}/escalate")
def escalate(alert_id: int, user: CurrentUser = Depends(require("handle_alerts"))) -> dict:
    with session_scope() as s:
        a, *_ = _load(s, alert_id)
        if a.status == "closed":
            raise HTTPException(409, "Cảnh báo đã đóng.")
        escalate_alert(s, alert_id, user.user_id, a.note or "")
    return {"ok": True}


class HandleIn(BaseModel):
    note: str = Field("", max_length=2000)
    final_level: str | None = None
    close: bool = True
    decision: str | None = None  # bác sĩ: visit_today | emergency | monitor (bắt buộc khi đóng ca "Giữ đỏ")
    also_ids: list[int] = Field(default_factory=list, max_length=20)  # cảnh báo khác cùng thai phụ – xử trí chung


@router.post("/{alert_id}/handle")
def handle(alert_id: int, body: HandleIn, user: CurrentUser = Depends(require("handle_alerts"))) -> dict:
    if body.final_level and body.final_level not in FINAL_LEVELS:
        raise HTTPException(422, "Mức sau khi gọi không hợp lệ.")
    if body.decision and not can(user.role, "decide_red"):
        raise HTTPException(403, "Chỉ bác sĩ chọn quyết định xử trí.")
    rules = load_rules()
    results: dict[int, str] = {}
    try:
        with session_scope() as s:
            _a, _asm, _resp, p = _load(s, alert_id)
            ids = [alert_id]
            for other in dict.fromkeys(body.also_ids):  # chỉ nhận cảnh báo của CÙNG thai phụ
                o = _load(s, other)
                if o[3].id != p.id:
                    raise HTTPException(422, "Chỉ xử trí chung các cảnh báo của cùng một thai phụ.")
                if o[0].status != "closed" and other != alert_id:
                    ids.append(other)
            for i in ids:
                if not body.close:
                    handle_alert(s, i, user.user_id, status="called", note=body.note, final_level=body.final_level)
                    results[i] = "called"
                else:
                    results[i] = close_alert(s, i, user_id=user.user_id, role=user.role, final_level=body.final_level,
                                             note=body.note, decision=body.decision, rules=rules)
    except HandleError as e:
        raise HTTPException(422, str(e)) from e
    status = "awaiting_doctor" if "awaiting_doctor" in results.values() else ("closed" if body.close else "called")
    return {"ok": True, "status": status, "results": results}


@router.post("/digest")
def digest(user: CurrentUser = Depends(require("handle_alerts"))) -> dict:
    rules = load_rules()
    with session_scope() as s:
        n = send_yellow_digest(s, get_alert_channel(rules))
    return {"sent": n}
