"""Lưu câu trả lời form, đánh giá, tạo và gửi cảnh báo, xử lý cảnh báo."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from core import audit
from core.ai.llm_client import LlmClient
from core.assess import AssessResult, assess_answers
from core.channels.base import AlertChannel, AlertPayload
from core.config import FormsConfig, Rules
from core.db import now
from core.levels import Level
from core.models import (
    Alert,
    AlertReview,
    Assessment,
    FormResponse,
    FormToken,
    Patient,
    VitalReading,
)
from core.repo import ga_on, patient_name, stage_text
from core.rules.triage_rules import triage
from core.security import encrypt

FINAL_LEVELS = {"keep_red": "Giữ đỏ", "down_yellow": "Hạ vàng", "false_alarm": "Báo nhầm"}
STATUS_VI = {"open": "Chưa xử lý", "called": "Đã gọi", "escalated": "Chờ bác sĩ", "closed": "Đã đóng"}


def history_for(s: Session, patient_id: int, days: int = 60, *, as_of: datetime | None = None) -> dict[str, list[tuple[datetime, float]]]:
    at = as_of or now()
    since = at - timedelta(days=days)
    out: dict[str, list[tuple[datetime, float]]] = {}
    for v in s.scalars(select(VitalReading).outerjoin(FormResponse, VitalReading.source_response_id == FormResponse.id)
                       .where(VitalReading.patient_id == patient_id,
                              VitalReading.measured_at >= since, VitalReading.measured_at <= at,
                              (VitalReading.source_response_id.is_(None)) | (FormResponse.source != "demo"))
                       .order_by(VitalReading.measured_at)):
        out.setdefault(v.kind, []).append((v.measured_at, v.value))
    return out


def payload_for(alert: Alert, patient: Patient, result_reasons: list[str], at: datetime) -> AlertPayload:
    return AlertPayload(alert_id=alert.id, patient_code=patient.code or "", ga_text=stage_text(patient, at.date()),
                        level=alert.level, reasons=tuple(result_reasons[:5]), created_at=alert.created_at or at)


class FormAlreadySubmitted(Exception):
    """Đường dẫn form đã được dùng (kể cả khi hai lần gửi đến gần như cùng lúc)."""


def _send_after_commit(s: Session, alert_id: int, payload: AlertPayload, channel: AlertChannel) -> None:
    """Gửi cảnh báo SAU khi lưu xong: không giữ khóa CSDL trong lúc gọi mạng, và lỗi kênh gửi không làm mất
    câu trả lời của thai phụ (cảnh báo chưa gửi được sẽ được gửi lại bởi core.jobs)."""

    def _do(_session: Session) -> None:
        from core.db import session_scope

        try:
            channel.send(payload)
        except Exception as e:  # noqa: BLE001 – mọi lỗi kênh gửi đều chỉ ghi nhật ký
            audit.log("alert_send_failed", "alert", alert_id, {"error": type(e).__name__})
            return
        with session_scope() as s2:
            a = s2.get(Alert, alert_id)
            if a is not None and a.sent_at is None:
                a.sent_at = now()

    event.listen(s, "after_commit", _do, once=True)


def _mark_token_used(s: Session, token: str, at: datetime) -> None:
    """Đánh dấu đã dùng bằng một câu UPDATE có điều kiện → hai lần gửi đồng thời chỉ một lần thành công."""
    res = s.execute(update(FormToken).where(FormToken.token == token, FormToken.used_at.is_(None)).values(used_at=at))
    if res.rowcount == 0 and s.get(FormToken, token) is not None:  # type: ignore[attr-defined]
        raise FormAlreadySubmitted(token)


def submit_response(
    s: Session,
    patient: Patient,
    form_code: str,
    answers: dict[str, Any],
    free_text: str,
    *,
    rules: Rules,
    forms_cfg: FormsConfig,
    client: LlmClient | None,
    alert_channel: AlertChannel | None,
    token: str | None = None,
    source: str = "app",
    submitted_at: datetime | None = None,
    ner: Any = True,
) -> tuple[FormResponse, Assessment, Alert | None, AssessResult]:
    at = submitted_at or now()
    # 1) Đánh giá TRƯỚC khi ghi: chỉ có lệnh đọc → không giữ khóa ghi SQLite trong lúc chờ AI (có thể vài chục giây).
    ga = ga_on(patient, at.date())
    # Quy tắc đã đỏ → assess_answers tự bỏ qua LLM (skip_llm khi rule.level đỏ); dò văn bản xác định vẫn chạy.
    result = assess_answers(
        form_code, answers, free_text, ga[0] if ga else None, rules, forms_cfg, client=client,
        history=history_for(s, patient.id, as_of=at), on_insulin=patient.on_insulin, risk_groups=list(patient.risk_groups or []),
        ga_label=stage_text(patient, at.date()), patient_name=patient_name(patient), pid=patient.pid, ner=ner, at=at,
    )
    # 2) Ghi – một giao dịch ngắn.
    if token:
        _mark_token_used(s, token, at)
    resp = FormResponse(token=token, patient_id=patient.id, form_code=form_code, submitted_at=at,
                        answers=answers, free_text_enc=encrypt(free_text) if free_text else None, source=source)
    s.add(resp)
    s.flush()
    for kind, value, _slot in result.rule.readings:
        measured = at
        # Nhãn AM/PM không chứng minh giờ thực đo. Không tạo timestamp tương lai.
        s.add(VitalReading(patient_id=patient.id, measured_at=measured, kind=kind, value=value,
                           source_response_id=resp.id))
    asm = Assessment(response_id=resp.id, level=result.level.code, rule_level=result.rule_level.code,
                     trend_level=result.trend_level.code, ai_level=result.ai_level.code,
                     ai_confidence=result.reading.confidence, findings=result.findings_json(rules),
                     summary=result.summary, rules_version=rules.rules_version,
                     prompt_version=result.reading.prompt_version or None, model=result.reading.model or None,
                     raw={"llm": result.reading.raw, "summary_method": result.summary_method,
                          "summary_prompt_version": _summary_version() if result.summary_method == "llm" else None},
                     created_at=at)
    s.add(asm)
    s.flush()
    alert = None
    if result.level >= Level.YELLOW:
        alert = Alert(assessment_id=asm.id, level=result.level.code, status="open", created_at=at)
        s.add(alert)
        s.flush()
        if result.level == Level.RED and alert_channel is not None:
            _send_after_commit(s, alert.id, payload_for(alert, patient, _alert_reasons(result), at), alert_channel)
    audit.log("form_submitted", "form_response", resp.id,
              {"patient": patient.code, "form": form_code, "level": result.level.code, "source": source,
               "rules_version": rules.rules_version}, session=s)
    return resp, asm, alert, result


def structured_red(patient: Patient, answers: dict[str, Any], at: datetime, rules: Rules) -> bool:
    """Câu trả lời có cấu trúc đã đủ báo ĐỎ? Dùng để khỏi tạo / thăm dò client AI (cùng tuần thai với submit_response)."""
    ga = ga_on(patient, at.date())
    return triage(answers, ga[0] if ga else None, bool(patient.on_insulin), rules).level == Level.RED


def _alert_reasons(result: AssessResult) -> list[str]:
    """Lý do cho payload cảnh báo: chỉ tên quy tắc / khái niệm, không có văn bản tự do."""
    return [r for r in result.reasons if not r.startswith("Xu hướng:")] + \
        [r for r in result.reasons if r.startswith("Xu hướng:")]


def send_yellow_digest(s: Session, channel: AlertChannel) -> int:
    rows = s.execute(
        select(Alert, Assessment, FormResponse, Patient)
        .join(Assessment, Alert.assessment_id == Assessment.id)
        .join(FormResponse, Assessment.response_id == FormResponse.id)
        .join(Patient, FormResponse.patient_id == Patient.id)
        .where(Alert.level == "yellow", Alert.sent_at.is_(None))
    ).all()
    payloads = [payload_for(a, p, list((asm.findings or {}).get("reasons", [])), now()) for a, asm, _r, p in rows]
    if payloads:
        channel.send_digest(payloads)
        t = now()
        for a, *_ in rows:
            a.sent_at = t
    audit.log("send_yellow_digest", "alert", None, {"count": len(payloads)}, session=s)
    return len(payloads)


def handle_alert(s: Session, alert_id: int, user_id: int | None, *, status: str, note: str,
                 final_level: str | None) -> None:
    a = s.get(Alert, alert_id)
    assert a is not None
    a.status = status
    a.note = note
    if final_level:
        if final_level not in FINAL_LEVELS:
            raise ValueError(final_level)
        a.final_level = final_level
    a.handled_by = user_id
    a.handled_at = now()
    audit.log("handle_alert", "alert", alert_id, {"status": status, "final_level": final_level, "note": note},
              user_id=user_id, session=s)


# ---------------------------------------------------------------- ca đỏ: bác sĩ quyết định / xem lại ca hạ mức
DOCTOR_DECISIONS = {"visit_today": "Hẹn khám hôm nay", "emergency": "Chuyển cấp cứu", "monitor": "Theo dõi tiếp"}


class HandleError(ValueError):
    """Thao tác xử trí không hợp lệ theo chính sách ca đỏ."""


def needs_downgrade_review(alert_id: int, findings: dict[str, Any], rules: Rules) -> bool:
    """Ca đỏ bị điều dưỡng hạ mức có vào danh sách bác sĩ xem lại không. Hàm thuần.

    Lý do nguy hiểm nhất (rules.yaml → alert_review.always_review_*) luôn xem; còn lại lấy mẫu ổn định theo mã
    cảnh báo với tỷ lệ alert_review.downgrade_sample_rate (1.0 = xem tất cả).
    """
    cfg = rules["alert_review"]
    codes = {x.get("code") for x in findings.get("rule", [])}
    concepts = {x.get("concept") for x in findings.get("ai", [])
                if x.get("present") and x.get("level") in ("red", "yellow")}
    if codes & set(cfg["always_review_rule_codes"]) or concepts & set(cfg["always_review_concepts"]):
        return True
    rate = float(cfg["downgrade_sample_rate"])
    bucket = int(hashlib.sha256(f"alert-{alert_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


def _pending(s: Session, alert_id: int, kind: str) -> AlertReview | None:
    return s.scalar(select(AlertReview).where(AlertReview.alert_id == alert_id, AlertReview.kind == kind,
                                              AlertReview.status == "pending"))


def close_alert(s: Session, alert_id: int, *, user_id: int | None, role: str, final_level: str | None, note: str,
                decision: str | None, rules: Rules) -> str:
    """Ghi xử trí và đóng theo chính sách ca đỏ. Trả trạng thái: closed | awaiting_doctor.

    - Đỏ + "Giữ đỏ": điều dưỡng → chuyển "Chờ bác sĩ"; bác sĩ → phải chọn quyết định (DOCTOR_DECISIONS) rồi mới đóng.
    - Đỏ + hạ vàng / báo nhầm: đóng; nếu do điều dưỡng hạ → có thể vào danh sách bác sĩ xem lại.
    - Đỏ luôn phải có "mức sau gọi". Vàng: như trước.
    """
    a = s.get(Alert, alert_id)
    if a is None:
        raise HandleError("Không tìm thấy cảnh báo.")
    if a.status == "closed":
        raise HandleError("Cảnh báo đã đóng.")
    if final_level is not None and final_level not in FINAL_LEVELS:
        raise HandleError("Mức sau khi gọi không hợp lệ.")
    if a.level != "red":
        handle_alert(s, alert_id, user_id, status="closed", note=note, final_level=final_level)
        return "closed"
    if final_level is None:
        raise HandleError("Cảnh báo đỏ: chọn mức sau khi gọi (Giữ đỏ / Hạ vàng / Báo nhầm) trước khi đóng.")
    waiting = _pending(s, alert_id, "doctor_decision")
    if final_level == "keep_red":
        if role != "doctor":
            handle_alert(s, alert_id, user_id, status="escalated", note=note, final_level=final_level)
            if waiting is None:
                s.add(AlertReview(alert_id=alert_id, kind="doctor_decision", status="pending", created_at=now()))
            return "awaiting_doctor"
        if decision not in DOCTOR_DECISIONS:
            raise HandleError("Bác sĩ chọn quyết định xử trí: " + " / ".join(DOCTOR_DECISIONS.values()) + ".")
        handle_alert(s, alert_id, user_id, status="closed", note=note, final_level=final_level)
        row = waiting or AlertReview(alert_id=alert_id, kind="doctor_decision", created_at=now())
        row.status, row.decision, row.note, row.reviewed_by, row.reviewed_at = "done", decision, note, user_id, now()
        s.add(row)
        audit.log("doctor_decision", "alert", alert_id, {"decision": decision}, user_id=user_id, session=s)
        return "closed"
    # hạ vàng / báo nhầm
    handle_alert(s, alert_id, user_id, status="closed", note=note, final_level=final_level)
    if waiting is not None:  # bác sĩ (hoặc điều dưỡng gọi lại) hạ ca đang chờ bác sĩ
        waiting.status, waiting.reviewed_by, waiting.reviewed_at = "done", user_id, now()
    if role != "doctor":
        asm = s.get(Assessment, a.assessment_id)
        if needs_downgrade_review(alert_id, (asm.findings if asm else None) or {}, rules):
            s.add(AlertReview(alert_id=alert_id, kind="downgrade_review", status="pending", created_at=now()))
    return "closed"


def ack_downgrade_reviews(s: Session, review_ids: list[int], user_id: int | None) -> int:
    """Bác sĩ "Đã xem" (đồng ý với việc hạ mức)."""
    n = 0
    for r in s.scalars(select(AlertReview).where(AlertReview.id.in_(review_ids), AlertReview.kind == "downgrade_review",
                                                 AlertReview.status == "pending")):
        r.status, r.outcome, r.reviewed_by, r.reviewed_at = "done", "agree", user_id, now()
        n += 1
    audit.log("ack_downgrade_reviews", "alert_review", None, {"ids": review_ids, "count": n}, user_id=user_id, session=s)
    return n


def reopen_from_review(s: Session, review_id: int, user_id: int | None) -> int:
    """Bác sĩ không đồng ý hạ mức → mở lại cảnh báo ở trạng thái "Chờ bác sĩ". Trả alert_id."""
    r = s.get(AlertReview, review_id)
    if r is None or r.kind != "downgrade_review" or r.status != "pending":
        raise HandleError("Mục xem lại không tồn tại hoặc đã xử lý.")
    r.status, r.outcome, r.reviewed_by, r.reviewed_at = "done", "reopen", user_id, now()
    a = s.get(Alert, r.alert_id)
    assert a is not None
    a.status = "escalated"
    s.add(AlertReview(alert_id=a.id, kind="doctor_decision", status="pending", created_at=now()))
    audit.log("reopen_alert", "alert", a.id, {"from_review": review_id}, user_id=user_id, session=s)
    return a.id


def escalate_alert(s: Session, alert_id: int, user_id: int | None, note: str) -> None:
    """"Đã báo bác sĩ": chuyển "Chờ bác sĩ" và tạo việc cho bác sĩ (nếu chưa có)."""
    handle_alert(s, alert_id, user_id, status="escalated", note=note, final_level=None)
    if _pending(s, alert_id, "doctor_decision") is None:
        s.add(AlertReview(alert_id=alert_id, kind="doctor_decision", status="pending", created_at=now()))


def alert_rows(s: Session, include_closed: bool = False) -> list[tuple[Alert, Assessment, FormResponse, Patient]]:
    q = (select(Alert, Assessment, FormResponse, Patient)
         .join(Assessment, Alert.assessment_id == Assessment.id)
         .join(FormResponse, Assessment.response_id == FormResponse.id)
         .join(Patient, FormResponse.patient_id == Patient.id))
    if not include_closed:
        q = q.where(Alert.status != "closed")
    rows = list(s.execute(q).tuples().all())
    # Danh sách gọi: đỏ trước vàng; chưa xử lý trước; trong cùng nhóm, chờ lâu nhất lên trước.
    order = {"red": 0, "yellow": 1}
    rows.sort(key=lambda r: (order.get(r[0].level, 2), r[0].status != "open", (r[0].created_at or now()).timestamp()))
    return rows


def _summary_version() -> str:
    """prompt_version của câu tóm tắt do AI viết lại (luật lưu kết quả AI kèm phiên bản prompt)."""
    from core.config import prompt_text

    return prompt_text("summary")[1]
