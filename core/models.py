"""Bảng cơ sở dữ liệu (docs/03 mục 2)."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))  # nurse | doctor | admin


class Patient(Base):
    __tablename__ = "patient"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)  # R0001…
    pid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    full_name_enc: Mapped[str] = mapped_column(Text)
    phone_enc: Mapped[str] = mapped_column(Text)
    nationality: Mapped[str | None] = mapped_column(String(64))
    consent_status: Mapped[str] = mapped_column(String(16), default="none")  # none/granted/withdrawn
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    consent_by: Mapped[int | None] = mapped_column(Integer)
    risk_groups: Mapped[list[str]] = mapped_column(JSON, default=list)  # module đã xác nhận: M-THA, M-DTD
    proposed_risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)  # từ note_extraction
    on_insulin: Mapped[bool] = mapped_column(default=False)
    edd: Mapped[date | None] = mapped_column(Date)  # ngày dự sinh suy từ tuần thai
    delivery_date: Mapped[date | None] = mapped_column(Date)


class ImportBatch(Base):
    __tablename__ = "import_batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # his/arrivals/forms
    file_name: Mapped[str] = mapped_column(String(256))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[Any] = mapped_column(JSON, default=list)
    target_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Appointment(Base):
    __tablename__ = "appointment"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    appt_date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    services: Mapped[list[str]] = mapped_column(JSON, default=list)
    department: Mapped[str | None] = mapped_column(String(128))
    doctor: Mapped[str | None] = mapped_column(String(128))
    note_enc: Mapped[str | None] = mapped_column(Text)
    booking_source: Mapped[str | None] = mapped_column(String(64))
    booked_at: Mapped[datetime | None] = mapped_column(DateTime)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id"))
    merged_from: Mapped[list[int]] = mapped_column(JSON, default=list)
    row_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)


class NoteExtraction(Base):
    __tablename__ = "note_extraction"
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointment.id"), index=True)
    ga_weeks: Mapped[int | None] = mapped_column(Integer)
    ga_days: Mapped[int | None] = mapped_column(Integer)
    risk_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    arrival_hint: Mapped[str | None] = mapped_column(String(32))
    votes: Mapped[Any] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rules_version: Mapped[str | None] = mapped_column(String(16))
    masked_note: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[Any] = mapped_column(JSON, default=dict)


class ArrivalPlan(Base):
    __tablename__ = "arrival_plan"
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointment.id"), index=True)
    arrival_time: Mapped[time | None] = mapped_column(Time)
    proposed_time: Mapped[time | None] = mapped_column(Time)  # giờ hệ thống đề xuất ban đầu
    method: Mapped[str] = mapped_column(String(16))  # rule/ai/manual
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))  # auto/needs_review/confirmed
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    details: Mapped[Any] = mapped_column(JSON, default=dict)
    rules_version: Mapped[str] = mapped_column(String(16))
    confirmed_by: Mapped[int | None] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class FormSchedule(Base):
    __tablename__ = "form_schedule"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    send_date: Mapped[date] = mapped_column(Date, index=True)
    form_code: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))  # planned/exported/skipped/cancelled
    skip_reason: Mapped[str | None] = mapped_column(Text)
    token: Mapped[str | None] = mapped_column(String(32))
    rules_version: Mapped[str | None] = mapped_column(String(16))


class FormToken(Base):
    __tablename__ = "form_token"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    form_code: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime)


class FormResponse(Base):
    __tablename__ = "form_response"
    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str | None] = mapped_column(String(32))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    form_code: Mapped[str] = mapped_column(String(16))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    answers: Mapped[Any] = mapped_column(JSON, default=dict)
    free_text_enc: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="app")  # app/msforms/seed


class Assessment(Base):
    __tablename__ = "assessment"
    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[int] = mapped_column(ForeignKey("form_response.id"), index=True)
    level: Mapped[str] = mapped_column(String(8))
    rule_level: Mapped[str] = mapped_column(String(8))
    trend_level: Mapped[str] = mapped_column(String(8))
    ai_level: Mapped[str] = mapped_column(String(8))
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    findings: Mapped[Any] = mapped_column(JSON, default=dict)
    summary: Mapped[str | None] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)


class Alert(Base):
    __tablename__ = "alert"
    id: Mapped[int] = mapped_column(primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessment.id"), index=True)
    level: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/called/escalated/closed
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)  # đã gửi qua AlertChannel
    handled_by: Mapped[int | None] = mapped_column(Integer)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)
    final_level: Mapped[str | None] = mapped_column(String(16))  # keep_red/down_yellow/false_alarm


class VitalReading(Base):
    __tablename__ = "vital_reading"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patient.id"), index=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # bp_sys/bp_dia/glu_fast/glu_1h/glu_2h/weight
    value: Mapped[float] = mapped_column(Float)
    source_response_id: Mapped[int | None] = mapped_column(Integer)


class NoshowPrediction(Base):
    __tablename__ = "noshow_prediction"
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointment.id"), index=True)
    prob: Mapped[float] = mapped_column(Float)
    top_factors: Mapped[Any] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Arrival(Base):
    """Danh sách khách đến (docs/03 mục 1.2)."""

    __tablename__ = "arrival"
    id: Mapped[int] = mapped_column(primary_key=True)
    pid: Mapped[str] = mapped_column(String(32), index=True)
    visit_date: Mapped[date] = mapped_column(Date, index=True)
    checkin_time: Mapped[time | None] = mapped_column(Time)
    batch_id: Mapped[int | None] = mapped_column(Integer)


class RuleChange(Base):
    __tablename__ = "rule_change"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128))
    old_value: Mapped[Any] = mapped_column(JSON)
    new_value: Mapped[Any] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    proposed_by: Mapped[int | None] = mapped_column(Integer)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    new_rules_version: Mapped[str | None] = mapped_column(String(16))


class MessageLog(Base):
    __tablename__ = "message_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    template: Mapped[str] = mapped_column(String(64))
    patient_code: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AlertReview(Base):
    """Việc của bác sĩ trên cảnh báo đỏ.

    kind = doctor_decision: điều dưỡng gọi xong vẫn "Giữ đỏ" → bác sĩ chọn quyết định xử trí (nút soạn sẵn).
    kind = downgrade_review: điều dưỡng hạ mức ca đỏ → bác sĩ xem lại trong ngày (theo tỷ lệ mẫu / lý do luôn xem).
    """

    __tablename__ = "alert_review"
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alert.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | done
    decision: Mapped[str | None] = mapped_column(String(32))  # doctor_decision: visit_today | emergency | monitor
    outcome: Mapped[str | None] = mapped_column(String(16))  # downgrade_review: agree | reopen
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_by: Mapped[int | None] = mapped_column(Integer)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserSession(Base):
    """Phiên đăng nhập phía máy chủ: cookie chỉ chứa mã phiên ngẫu nhiên; ở đây lưu bản băm SHA-256 của mã."""

    __tablename__ = "user_session"
    sid_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[Any] = mapped_column(JSON, default=dict)


class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    dataset: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rules_version: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[Any] = mapped_column(JSON, default=dict)
