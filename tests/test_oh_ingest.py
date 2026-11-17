"""File OH thật: 6 cột, ngày-giờ chung một ô, không có Dịch vụ / Khoa / Giờ kết thúc (17/11/2026).
Chỉ dữ liệu giả (tên, SĐT, mã BN giả)."""

from __future__ import annotations

import io
from datetime import date, datetime, time

import pandas as pd
import pytest

from core.ingest.his_reader import read_raw
from core.pipeline import process_his

D = date(2026, 11, 17)


def oh_file(rows: list[tuple[str, str, str]]) -> io.BytesIO:
    """rows: (giờ hẹn HH:MM, ghi chú, SĐT). Ô ngày-giờ là kiểu datetime của Excel như file OH."""
    data = []
    for i, (hhmm, note, phone) in enumerate(rows, start=1):
        hh, mm = (int(x) for x in hhmm.split(":"))
        data.append(
            {
                "HN": f"SYN{i:04d}",
                "Patient Name": f"Khách Giả {i}",
                "Appointment Start Date Time": datetime(D.year, D.month, D.day, hh, mm),
                "Appointment Resource": "BS Giả",
                "Appointment Comment": note,
                "Mobile": phone,
            }
        )
    buf = io.BytesIO()
    pd.DataFrame(data).to_excel(buf, index=False)
    buf.seek(0)
    buf.name = "oh.xlsx"  # type: ignore[attr-defined]
    return buf


@pytest.fixture()
def processed(his_cfg, rules):  # noqa: ANN001, ANN201
    rows = [
        ("11:00", "39w6d/SA/CTG tới 08h30", "0901000001"),
        ("08:30", "Chậm kinh", "0901000002"),
        ("09:00", "tkss thường", "0901000003"),
        ("14:30", "Có khí hư trắng", "0901000004"),
        ("09:00", "dky gói thai 27w", "0901000005"),
        ("08:45", "36w5, sa, ctg, kgm đến 8h30", "0901000006"),
        ("10:30", "tkss thường đến 7h", "0901000007"),
    ]
    res = process_his(read_raw(oh_file(rows)), his_cfg, rules, D, client=None, ner=False)
    assert res.ok, res.report.as_dicts()
    return {p.merged.pid: p for p in res.planned}


def test_oh_columns_accepted_and_datetime_split(processed) -> None:  # noqa: ANN001
    p = processed["SYN0001"]
    assert p.merged.appt_date == D and p.merged.start_time == time(11, 0)
    assert p.merged.doctor == "BS Giả" and p.merged.end_time is None


def test_explicit_time_kept_within_service_cap(processed) -> None:  # noqa: ANN001
    """CTG trần 180': giờ ghi rõ 08:30 cho lịch 11:00 (150') được giữ."""
    p = processed["SYN0001"]
    assert p.arrival.arrival_time == time(8, 30)
    assert p.arrival.max_minutes_early == 180


def test_doctor_time_trusted_even_far_ahead(processed) -> None:  # noqa: ANN001
    """Bác sĩ ghi 'đến 7h' cho lịch 10:30 → dùng đúng 7:00 (người dùng 17/11: giờ bác sĩ ghi là rõ ràng)."""
    p = processed["SYN0007"]
    assert p.arrival.arrival_time == time(7, 0)


def test_service_minutes_from_note(processed) -> None:  # noqa: ANN001
    assert processed["SYN0002"].arrival.arrival_time == time(
        8, 0
    )  # chậm kinh: SA đầu dò + βhCG → 30'
    assert processed["SYN0003"].arrival.arrival_time == time(8, 45)  # sau sinh: khám → 15'
    assert processed["SYN0004"].arrival.arrival_time == time(14, 0)  # khí hư: soi tươi → 30'
    assert processed["SYN0006"].arrival.arrival_time == time(8, 30)  # giờ bác sĩ ghi rõ được giữ nguyên


def test_inferred_fasting_needs_confirmation(processed) -> None:  # noqa: ANN001
    p = processed["SYN0005"]
    assert "SERVICE_CONFIRM" in p.decision.reasons and p.decision.status == "needs_review"
    assert any("nhịn ăn" in d for d in p.arrival.details)


def test_plan_lists_services_and_basis(processed) -> None:  # noqa: ANN001
    p = processed["SYN0001"]
    assert "Monitor tim thai (CTG/NST)" in p.service_names
    assert p.basis and all(isinstance(b, str) for b in p.basis)


