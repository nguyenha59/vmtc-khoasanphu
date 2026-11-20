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
                    "evidence": note, "visit_type": "OTHER", "services": [], **out}

    m = MergedAppt(pid="SYN9", appt_date=D, start_time=start, end_time=None, services=[], note=note,
                   full_name="Khách Giả", phone="0901000009", phone_raw="", nationality="Việt Nam",
                   department="", doctor="", booking_source="", booked_at=None, slots=[Slot(start, "", note)])
    return plan_appointment(m, rules, Fixed(), [], ner=False)


def test_doctor_written_time_beats_ai_estimate(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "38w ctg đến 9h30", [{"arrival_minutes": 70, "minutes_basis": ["VMC_NST"]}])
    assert p.arrival.arrival_time == time(9, 30)


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


def test_late_ga_always_reviewed_even_with_doctor_time(rules) -> None:  # noqa: ANN001
    """Từ 37 tuần luôn cần xác nhận lại, kể cả khi bác sĩ đã ghi giờ đến (người dùng chốt 17/11/2026)."""
    p = _plan_with_ai(rules, "37w3d, đến 8h", [{"arrival_minutes": 45, "minutes_basis": ["VMC_CTG"], "ga_weeks": 37}])
    assert p.arrival.arrival_time == time(8, 0) and "LATE_GA" in p.decision.reasons


def test_waiting_slot_with_doctor_time_uses_that_time(rules) -> None:  # noqa: ANN001
    """Lịch khám chờ 12:00 nhưng ghi chú 'KH tới 10h' → giờ đến 10:00, không gắn khám chờ."""
    p = _plan_with_ai(rules, "Chuẩn bị hồ sơ làm LEEP, KH tới 10h",
                      [{"arrival_minutes": 60, "minutes_basis": ["VMC_LEEP"], "ga_weeks": None}], start=time(12, 0))
    assert p.arrival.arrival_time == time(10, 0) and not p.arrival.waiting
    assert "WAITING_NOTE_TIME" not in p.decision.reasons


def test_arrival_not_before_clinic_opening(rules) -> None:  # noqa: ANN001
    """Vinmec Times City mở 08:00 → giờ đến tính ra 06:50 (test đường huyết) đẩy về 07:45 + Cần xác nhận."""
    p = _plan_with_ai(rules, "25w NPDN", [{"arrival_minutes": 130, "minutes_basis": ["VMC_OGTT"], "ga_weeks": 25,
                                           "services": ["OGTT"]}], start=time(9, 0))
    assert p.arrival.arrival_time == time(7, 45) and "OPEN_TIME" in p.decision.reasons
    q = _plan_with_ai(rules, "khám thai đến 7h", [{"arrival_minutes": 15, "minutes_basis": ["VMC_DEN_TRUOC"],
                                                   "ga_weeks": None}], start=time(9, 0))
    assert q.arrival.arrival_time == time(7, 0) and "OPEN_TIME" in q.decision.reasons  # giữ giờ bác sĩ, vẫn kiểm


def test_any_shortfall_before_opening_needs_review(rules) -> None:  # noqa: ANN001
    """Lịch 08:00 cần đến trước 25' → 07:35 < 07:45: đổi thành 07:45 VÀ cần xác nhận (người dùng 17/11/2026)."""
    p = _plan_with_ai(rules, "30w2d, 4d + khám", [{"arrival_minutes": 25, "minutes_basis": ["VMC_SA_TAM_SOAT"],
                                                  "ga_weeks": 30}], start=time(8, 0))
    assert p.arrival.arrival_time == time(7, 45) and "OPEN_TIME" in p.decision.reasons


def test_other_department_services_dropped_and_merge_kept(his_cfg, rules) -> None:  # noqa: ANN001
    """Ghi chú IVF / chuyển phôi → bỏ khỏi lịch; 'thai IVF 20w' vẫn giữ; khách 2 lịch vẫn gộp về giờ sớm hơn."""
    raw = read_raw(oh_file([("08:00", "chuyển phôi ngày 5", "0901000001"),
                            ("09:00", "thai IVF 20w, khám thai", "0901000002"),
                            ("10:00", "IVF tư vấn kích trứng", "0901000003"),
                            ("11:00", "khám thai", "0901000004"),
                            ("09:30", "SA 2D", "0901000004")]))
    raw.loc[3, "HN"] = raw.loc[4, "HN"] = "SYNSAME"  # cùng một khách, 2 lịch
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    assert res.ok
    pids = {p.merged.pid for p in res.planned}
    assert "SYN0001" not in pids and "SYN0003" not in pids  # IVF bị bỏ
    assert "SYN0002" in pids  # thai IVF vẫn là khám thai
    same = next(p for p in res.planned if p.merged.pid == "SYNSAME")
    assert same.merged.start_time == time(9, 30) and same.merged.is_merged
    assert len(res.dropped) == 2


