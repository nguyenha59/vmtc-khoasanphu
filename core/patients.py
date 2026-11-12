"""Quản lý hồ sơ thai phụ: tạo hồ sơ, chương trình theo dõi (theo gói / bác sĩ chỉ định), kết quả khám.

Mọi kiểm tra dữ liệu đầu vào nằm ở đây (không ở API) để mọi đường gọi dùng chung một luật.
Tên, SĐT, kết quả khám lưu mã hóa (Fernet). Ghi nhật ký do lớp API làm (biết người thao tác).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import Rules
from core.db import now
from core.models import Patient, PatientProgram, VisitResult
from core.repo import next_code
from core.security import decrypt, encrypt, is_valid_phone, normalize_phone

PID_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")
MIN_REASON = 10


class PatientError(ValueError):
    """Dữ liệu hồ sơ không hợp lệ – thông báo bằng tiếng Việt, hiện thẳng cho người dùng."""


@dataclass(frozen=True)
class NewPatient:
    pid: str
    full_name: str
    phone: str
    nationality: str = "Việt Nam"
    edd: date | None = None
    in_package: bool = False
    package_name: str | None = None


def validate_new(p: NewPatient, today: date) -> NewPatient:
    pid = p.pid.strip()
    name = " ".join(p.full_name.split())
    phone = normalize_phone(p.phone)
    if not PID_RE.match(pid):
        raise PatientError("PID chỉ gồm chữ và số, 4–20 ký tự.")
    if len(name) < 3 or len(name.split()) < 2:
        raise PatientError("Họ tên phải có ít nhất họ và tên.")
    if not is_valid_phone(phone):
        raise PatientError("Số điện thoại không hợp lệ (10 số, bắt đầu bằng 0).")
    if p.edd is not None and not (today <= p.edd <= date.fromordinal(today.toordinal() + 300)):
        raise PatientError("Ngày dự sinh phải từ hôm nay đến tối đa khoảng 43 tuần nữa.")
    pkg = (p.package_name or "").strip() or None
    if p.in_package and not pkg:
        raise PatientError("Chọn tên gói khi thai phụ theo gói.")
    return NewPatient(pid=pid, full_name=name, phone=phone, nationality=(p.nationality or "Việt Nam").strip(),
                      edd=p.edd, in_package=p.in_package, package_name=pkg if p.in_package else None)


def create_patient(s: Session, data: NewPatient, user_id: int | None, today: date) -> Patient:
    d = validate_new(data, today)
    if s.scalar(select(Patient.id).where(Patient.pid == d.pid)) is not None:
        raise PatientError(f"PID {d.pid} đã có hồ sơ.")
    p = Patient(pid=d.pid, full_name_enc=encrypt(d.full_name) or "", phone_enc=encrypt(d.phone) or "",
                nationality=d.nationality, code=next_code(s), risk_groups=[], proposed_risk_flags=[], edd=d.edd)
    s.add(p)
    s.flush()
    s.add(PatientProgram(patient_id=p.id, in_package=d.in_package, package_name=d.package_name,
                         package_since=today if d.in_package else None, updated_by=user_id, updated_at=now()))
    return p


# ---------------------------------------------------------------- chương trình theo dõi
def program_of(s: Session, patient_id: int) -> dict[str, Any]:
    pr = s.get(PatientProgram, patient_id)
    if pr is None:
        return {"in_package": False, "package_name": None, "package_since": None, "override_monitoring": False,
                "override_reason": None}
    return {"in_package": pr.in_package, "package_name": pr.package_name,
            "package_since": pr.package_since.isoformat() if pr.package_since else None,
            "override_monitoring": pr.override_monitoring, "override_reason": pr.override_reason}


def set_program(s: Session, patient_id: int, *, in_package: bool, package_name: str | None,
                override_monitoring: bool, override_reason: str | None, user_id: int | None,
                can_override: bool, today: date) -> dict[str, Any]:
    """Cập nhật chương trình. Chỉ người có quyền (bác sĩ) mới đổi chỉ định ngoài gói; bắt buộc ghi lý do."""
    if s.get(Patient, patient_id) is None:
        raise PatientError("Không tìm thấy thai phụ.")
    pr = s.get(PatientProgram, patient_id) or PatientProgram(patient_id=patient_id)
    pkg = (package_name or "").strip() or None
    if in_package and not pkg:
        raise PatientError("Chọn tên gói khi thai phụ theo gói.")
    reason = (override_reason or "").strip() or None
    if override_monitoring != bool(pr.override_monitoring) or (override_monitoring and reason != pr.override_reason):
        if not can_override:
            raise PermissionError("Chỉ bác sĩ được chỉ định theo dõi từ xa ngoài gói.")
        if override_monitoring and (not reason or len(reason) < MIN_REASON):
            raise PatientError(f"Ghi lý do chỉ định (ít nhất {MIN_REASON} ký tự), ví dụ: tăng huyết áp, tiền sản giật.")
    if in_package and not pr.in_package:
        pr.package_since = today
    pr.in_package, pr.package_name = in_package, pkg if in_package else None
    if not in_package:
        pr.package_since = None
    pr.override_monitoring = override_monitoring
    pr.override_reason = reason if override_monitoring else None
    pr.updated_by, pr.updated_at = user_id, now()
    s.add(pr)
    s.flush()
    return program_of(s, patient_id)


def monitoring_eligible(program: dict[str, Any], rules: Rules) -> bool:
    """Được theo dõi từ xa (gửi form)? package_only = True: chỉ khách theo gói hoặc bác sĩ chỉ định."""
    cfg = (rules.get("forms.eligibility") or {})
    if not cfg.get("package_only", False):
        return True
    return bool(program.get("in_package") or program.get("override_monitoring"))


# ---------------------------------------------------------------- kết quả khám
def save_visit_result(s: Session, appointment_id: int, data: dict[str, Any], *, source: str,
                      reference_version: str | None, at: datetime | None = None) -> None:
    row = s.get(VisitResult, appointment_id) or VisitResult(appointment_id=appointment_id)
    row.data_enc = encrypt(json.dumps(data, ensure_ascii=False)) or ""
    row.source, row.reference_version, row.created_at = source, reference_version, at or now()
    s.add(row)


def visit_results(s: Session, appointment_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not appointment_ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for r in s.scalars(select(VisitResult).where(VisitResult.appointment_id.in_(appointment_ids))):
        raw = decrypt(r.data_enc)
        if raw:
            out[r.appointment_id] = {**json.loads(raw), "source": r.source, "reference_version": r.reference_version}
    return out


# ---------------------------------------------------------------- thông tin sản khoa (tiền sử, PARA, cuộc sinh)
def save_history(s: Session, patient_id: int, data: dict[str, Any], *, source: str, at: datetime | None = None) -> None:
    from core.models import PatientHistory

    row = s.get(PatientHistory, patient_id) or PatientHistory(patient_id=patient_id)
    row.data_enc = encrypt(json.dumps(data, ensure_ascii=False)) or ""
    row.source, row.updated_at = source, at or now()
    s.add(row)


def history_of(s: Session, patient_id: int) -> dict[str, Any] | None:
    from core.models import PatientHistory

    row = s.get(PatientHistory, patient_id)
    raw = decrypt(row.data_enc) if row else None
    return {**json.loads(raw), "source": row.source} if raw and row else None
