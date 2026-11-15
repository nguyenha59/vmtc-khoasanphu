"""Theo dõi tại nhà (GIẢ) cho 5 hồ sơ nguy cơ trong scripts/gen_cases.py – để hồ sơ có đủ: số đo tại nhà, nhật ký form,
cảnh báo và xử trí. KHÔNG dùng dữ liệu thật.

Mọi câu trả lời đi qua pipeline thật (core.alerts.submit_response: quy tắc → xu hướng → đọc phần tự viết) nên mức cảnh
báo, lý do và căn cứ đúng như khi thai phụ gửi thật. Xử trí ghi qua close_alert / escalate_alert (có audit_log).

python scripts/gen_monitoring.py      # làm lại phần theo dõi của 5 ca (gọi tự động ở cuối scripts/gen_cases.py)
"""

from __future__ import annotations

import argparse
import secrets
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import TZ, load_env, load_forms, load_rules  # noqa: E402
from core.models import (  # noqa: E402
    Alert, AlertReview, Appointment, Assessment, FormResponse, FormToken, Patient, User, VitalReading,
)


@dataclass
class Event:
    days_ago: int
    form: str
    answers: dict[str, Any]
    free_text: str = ""
    hour: int = 20
    # Xử trí sau khi gọi: None = để mở (ca đang chờ). ("close", final_level, note) | ("escalate_doctor", decision, note)
    handle: tuple[str, str | None, str] | None = ("close", None, "")
    answered: bool = True                 # False = gửi đường dẫn nhưng thai phụ không trả lời


@dataclass
class Plan:
    pid: str
    story: str
    events: list[Event] = field(default_factory=list)


NO = "Không"
YES = "Có"


def _tha(days_ago: int, am: tuple[int, int], pm: tuple[int, int], *, c6: str = NO, c7: str = NO, missed: str = NO,
         free: str = "", handle: tuple[str, str | None, str] | None = ("close", None, ""), answered: bool = True) -> Event:
    return Event(days_ago, "M-THA", {"BP_AM_SYS": am[0], "BP_AM_DIA": am[1], "BP_PM_SYS": pm[0], "BP_PM_DIA": pm[1],
                                     "C6": c6, "C7": c7, "MISSED_MED": missed}, free, handle=handle, answered=answered)


def _dtd(days_ago: int, fast: float, h1: float, h2: float, *, hypo: str = NO,
         handle: tuple[str, str | None, str] | None = ("close", None, "")) -> Event:
    return Event(days_ago, "M-DTD", {"GLU_FAST": fast, "GLU_1H": h1, "GLU_2H": h2, "HYPO": hypo, "MISSED_MED": NO},
                 handle=handle)


def _f5(days_ago: int, weight: float, **yes: str) -> dict[str, Any]:
    base = {q: NO for q in ("C1", "C2", "C3", "C4", "C6", "C7", "C8", "C9", "ITCH")}
    return {**base, **yes, "WEIGHT": weight}