# ---------------- Code tự tính giờ đến (người dùng chốt 17/11/2026): tiếp đón 15' + tối thiểu có nguồn
def test_service_minutes_from_note(processed) -> None:  # noqa: ANN001
    assert processed["SYN0002"].arrival.arrival_time == time(7, 55)   # chậm kinh: tiếp đón 15' + SA đầu dò 20'
    assert processed["SYN0003"].arrival.arrival_time == time(8, 45)   # sau sinh: tiếp đón 15'
    assert processed["SYN0004"].arrival.arrival_time == time(14, 15)  # khí hư: soi tươi làm trong lúc khám → 15'
    assert processed["SYN0006"].arrival.arrival_time == time(8, 30)   # giờ bác sĩ ghi rõ được giữ nguyên


def test_conditional_service_not_timed(processed) -> None:  # noqa: ANN001
    """'dky gói thai 27w': test đường huyết chỉ 'có thể cần' – không tính giờ, không dặn nhịn ăn."""
    p = processed["SYN0005"]
    sp = p.service_plans[0]
    assert [x.code for x in sp.maybe] == ["OGTT"] and not sp.fasting
    assert p.arrival.arrival_time == time(8, 25)  # tiếp đón 15' + SA 20'
    assert any("Có thể cần" in d for d in p.arrival.details)


def test_code_computes_minutes_ignoring_ai_numbers(rules) -> None:  # noqa: ANN001
    """AI có trả số phút cũng không dùng: SA 4D 30' + tiếp đón 15' = 45'."""
    p = _plan_with_ai(rules, "31w3d, 4d + khám", [{"arrival_minutes": 90, "ga_weeks": 31}], start=time(10, 0))
    assert p.arrival.arrival_time == time(9, 15)
    assert "tiếp đón 15'" in p.arrival.rule_applied


def test_preterm_always_ctg_review_and_warning(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "Thai sớm/Chưa loại trừ chuyển dạ /SA trước", [{"ga_weeks": None}], start=time(11, 0))
    assert "CTG" in p.service_plans[0].codes
    assert p.arrival.arrival_time == time(9, 55)  # tiếp đón 15' + SA 20' + CTG 30'
    assert "URGENT" in p.decision.reasons and any("cấp cứu sản" in d for d in p.arrival.details)


def test_doctor_time_short_by_15_or_more_reviews(rules) -> None:  # noqa: ANN001
    """Bác sĩ ghi 8:30 cho lịch 8:45 nhưng SA + CTG cần ≥ 60' → giữ 8:30, cần xác nhận."""
    p = _plan_with_ai(rules, "36w5, sa, ctg, khám gây mê đến 8h30", [{"ga_weeks": 36}], start=time(8, 45))
    assert p.arrival.arrival_time == time(8, 30) and "DOCTOR_SHORT" in p.decision.reasons
    q = _plan_with_ai(rules, "khám thai 30w, SA đến 9h30", [{"ga_weeks": 30}], start=time(10, 0))
    assert q.arrival.arrival_time == time(9, 30) and "DOCTOR_SHORT" not in q.decision.reasons  # thiếu 5' < 15'


def test_note_service_time_is_doctor_time(rules) -> None:  # noqa: ANN001
    """'21w/sa4d có hẹn 9h30' → đến đúng 9:30 (giờ bác sĩ ghi)."""
    p = _plan_with_ai(rules, "21w/sa4d có hẹn 9h30", [{"ga_weeks": 21}], start=time(11, 30))
    assert p.arrival.arrival_time == time(9, 30)