def test_missing_oh_column_reported(his_cfg) -> None:  # noqa: ANN001
    from core.ingest.validators import check_columns

    raw = read_raw(oh_file([("09:00", "khám thai", "0901000001")]))
    rep = check_columns(raw.drop(columns=["Mobile"]), his_cfg)
    assert not rep.ok and "Mobile" in rep.fatal[0].message


# ---------------- AI ước lượng số phút đến trước theo nguồn (người dùng chốt 17/11/2026)
def _plan_with_ai(rules, note: str, answers: list[dict], start: time = time(10, 0)):  # noqa: ANN001, ANN201
    from core.pipeline import plan_appointment
    from core.rules.merge import MergedAppt, Slot

    class Fixed:
        name = model = "fixed"

        def __init__(self) -> None:
            self.i = 0

        def generate_json(self, prompt, schema, temperature=0.0, seed=None):  # noqa: ANN001, ANN201
            out = answers[self.i % len(answers)]
            self.i += 1
            return {"explicit_arrival_time": None, "minutes_early": None, "needs_fasting": False,
                    "ga_weeks": 38, "ga_days": None, "risk_flags": [], "asks_confirmation": False,
                    "evidence": note, "visit_type": "ANC", "services": ["KHAM", "CTG"], **out}

    m = MergedAppt(pid="SYN9", appt_date=D, start_time=start, end_time=None, services=[], note=note,
                   full_name="Khách Giả", phone="0901000009", phone_raw="", nationality="Việt Nam",
                   department="", doctor="", booking_source="", booked_at=None, slots=[Slot(start, "", note)])
    return plan_appointment(m, rules, Fixed(), [], ner=False)


def test_ai_estimate_with_sources_is_used(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "38w ctg", [{"arrival_minutes": 70, "minutes_basis": ["VMC_NST", "VMC_DEN_TRUOC"]}])
    assert p.arrival.arrival_time == time(8, 50)
    assert "AI_MINUTES" not in p.decision.reasons
    assert any("AI tính đến trước 70 phút" in d for d in p.arrival.details)
    assert any("Non-stress" in b for b in p.basis)


def test_ai_estimate_without_source_falls_back_and_reviews(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "38w ctg", [{"arrival_minutes": 70, "minutes_basis": []}])
    assert "AI_MINUTES" in p.decision.reasons and p.decision.status == "needs_review"


def test_ai_estimate_disagreement_uses_largest(rules) -> None:  # noqa: ANN001
    """Các lần chạy AI lệch nhau → lấy số lớn nhất có nguồn (đến sớm hơn = an toàn), không bắt xác nhận."""
    p = _plan_with_ai(rules, "38w ctg", [{"arrival_minutes": 30, "minutes_basis": ["VMC_CTG"]},
                                         {"arrival_minutes": 90, "minutes_basis": ["VMC_NST"]}])
    assert "AI_MINUTES" not in p.decision.reasons
    assert p.arrival.arrival_time == time(8, 30)  # 10:00 − 90'



def test_ai_estimate_over_safety_cap_reviews(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "khám thai", [{"arrival_minutes": 150, "minutes_basis": ["VMC_DEN_TRUOC"],
                                            "services": ["KHAM"], "ga_weeks": None}])
    assert "AI_MINUTES" in p.decision.reasons


def test_doctor_written_time_beats_ai_estimate(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "38w ctg đến 9h30", [{"arrival_minutes": 70, "minutes_basis": ["VMC_NST"]}])
    assert p.arrival.arrival_time == time(9, 30)


def test_ai_estimate_below_sourced_floor_lifted_to_floor(rules) -> None:  # noqa: ANN001
    """CTG tối thiểu 30' (VMC_CTG) + tiếp đón 10' → AI cho 15' thì nâng lên 40', không bắt xác nhận."""
    p = _plan_with_ai(rules, "33w nghi chuyen da, SA + CTG", [{"arrival_minutes": 15, "minutes_basis": ["VMC_DEN_TRUOC"],
                                                               "ga_weeks": 33}])
    assert "AI_MINUTES" not in p.decision.reasons
    assert p.arrival.arrival_time == time(9, 20)  # 10:00 − (10' tiếp đón + 30' CTG)