PLANS: list[Plan] = [
    Plan("HS000115", "THA từ 28 tuần, bác sĩ chỉ định theo dõi từ xa: HA tăng dần → vàng (đã gọi) → hôm qua 164/108 "
         "kèm đau đầu không đỡ, nhìn mờ → ĐỎ, đang chờ xử trí.", [
        _tha(32, (128, 82), (130, 84)),
        _tha(29, (126, 80), (131, 83)),
        _tha(25, (130, 84), (134, 86)),
        _tha(22, (132, 85), (135, 86), missed=YES,
             handle=("close", None, "Đã gọi: quên 1 liều methyldopa tối qua. Dặn đặt nhắc giờ uống thuốc, HA đo lại 132/84.")),
        _tha(18, (134, 86), (138, 88)),
        _tha(15, (136, 88), (142, 92),
             handle=("close", None, "Đã gọi: không đau đầu, không nhìn mờ, không phù. Đo lại sau nghỉ 15 phút 136/88. "
                                     "Dặn đo 2 lần/ngày, có đau đầu / nhìn mờ thì đến khoa ngay.")),
        _tha(11, (138, 90), (144, 94), c7=YES,
             handle=("close", None, "Đã gọi: phù nhẹ mu bàn tay buổi sáng, không đau đầu. Báo bác sĩ Khải – "
                                     "hẹn khám sớm, xét nghiệm protein niệu.")),
        _tha(8, (136, 88), (140, 90)),
        _tha(4, (142, 92), (148, 96),
             handle=("close", None, "Đã gọi: không triệu chứng. Bác sĩ chỉnh liều methyldopa, giữ lịch hẹn.")),
        _tha(1, (158, 104), (164, 108), c6=YES, c7=YES,
             free="Từ chiều qua em đau đầu nhiều, uống paracetamol không đỡ, nhìn hơi mờ.", handle=None),
    ]),
    Plan("HS000104", "THA thai kỳ 36 tuần: HA ổn trong ngưỡng nhưng tăng dần 3 lần liên tiếp → vàng theo xu hướng; "
         "1 lần không trả lời form.", [
        _tha(30, (118, 76), (120, 78)),
        _tha(27, (120, 78), (122, 78)),
        _tha(23, (119, 76), (121, 77)),
        _tha(20, (121, 78), (122, 79)),
        _tha(16, (120, 77), (123, 79), answered=False),
        _tha(13, (124, 80), (126, 82)),
        _tha(9, (128, 82), (131, 84)),
        _tha(6, (131, 84), (134, 86),
             handle=("close", None, "Đã gọi: không đau đầu, không phù, thai đạp tốt. HA tăng dần nhưng < 140/90 – "
                                     "báo bác sĩ, dặn đo sáng/tối hằng ngày, giữ lịch khám 37 tuần.")),
        _tha(2, (133, 85), (137, 88), handle=None),
    ]),
    Plan("HS000103", "ĐTĐ thai kỳ (ăn kiêng, chưa insulin): đường huyết phần lớn trong mục tiêu, 2 lần vượt ngưỡng sau "
         "ăn → vàng, đã tư vấn dinh dưỡng.", [
        _dtd(35, 4.9, 7.2, 6.3),
        _dtd(28, 5.0, 7.4, 6.4),
        _dtd(21, 5.1, 8.3, 6.9,
             handle=("close", None, "Đã gọi: ăn xôi + nước ngọt bữa sáng. Tư vấn chia nhỏ bữa, hẹn khám dinh dưỡng.")),
        _dtd(14, 4.8, 7.1, 6.2),
        _dtd(7, 5.4, 7.9, 6.6,
             handle=("close", None, "Đã gọi: ngủ muộn, ăn đêm. Nhắc chế độ ăn; báo bác sĩ cân nhắc insulin nếu tiếp tục vượt.")),
        _dtd(0, 5.0, 7.3, 6.4),
    ]),
    Plan("HS000105", "Song thai 30 tuần: form mốc F5 báo gò bụng đều → ĐỎ; điều dưỡng giữ đỏ → bác sĩ hẹn khám ngay "
         "trong ngày, kết quả ổn.", [
        Event(9, "F5", _f5(9, 68.5)),
        Event(3, "F5", _f5(3, 69.4, C9=YES), "Từ tối qua bụng gò cứng 4-5 lần một giờ, em hơi lo.", hour=7,
              handle=("escalate_doctor", "visit_today",
                      "Bác sĩ Hằng: khám trong ngày – monitor 40 phút không có cơn co đều, cổ tử cung dài 32 mm. "
                      "Cho về, nghỉ ngơi, tái khám theo lịch song thai 2 tuần/lần.")),
    ]),
    Plan("HS000108", "Tiền sử dọa sinh non 26 tuần: form F5 trả lời “Không chắc” gò bụng → vàng, gọi xác minh là căng "
         "bụng khi đi lại; các lần khác bình thường.", [
        Event(12, "F5", _f5(12, 58.0)),
        Event(5, "F5", _f5(5, 58.6, C9="Không chắc"), "Đi lại nhiều thì bụng căng, nằm nghỉ thì đỡ.",
              handle=("close", None, "Đã gọi: căng bụng khi đi lại, nghỉ 30 phút thì hết, không ra nước / ra máu. "
                                     "Hướng dẫn đếm cơn gò, đến khoa nếu ≥ 4 cơn/giờ.")),
        Event(0, "F5", _f5(0, 59.1)),
    ]),
]


def remove_monitoring(s: Session, patient_ids: list[int]) -> None:
    """Xóa dữ liệu theo dõi tại nhà (form, đánh giá, cảnh báo, số đo, đường dẫn) của các thai phụ này."""
    if not patient_ids:
        return
    resp_ids = list(s.scalars(select(FormResponse.id).where(FormResponse.patient_id.in_(patient_ids))))
    asm_ids = list(s.scalars(select(Assessment.id).where(Assessment.response_id.in_(resp_ids)))) if resp_ids else []
    alert_ids = list(s.scalars(select(Alert.id).where(Alert.assessment_id.in_(asm_ids)))) if asm_ids else []
    if alert_ids:
        s.execute(delete(AlertReview).where(AlertReview.alert_id.in_(alert_ids)))
        s.execute(delete(Alert).where(Alert.id.in_(alert_ids)))
    if asm_ids:
        s.execute(delete(Assessment).where(Assessment.id.in_(asm_ids)))
    s.execute(delete(VitalReading).where(VitalReading.patient_id.in_(patient_ids)))
    if resp_ids:
        s.execute(delete(FormResponse).where(FormResponse.id.in_(resp_ids)))
    s.execute(delete(FormToken).where(FormToken.patient_id.in_(patient_ids)))
    s.flush()