def test_sa_4d_replaces_sa_2d_and_cbnv_is_not_package(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "Siêu âm 4d tuần 31", [{"ga_weeks": 31}])
    assert "SA_2D" not in p.service_plans[0].codes and "SA_4D" in p.service_plans[0].codes
    q = _plan_with_ai(rules, "Thai 39w5d CBNV Vinmec", [{"ga_weeks": 39}])
    assert q.service_plans[0].visit_type == "ANC"


# ---------------- Bug rà soát 18/11/2026
def test_urgent_warning_even_in_waiting_slot(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "Thai 34w, đau bụng cơn, ra nước", [{"ga_weeks": 34}], start=time(12, 0))
    assert "URGENT" in p.decision.reasons and any("cấp cứu sản" in d for d in p.arrival.details)


def test_very_early_computed_time_clamped_not_lost(rules) -> None:  # noqa: ANN001
    """08:00 + test đường huyết ghi rõ (15' + 120') → 05:45: đẩy về giờ nhận khách, không mất giờ đến."""
    p = _plan_with_ai(rules, "25w NPDN", [{"ga_weeks": 25}], start=time(8, 0))
    assert p.arrival.arrival_time is not None and "OPEN_TIME" in p.decision.reasons


def test_relative_doctor_instruction_is_doctor_time(rules) -> None:  # noqa: ANN001
    """'đến trước 1 tiếng' cho lịch 08:00 → 07:00 là lời bác sĩ dặn: giữ 07:00 (không đẩy về 07:45), cần xác nhận."""
    p = _plan_with_ai(rules, "khám thai 20w, đến trước 1 tiếng", [{"ga_weeks": 20}], start=time(8, 0))
    assert p.arrival.arrival_time == time(7, 0) and p.arrival.doctor_given and "OPEN_TIME" in p.decision.reasons


def test_clamp_never_after_appointment(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "31w3d, 4d + khám", [{"ga_weeks": 31}], start=time(7, 30))
    assert p.arrival.arrival_time is not None and p.arrival.arrival_time <= time(7, 30)


def test_gyn_note_with_ivf_history_is_kept(his_cfg, rules) -> None:  # noqa: ANN001
    raw = read_raw(oh_file([("09:00", "TS hiếm muộn, khí hư nhiều", "0901000001"),
                            ("10:00", "tiền sử IVF, khám phụ khoa", "0901000002"),
                            ("11:00", "chuyển phôi ngày 5", "0901000003")]))
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    assert {p.merged.pid for p in res.planned} == {"SYN0001", "SYN0002"}


def test_rules_clear_skips_ai_for_clear_notes(rules) -> None:  # noqa: ANN001
    from core.pipeline import rules_clear

    vocab = rules["arrival"]["ai_skip_vocabulary"]
    for note in ("39w6d/SA/CTG tới 08h30", "Chậm kinh", "tái khám sau sinh thường", "36w5, sa, ctg, khám gây mê đến 8h30"):
        assert rules_clear(note, vocab), note
    assert not rules_clear("Thai 39w5d CBNV Vinmec 100201", vocab)


# ---------------- Rà soát lần 2 (18/11/2026)
def test_ivf_with_bleeding_kept_and_reviewed(his_cfg, rules) -> None:  # noqa: ANN001
    raw = read_raw(oh_file([("09:00", "IVF, ra máu", "0901000001"), ("10:00", "KH IVF tái khám", "0901000002"),
                            ("11:00", "chuyển phôi ngày 5", "0901000003")]))
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    by = {p.merged.pid: p for p in res.planned}
    assert set(by) == {"SYN0001", "SYN0002"}  # chỉ bỏ dòng thủ thuật rõ ràng
    assert all("SCOPE_CHECK" in p.decision.reasons for p in by.values())


def test_sunday_appointment_flagged(his_cfg, rules) -> None:  # noqa: ANN001
    from datetime import date as _d

    from core.pipeline import plan_appointment
    from core.rules.merge import MergedAppt, Slot

    sun = _d(2026, 11, 22)  # chủ nhật
    m = MergedAppt(pid="SYNS", appt_date=sun, start_time=time(9, 0), end_time=None, services=[], note="khám thai 20w",
                   full_name="Khách Giả", phone="0901000009", phone_raw="", nationality="Việt Nam", department="",
                   doctor="", booking_source="", booked_at=None, slots=[Slot(time(9, 0), "", "khám thai 20w")])
    p = plan_appointment(m, rules, None, [], ner=False)
    assert "CLINIC_CLOSED" in p.decision.reasons