def test_note_service_time_used_and_reviewed(rules) -> None:  # noqa: ANN001
    """'21w/sa4d có hẹn 9h30' (lịch OH 11:30 = gặp bác sĩ) → đến 9:20 (tiếp đón 10'), tự áp dụng."""
    p = _plan_with_ai(rules, "21w/sa4d có hẹn 9h30", [{"arrival_minutes": 25, "minutes_basis": ["VMC_SA_TAM_SOAT"],
                                                       "ga_weeks": 21}], start=time(11, 30))
    assert p.arrival.arrival_time == time(9, 20)
    assert any("có hẹn 09:30" in d for d in p.arrival.details)


def test_self_booked_without_hn_gets_temp_id_and_review(his_cfg, rules) -> None:  # noqa: ANN001
    """Khách tự đặt (Autobooking) chưa có HN → mã tạm riêng từng dòng + Cần xác nhận; không làm hỏng cả file."""
    raw = read_raw(oh_file([("09:00", "Autobooking_MVM_ko tam ung_#1_Chậm kinh", "0901000001"),
                            ("10:00", "Autobooking_MVM_ko tam ung_#2_khám thai", "0901000002"),
                            ("11:00", "tkss thường", "0901000003")]))
    raw.loc[[0, 1], "HN"] = ""
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    assert res.ok, res.report.as_dicts()
    temp = [p for p in res.planned if p.merged.pid.startswith("TAM-")]
    assert len(temp) == 2 and len({p.merged.pid for p in temp}) == 2  # không gộp hai khách
    assert all("NO_PID" in p.decision.reasons and p.decision.status == "needs_review" for p in temp)


def test_missing_hn_not_self_booked_still_rejected(his_cfg, rules) -> None:  # noqa: ANN001
    raw = read_raw(oh_file([("09:00", "khám thai", "0901000001"), ("10:00", "tkss thường", "0901000002")]))
    raw.loc[[0], "HN"] = ""
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    assert not res.ok and res.report.fatal[0].code == "E07"


def test_parallel_planning_matches_sequential(his_cfg, rules, monkeypatch) -> None:  # noqa: ANN001
    """Khi gọi được OpenAI, các lịch xử lý song song – kết quả và thứ tự giống hệt chạy lần lượt."""
    from core.ai.llm_client import MockClient

    class FakeExternal(MockClient):
        def __init__(self) -> None:
            super().__init__(model="openai:fake")

    rows = [("11:00", "39w6d/SA/CTG tới 08h30", "0901000001"), ("08:30", "Chậm kinh", "0901000002"),
            ("09:00", "tkss thường", "0901000003"), ("14:30", "Có khí hư trắng", "0901000004"),
            ("08:45", "36w5, sa, ctg, khám gây mê đến 8h30", "0901000005")]
    raw = read_raw(oh_file(rows))
    monkeypatch.setenv("AI_PARALLEL_CALLS", "1")
    seq = process_his(raw, his_cfg, rules, D, client=FakeExternal(), ner=False)
    monkeypatch.setenv("AI_PARALLEL_CALLS", "4")
    par = process_his(raw, his_cfg, rules, D, client=FakeExternal(), ner=False)
    key = [(p.merged.pid, p.arrival.arrival_time, p.decision.reasons, p.service_names) for p in seq.planned]
    assert key == [(p.merged.pid, p.arrival.arrival_time, p.decision.reasons, p.service_names) for p in par.planned]


def test_note_services_only_when_doctor_wrote_them(rules) -> None:  # noqa: ANN001
    """Ghi chú ghi rõ dịch vụ → chỉ làm đúng dịch vụ đó (+ khám), không tự thêm theo tuần thai / loại khám."""
    p = _plan_with_ai(rules, "31w3d, 4d + khám", [{"arrival_minutes": 30, "minutes_basis": ["VMC_SA_TAM_SOAT"],
                                                  "services": ["KHAM", "SA_4D", "DOPPLER"], "ga_weeks": 31}])
    assert set(p.service_plans[0].codes) == {"SA_4D", "KHAM"}


def test_late_ga_with_doctor_time_needs_no_review(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "37w3d, đến 8h", [{"arrival_minutes": 45, "minutes_basis": ["VMC_CTG"], "ga_weeks": 37}])
    assert p.arrival.arrival_time == time(8, 0) and "LATE_GA" not in p.decision.reasons
    q = _plan_with_ai(rules, "38w ctg", [{"arrival_minutes": 45, "minutes_basis": ["VMC_CTG"], "ga_weeks": 38}])
    assert "LATE_GA" in q.decision.reasons  # không có giờ bác sĩ ghi → vẫn xác nhận