def _user_id(s: Session, role: str) -> int | None:
    return s.scalar(select(User.id).where(User.role == role).order_by(User.id))


def generate(s: Session, today: date) -> dict[str, int]:
    from core.ai.llm_client import MockClient
    from core.alerts import close_alert, submit_response
    from core.channels.alert_mock import MockAlertChannel

    rules, forms_cfg = load_rules(), load_forms()
    client, channel = MockClient(), MockAlertChannel()
    nurse, doctor = _user_id(s, "nurse"), _user_id(s, "doctor")
    by_pid = {p.pid: p for p in s.scalars(select(Patient).where(Patient.pid.in_([x.pid for x in PLANS])))}
    remove_monitoring(s, [p.id for p in by_pid.values()])
    n_resp = n_alert = 0
    for plan in PLANS:
        p = by_pid.get(plan.pid)
        if p is None:
            continue
        for ev in sorted(plan.events, key=lambda e: -e.days_ago):
            # Hôm nay: gửi lúc 7:15 sáng (không tạo thời điểm trong tương lai).
            at = datetime.combine(today - timedelta(days=ev.days_ago), time(ev.hour if ev.days_ago else 7, 15))
            sent = at - timedelta(hours=12)
            token = secrets.token_hex(8)
            s.add(FormToken(token=token, patient_id=p.id, form_code=ev.form, created_at=sent,
                            expires_at=sent + timedelta(days=int(rules["forms"]["token_ttl_days"])), used_at=None))
            s.flush()
            if not ev.answered:
                continue
            _r, _a, alert, _res = submit_response(
                s, p, ev.form, ev.answers, ev.free_text, rules=rules, forms_cfg=forms_cfg, client=client,
                alert_channel=channel, token=token, source="seed", submitted_at=at, ner=False)
            n_resp += 1
            if alert is None:
                continue
            n_alert += 1
            if ev.handle is None:
                continue
            kind, value, note = ev.handle
            done_at = at + timedelta(hours=14 if at.hour >= 18 else 2)
            if kind == "close":
                final = value or ("keep_red" if alert.level == "red" else None)
                close_alert(s, alert.id, user_id=nurse, role="nurse", final_level=final,
                            note=note or "Đã gọi: thai phụ ổn, dặn theo dõi tiếp.", decision=None, rules=rules)
            else:  # điều dưỡng giữ đỏ → chờ bác sĩ → bác sĩ quyết định
                nurse_note = "ĐD: đã gọi – gò cứng 4–5 cơn/giờ, không ra nước, không ra máu. Giữ đỏ, chuyển bác sĩ."
                close_alert(s, alert.id, user_id=nurse, role="nurse", final_level="keep_red", note=nurse_note,
                            decision=None, rules=rules)
                close_alert(s, alert.id, user_id=doctor, role="doctor", final_level="keep_red",
                            note=f"{nurse_note} | {note}", decision=value, rules=rules)
            alert.handled_at = done_at
            for r in s.scalars(select(AlertReview).where(AlertReview.alert_id == alert.id)):
                r.created_at = at + timedelta(hours=1)
                if r.reviewed_at:
                    r.reviewed_at = done_at
    # Hồ sơ đang theo dõi phải có lịch hẹn kế tiếp (lịch soạn sẵn có thể đã qua ngày khi chạy lại).
    for plan in PLANS:
        p = by_pid.get(plan.pid)
        if p is None or p.delivery_date is not None:
            continue
        if s.scalar(select(Appointment.id).where(Appointment.patient_id == p.id, Appointment.appt_date >= today)):
            continue
        last = s.scalars(select(Appointment).where(Appointment.patient_id == p.id)
                         .order_by(Appointment.appt_date.desc())).first()
        days = 3 if plan.pid == "HS000115" else 7  # ca THA vừa báo đỏ: hẹn sớm
        d = today + timedelta(days=days)
        s.add(Appointment(patient_id=p.id, appt_date=d, start_time=time(8, 30), end_time=time(9, 0),
                          services=["Khám thai"], department=last.department if last else None,
                          doctor=last.doctor if last else None, note_enc=None, booking_source="Bác sĩ hẹn",
                          booked_at=datetime.combine(today, time(9)), merged_from=[]))
    s.flush()
    return {"patients": len(by_pid), "responses": n_resp, "alerts": n_alert}


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_env()
    from core import db as dbmod

    dbmod.init_db()
    with dbmod.session_scope() as s:
        out = generate(s, datetime.now(TZ).date())
    print(f"Theo dõi tại nhà: {out['patients']} hồ sơ, {out['responses']} câu trả lời form, {out['alerts']} cảnh báo (dữ liệu giả).")


if __name__ == "__main__":
    main()