def test_waiting_doctor_time_still_checks_mixed(rules) -> None:  # noqa: ANN001
    from core.config import load_visit_services
    from core.rules.arrival import compute_arrival
    from core.rules.merge import Slot

    slots = [Slot(time(12, 0), "", "LEEP, KH tới 10h"), Slot(time(14, 0), "", "SA 2D")]
    mins = {x: (15, "x") for x in slots}
    load_visit_services()
    r = compute_arrival(time(12, 0), [], "LEEP, KH tới 10h", None, rules, slots=slots, slot_minutes=mins, max_early=180)
    assert r.arrival_time == time(10, 0) and "WAITING_MIXED" in r.reasons


# ---------------- Rà soát file v4 (18/11/2026)
def test_missing_hn_merged_by_name_and_phone(his_cfg, rules) -> None:  # noqa: ANN001
    """Olivia Brown: 1 dòng có HN + 1 dòng Autobooking thiếu HN, cùng tên + SĐT → 1 lịch (giờ sớm hơn)."""
    raw = read_raw(oh_file([("08:00", "26w NPDN", "0900001049"), ("10:00", "Autobooking_MVM_#1_khám thai", "0900001049")]))
    raw.loc[0, "HN"] = "1049"
    raw.loc[1, "HN"] = ""
    raw.loc[:, "Patient Name"] = ["Olivia Brown", "OLIVIA  brown"]
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    assert res.ok and len(res.planned) == 1
    assert res.planned[0].merged.pid == "1049" and res.planned[0].merged.is_merged


def test_masking_keeps_medical_abbreviations() -> None:
    from core.ai.masking import mask_text

    for note in ("SA NT 12w", "XN GBS + khám", "KH cần phiên dịch"):
        assert mask_text(note, full_name="Nguyễn Thị Lan", pid="X", doctor_names=[], ner=True).text == note
    assert "[TÊN]" in mask_text("Chị Lan hẹn SA", full_name="Nguyễn Thị Lan", pid="X", doctor_names=[], ner=True).text


def test_suction_procedure_vs_follow_up_and_iud_no_fasting(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "hút buồng tử cung, nhịn ăn từ tối", [{"ga_weeks": None}])
    assert p.service_plans[0].visit_type == "PROCEDURE" and p.service_plans[0].fasting
    q = _plan_with_ai(rules, "tk sau hút buồng tử cung", [{"ga_weeks": None}])
    assert q.service_plans[0].visit_type == "POST_ABORTION" and not q.service_plans[0].fasting
    for note in ("đặt vòng, sạch kinh 5 ngày", "tháo vòng"):
        assert not _plan_with_ai(rules, note, [{"ga_weeks": None}]).service_plans[0].fasting, note


def test_no_extra_generic_services(rules) -> None:  # noqa: ANN001
    codes = lambda n: [x.code for x in _plan_with_ai(rules, n, [{"ga_weeks": 12}]).service_plans[0].timed]  # noqa: E731
    assert "SA_2D" not in codes("12w5d SA NT + double")
    assert "SA_2D" not in codes("SA đầu dò")
    assert "XN_MAU" not in codes("35w6d, XN GBS + khám")
    assert "SA_2D" in codes("31w, 2d + khám")


def test_done_short_and_suffix_forms(rules) -> None:  # noqa: ANN001
    for note in ("khám thai 26w, đã làm NPDN", "26w, NPDN rồi", "26w đã NPDN"):
        p = _plan_with_ai(rules, note, [{"ga_weeks": 26}])
        assert "OGTT" not in p.service_plans[0].codes, note


def test_sms_export_hides_temp_id_fasting_note_and_dup_phone() -> None:
    from datetime import date as _d

    import pytest as _pytest

    from core.rules.export_worldsms import ExportLocked, ExportRow, build_exports

    cols = (("Số điện thoại", "phone"), ("P", "full_name"), ("P1", "pid"), ("P6", "arrival_time"), ("P7", "fasting_note"))
    rows = [ExportRow("0901000001", "Khách A", "TAM-261119-003", _d(2026, 11, 19), time(9), time(8, 45), "auto",
                      "Việt Nam", fasting=True),
            ExportRow("0901000002", "Khách B", "1002", _d(2026, 11, 19), time(10), time(9, 45), "auto", "Việt Nam")]
    out = build_exports(rows, _d(2026, 11, 19), columns=cols, fasting_text="Nhịn ăn 8 tiếng")
    vn = next(v for k, v in out.items() if "_VN_" in k)
    assert vn.iloc[0]["P1"] == "" and vn.iloc[0]["P7"] == "Nhịn ăn 8 tiếng" and vn.iloc[1]["P7"] == ""
    dup = [*rows, ExportRow("0901000001", "Khách A", "1001", _d(2026, 11, 19), time(11), time(10, 45), "auto", "Việt Nam")]
    with _pytest.raises(ExportLocked):
        build_exports(dup, _d(2026, 11, 19), columns=cols)


def test_alt_arrival_without_reception(rules) -> None:  # noqa: ANN001
    """Bản 2: có dịch vụ tính giờ → chỉ trừ thời gian dịch vụ; chỉ khám / giờ bác sĩ → như bản 1."""
    p = _plan_with_ai(rules, "31w, 2d + khám", [{"ga_weeks": 31}], start=time(10, 0))
    assert p.arrival.arrival_time == time(9, 25) and p.arrival.alt_time == time(9, 40)  # 15'+20' / chỉ 20'
    q = _plan_with_ai(rules, "tái khám sau sinh thường", [{"ga_weeks": None}], start=time(10, 0))
    assert q.arrival.arrival_time == q.arrival.alt_time == time(9, 45)
    r = _plan_with_ai(rules, "39w6d/SA/CTG tới 08h30", [{"ga_weeks": 39}], start=time(11, 0))
    assert r.arrival.arrival_time == r.arrival.alt_time == time(8, 30)


# ---------------- Rà soát bộ 100 ca (18/11/2026)
def test_safety_warnings_by_type(rules) -> None:  # noqa: ANN001
    e = _plan_with_ai(rules, "nghi thai ngoài tử cung", [{"ga_weeks": None}])
    assert "URGENT" in e.decision.reasons and any("thai ngoài tử cung" in d for d in e.arrival.details)
    m = _plan_with_ai(rules, "thai 6w, ra máu ít", [{"ga_weeks": 6}])
    assert "URGENT" in m.decision.reasons and any("dọa sảy" in d for d in m.arrival.details)
    assert not any("chuyển dạ" in d for d in m.arrival.details)
    r = _plan_with_ai(rules, "34w ra nước", [{"ga_weeks": 34}])
    assert {"SA_2D", "CTG"} <= set(r.service_plans[0].codes) and "URGENT" in r.decision.reasons
    assert _plan_with_ai(rules, "36w ra máu", [{"ga_weeks": 36}]).service_plans[0].visit_type == "ANC"


def test_visit_type_fixes_100_cases(rules) -> None:  # noqa: ANN001
    vt = lambda n: _plan_with_ai(rules, n, [{"ga_weeks": None}]).service_plans[0]  # noqa: E731
    a = vt("tk sau mổ lấy thai 10 ngày")
    assert a.visit_type == "POSTPARTUM" and "XN_TIEN_PHAU" not in a.codes
    assert vt("uống thuốc phá thai nội khoa lần 2").visit_type == "MED_ABORTION"
    b = vt("soi cổ tử cung + sinh thiết")
    assert not b.fasting and "THU_THUAT" not in b.codes
    c = _plan_with_ai(rules, "30w, tiêm uốn ván + khám", [{"ga_weeks": 30}]).service_plans[0]
    assert set(c.codes) == {"KHAM", "TIEM_UV"}


def test_prep_only_procedure_asks_fasting(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "chuẩn bị hồ sơ LEEP", [{"ga_weeks": None}])
    assert not p.service_plans[0].fasting and "FASTING_CHECK" in p.decision.reasons


def test_shared_phone_different_patients_flagged(his_cfg, rules) -> None:  # noqa: ANN001
    raw = read_raw(oh_file([("08:00", "khám thai 20w", "0900009898"), ("09:00", "khám thai 30w", "0900009898")]))
    raw.loc[:, "Patient Name"] = ["Trần Thị Hoa", "Lê Thị Hằng"]
    res = process_his(raw, his_cfg, rules, D, client=None, ner=False)
    assert len(res.planned) == 2 and all("SHARED_PHONE" in p.decision.reasons for p in res.planned)


# ---------------- Rà soát code 18/11/2026
def test_name_like_syllables_still_masked() -> None:
    from core.ai.masking import _medical_only

    for name in ("Thái Hà", "Hà", "Tài Hà", "Hà Mơ"):
        assert not _medical_only(name), name
    for med in ("SA NT", "XN GBS", "KH", "THA", "Tái khám"):
        assert _medical_only(med), med


def test_roi_xong_as_then_is_not_done(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "12w5d SA NT rồi khám BS", [{"ga_weeks": 12}])
    assert "NT" in p.service_plans[0].codes
    q = _plan_with_ai(rules, "26w, NPDN rồi", [{"ga_weeks": 26}])
    assert "OGTT" not in q.service_plans[0].codes


def test_ultrasounds_one_session_take_longest(rules) -> None:  # noqa: ANN001
    """Người dùng 18/11/2026: nhiều loại siêu âm trong 1 lịch làm 1 lần → tính loại lâu nhất; CTG vẫn cộng thêm."""
    p = _plan_with_ai(rules, "13w SA NT + 4D + doppler + đầu dò", [{"ga_weeks": 13}], start=time(11, 0))
    assert p.service_plans[0].floor_minutes == 45 and p.arrival.arrival_time == time(10, 15)
    assert "OVER_CAP" not in p.decision.reasons
    q = _plan_with_ai(rules, "36w, SA + CTG", [{"ga_weeks": 36}], start=time(10, 0))
    assert q.service_plans[0].floor_minutes == 65


def test_floor_over_service_cap_reviews(rules, monkeypatch) -> None:  # noqa: ANN001
    """Tổng thời gian tối thiểu vượt giới hạn an toàn của dịch vụ → Cần xác nhận (CLAUDE.md luật 2)."""
    import core.pipeline as pl

    orig = pl.load_visit_services

    def low_cap(*a, **k):  # noqa: ANN002, ANN003, ANN202
        c = orig(*a, **{**k, "copy_data": True})
        c["services"]["SA_4D"]["max_minutes_early"] = 30
        c["services"]["KHAM"]["max_minutes_early"] = 30
        return c

    monkeypatch.setattr(pl, "load_visit_services", low_cap)
    monkeypatch.setitem(rules["arrival"], "max_minutes_early", 30)
    p = _plan_with_ai(rules, "31w SA 4D + khám", [{"ga_weeks": 31}], start=time(11, 0))
    assert p.service_plans[0].floor_minutes == 45 and "OVER_CAP" in p.decision.reasons


def test_blood_urine_tests_need_confirmation(rules) -> None:  # noqa: ANN001
    p = _plan_with_ai(rules, "28w, xn máu, nước tiểu, khám", [{"ga_weeks": 28}])
    assert "LAB_CONFIRM" in p.decision.reasons
    q = _plan_with_ai(rules, "28w, xn máu, khám, đến 8h", [{"ga_weeks": 28}], start=time(9, 0))
    assert "LAB_CONFIRM" not in q.decision.reasons  # giờ bác sĩ ghi → theo bác sĩ


def test_risk_note_still_calls_ai() -> None:
    from core.pipeline import rules_clear

    assert rules_clear("39w6d/SA/CTG tới 08h30")
    assert not rules_clear("Tái khám sau hút thai, song thai, THA")


def test_dup_phone_message_masks_phone_and_hides_names() -> None:
    from datetime import date as _d

    import pytest as _pytest

    from core.rules.export_worldsms import ExportLocked, ExportRow, build_exports

    rows = [ExportRow("0901000412", "Khách A", "1", _d(2026, 11, 19), time(9), time(8, 45), "auto", "Việt Nam"),
            ExportRow("0901000412", "Khách B", "2", _d(2026, 11, 19), time(10), time(9, 45), "auto", "Việt Nam")]
    with _pytest.raises(ExportLocked) as e:
        build_exports(rows, _d(2026, 11, 19))
    assert "Khách A" not in str(e.value) and "412" in str(e.value) and "0901000412" not in str(e.value)
