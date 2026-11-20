from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import pytest

from core.ingest.his_reader import HisRow, read_raw
from core.ingest.validators import check_columns, validate_rows
from core.pipeline import process_his
from core.rules.arrival import NoteAI, arrival_text, compute_arrival, waiting_label
from core.rules.export_worldsms import ExportLocked, ExportRow, build_exports
from core.rules.merge import Slot, filter_departments, merge_appointments
from core.rules.review import decide

GOLDEN = Path(__file__).parent / "golden"
D = date(2026, 11, 10)


def row(n: int, pid: str = "P1", start: str = "08:00", end: str = "08:30", service: str = "Khám thai",
        dept: str = "Phòng khám Sản phụ khoa", note: str = "", phone: str = "0900123456", d: date = D) -> HisRow:
    return HisRow(row_no=n, pid=pid, full_name="Trần Thị Lan", phone=phone, nationality="Việt Nam",
                  appt_date=d, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
                  service=service, department=dept, note=note)


# ---------------- validators
def test_missing_column_E01(his_cfg):
    raw = read_raw(GOLDEN / "his_error_missing_col.xlsx")
    rep = check_columns(raw, his_cfg)
    assert not rep.ok and rep.fatal[0].code == "E01"
    assert "Điện thoại" in rep.fatal[0].message


def test_swapped_columns_E02_stops(his_cfg, rules):
    raw = read_raw(GOLDEN / "his_error_swapped_cols.xlsx")
    res = process_his(raw, his_cfg, rules, None, client=None, ner=False)
    assert not res.ok
    assert res.report.fatal[0].code == "E02"
    assert res.planned == []


def test_bad_time_E04_E03(his_cfg, rules):
    raw = read_raw(GOLDEN / "his_error_bad_time.xlsx")
    res = process_his(raw, his_cfg, rules, None, client=None, ner=False)
    assert res.ok
    codes = {i.code for i in res.report.row_issues}
    assert "E04" in codes and "E03" in codes


def test_E05_other_date(rules):
    rep = validate_rows([row(2, d=date(2026, 11, 11))], rules, D)
    assert [i.code for i in rep.row_issues] == ["E05"]


def test_E06_bad_phone(rules):
    rep = validate_rows([row(2, phone="09123")], rules, D)
    assert "E06" in {i.code for i in rep.row_issues}


# ---------------- merge
def test_merge_same_pid_same_day(rules):
    rows = [row(2, start="09:00", end="09:30", service="Siêu âm 2D"), row(3, start="08:00", service="Khám thai")]
    m = merge_appointments(rows, 180, rules["arrival"]["fasting_keywords"])
    assert len(m) == 1
    assert m[0].start_time == time(8, 0)
    assert set(m[0].services) == {"Khám thai", "Siêu âm 2D"}
    assert m[0].merged_from == [3, 2]
    assert not m[0].conflict


def test_merge_different_days_not_merged(rules):
    rows = [row(2), row(3, d=date(2026, 11, 11))]
    assert len(merge_appointments(rows, 180, [])) == 2


def test_merge_conflict_over_180(rules):
    rows = [row(2, start="08:00"), row(3, start="11:30", end="11:45", service="Siêu âm 2D")]
    m = merge_appointments(rows, 180, [])
    assert m[0].conflict


def test_merge_conflict_fasting_later(rules):
    rows = [row(2, start="08:00"), row(3, start="09:00", end="09:15", service="Xét nghiệm máu")]
    m = merge_appointments(rows, 180, rules["arrival"]["fasting_keywords"])
    assert m[0].conflict


def test_filter_department(rules):
    keep, dropped = filter_departments([row(2), row(3, dept="Phòng khám Nội tổng quát")],
                                       rules["departments_included"])
    assert len(keep) == 1 and len(dropped) == 1


# ---------------- arrival
def test_ultrasound_20_minutes(rules):
    r = compute_arrival(time(8, 30), ["Siêu âm 4D"], "", None, rules)
    assert r.arrival_time == time(8, 10) and r.method == "rule"


def test_exam_15_minutes(rules):
    assert compute_arrival(time(8, 30), ["Khám thai"], "", None, rules).arrival_time == time(8, 15)


def test_autobooking_note_is_plain_exam(rules):
    """Khách tự đặt: ghi chú kiểu "thai moi ycbs" (không giờ, không XN) → khám 15'."""
    assert compute_arrival(time(9, 0), ["Khám thai"], "thai moi ycbs", None, rules).arrival_time == time(8, 45)


def test_lab_service_30_minutes(rules):
    assert compute_arrival(time(9, 0), ["Xét nghiệm máu"], "", None, rules).arrival_time == time(8, 30)


def test_lab_in_note_30_minutes(rules):
    for note in ("làm XN", "lam xn", "nhịn ăn lấy máu"):
        assert compute_arrival(time(9, 0), ["Khám thai"], note, None, rules).arrival_time == time(8, 30), note


def test_explicit_time_kept_when_earlier(rules):
    r = compute_arrival(time(8, 0), ["Siêu âm 2D"], "BS hẹn đến 7h15", None, rules)
    assert r.arrival_time == time(7, 15)
    assert "ghi rõ" in r.rule_applied


def test_explicit_time_vs_rule_take_earlier(rules):
    # Ghi rõ 7h50, quy tắc siêu âm 8:00−20 = 7:40 → lấy 7:40.
    r = compute_arrival(time(8, 0), ["Siêu âm 2D"], "đến 7h50", None, rules)
    assert r.arrival_time == time(7, 40)
    assert "sớm hơn bác sĩ dặn" in r.rule_applied


def test_multi_service_same_time_takes_largest(rules):
    r = compute_arrival(time(9, 0), ["Khám thai", "Siêu âm 2D"], "", None, rules)
    assert r.arrival_time == time(8, 40)


def test_slots_each_minus_own_minutes_take_earliest(rules):
    """Khám 8:00 (−15 = 7:45), XN 8:10 (−30 = 7:40) → 7:40."""
    slots = [Slot(time(8, 0), "Khám thai", ""), Slot(time(8, 10), "Xét nghiệm máu", "")]
    r = compute_arrival(time(8, 0), ["Khám thai", "Xét nghiệm máu"], "", None, rules, slots=slots)
    assert r.arrival_time == time(7, 40)
    # Siêu âm 8:00 (−20 = 7:40), khám 9:00 (−15 = 8:45) → 7:40.
    slots = [Slot(time(8, 0), "Siêu âm 2D", ""), Slot(time(9, 0), "Khám thai", "thai moi ycbs")]
    r = compute_arrival(time(8, 0), ["Siêu âm 2D", "Khám thai"], "", None, rules, slots=slots)
    assert r.arrival_time == time(7, 40)


def test_merge_keeps_slots(rules):
    rows = [row(2, start="09:00", end="09:30", service="Siêu âm 2D"), row(3, start="08:00", service="Khám thai")]
    m = merge_appointments(rows, 180, rules["arrival"]["fasting_keywords"])[0]
    assert [(s.start_time, s.service) for s in m.slots] == [(time(8, 0), "Khám thai"), (time(9, 0), "Siêu âm 2D")]


def test_arrival_after_appt_rejected(rules):
    r = compute_arrival(time(8, 0), ["Khám thai"], "có mặt lúc 9h", None, rules)
    assert r.arrival_time != time(9, 0)
    assert "NO_RULE" in r.reasons


def test_ai_hint_too_early_rejected(rules):
    ai = NoteAI(ok=True, confidence=1.0, minutes_early=120)
    r = compute_arrival(time(9, 0), ["Khám thai"], "", ai, rules)
    assert r.arrival_time == time(8, 45) and r.method == "rule"


def test_ai_hint_applied_within_diff(rules):
    ai = NoteAI(ok=True, confidence=1.0, minutes_early=20)
    r = compute_arrival(time(9, 0), ["Siêu âm 2D"], "", ai, rules)
    assert r.arrival_time == time(8, 40) and r.method == "ai" and not r.reasons


def test_before_earliest_rejected(rules):
    r = compute_arrival(time(6, 40), ["Siêu âm 2D"], "", None, rules)
    assert r.arrival_time is None and "NO_RULE" in r.reasons


# ---------------- review
def _decide(rules, note="", ai=None, attempted=False, phone_ok=True, conflict=False, mask_ok=True,
            services=("Khám thai",), failed=False):
    arr = compute_arrival(time(9, 0), list(services), note, ai, rules)
    return decide(arrival=arr, note=note, phone_ok=phone_ok, conflict=conflict, ai=ai,
                  ai_attempted=attempted, mask_ok=mask_ok, rules=rules, ai_failed=failed)


def test_review_low_conf(rules):
    d = _decide(rules, note="x", ai=NoteAI(ok=True, confidence=0.6), attempted=True)
    assert d.reasons == ["LOW_CONF"]


def test_review_ai_failed_not_reported_as_low_conf(rules):
    """Hết ngân sách / lỗi mạng: báo AI_FAILED, không nói 'AI không chắc chắn'; vẫn phải cần xác nhận."""
    d = _decide(rules, note="x", ai=None, attempted=True, failed=True)
    assert d.reasons == ["AI_FAILED"] and d.status == "needs_review"


def test_review_call_note(rules):
    assert _decide(rules, note="gọi xác nhận lại giờ").reasons == ["CALL_NOTE"]
    assert _decide(rules, note="?").reasons == ["CALL_NOTE"]
    assert _decide(rules, note="goi xac nhan truoc 1 ngay").reasons == ["CALL_NOTE"]
    assert _decide(rules, note="Đã đóng gói thai sản").status == "auto"


def test_review_late_ga(rules):
    assert _decide(rules, note="38w, dặn vào viện nếu đau bụng").reasons == ["LATE_GA"]
    assert _decide(rules, note="Thai 32w").status == "auto"


def test_review_conflict(rules):
    assert _decide(rules, conflict=True).reasons == ["CONFLICT"]


def test_review_ai_rule_diff(rules):
    d = _decide(rules, note="đến sớm để chuẩn bị thủ thuật", ai=NoteAI(ok=True, confidence=1.0, minutes_early=45),
                attempted=True)
    assert d.reasons == ["AI_RULE_DIFF"]


def test_review_bad_phone(rules):
    assert _decide(rules, phone_ok=False).reasons == ["BAD_PHONE"]


def test_review_pii_left(rules):
    assert _decide(rules, mask_ok=False).reasons == ["PII_LEFT"]


# ---------------- export
def _er(pid: str, nat: str = "Việt Nam", status: str = "auto", arrival: time | None = time(7, 45)) -> ExportRow:
    return ExportRow(phone=f"0900123{int(pid) % 1000:03d}", full_name="A", pid=pid, appt_date=D, start_time=time(8, 0),
                     arrival_time=arrival, status=status, nationality=nat)


def test_export_columns_and_split():
    out = build_exports([_er("1"), _er("2", nat="Hàn Quốc"), _er("3")], D)
    vn, nn = out["nhac_hen_VN_2026-11-10.xlsx"], out["nhac_hen_NN_2026-11-10.xlsx"]
    assert list(vn.columns) == ["Số điện thoại", "P", "P1", "P2", "P3", "P4", "P5", "P6"]
    assert len(vn) == 2 and len(nn) == 1
    r = vn.iloc[0]
    assert (r["P2"], r["P3"], r["P4"], r["P5"], r["P6"]) == ("08:00", "10", "11", "2026", "07:45")


def test_export_skips_invalid_phone():
    bad = _er("9")
    bad.phone = "09123"
    out = build_exports([_er("1"), bad], D)
    assert len(out["nhac_hen_VN_2026-11-10.xlsx"]) == 1


def test_export_locked_when_needs_review():
    with pytest.raises(ExportLocked):
        build_exports([_er("1"), _er("2", status="needs_review")], D)


# ---------------- golden
def _golden_days() -> list[str]:
    return sorted(p.stem.replace("his_day_", "") for p in GOLDEN.glob("his_day_*.xlsx"))


@pytest.mark.parametrize("day", _golden_days())
def test_golden_rules_only(day, his_cfg, rules):
    raw = read_raw(GOLDEN / f"his_day_{day}.xlsx")
    res = process_his(raw, his_cfg, rules, date.fromisoformat(day), client=None, ner=False)
    assert res.ok
    exp = pd.read_csv(GOLDEN / f"expected_arrivals_{day}.csv", dtype=str, keep_default_na=False)
    got = {p.merged.pid: p for p in res.planned}
    assert set(got) == set(exp["pid"])
    for _, e in exp.iterrows():
        p = got[e["pid"]]
        assert p.merged.start_time.strftime("%H:%M") == e["start_time"], e["pid"]
        assert arrival_text(p.arrival, rules["arrival"]) == e["arrival_rule"], e.to_dict()
        assert p.decision.status == e["status_rule"], (e.to_dict(), p.decision.reasons)
        assert ",".join(p.decision.reasons) == e["reasons_rule"], e.to_dict()
    assert res.auto_rate >= 0.85


@pytest.mark.parametrize("day", _golden_days())
def test_golden_worldsms_export(day, his_cfg, rules):
    raw = read_raw(GOLDEN / f"his_day_{day}.xlsx")
    res = process_his(raw, his_cfg, rules, date.fromisoformat(day), client=None, ner=False)
    exp = pd.read_csv(GOLDEN / f"expected_arrivals_{day}.csv", dtype=str, keep_default_na=False).set_index("pid")
    rows = []
    for p in res.planned:
        arrival = p.arrival.arrival_time
        waiting = p.arrival.waiting
        if p.decision.status == "needs_review":  # điều dưỡng xác nhận giờ đúng
            true = exp.loc[p.merged.pid, "arrival_true"]
            waiting = true == waiting_label(rules["arrival"])
            arrival = None if waiting else time.fromisoformat(true)
        rows.append(ExportRow(phone=p.merged.phone, full_name=p.merged.full_name, pid=p.merged.pid,
                              appt_date=p.merged.appt_date, start_time=p.merged.start_time,
                              arrival_time=arrival, status="confirmed", nationality=p.merged.nationality,
                              waiting=waiting))
    out = build_exports(rows, date.fromisoformat(day), waiting_label(rules["arrival"]) or "")
    for kind in ("VN", "NN"):
        expected = pd.read_excel(GOLDEN / f"worldsms_expected_{kind}_{day}.xlsx", dtype=str, keep_default_na=False)
        got = out[f"nhac_hen_{kind}_{day}.xlsx"].astype(str).reset_index(drop=True)
        pd.testing.assert_frame_equal(got, expected.reset_index(drop=True), check_dtype=False)


def test_booked_at_parsed(his_cfg):
    raw = read_raw(GOLDEN / f"his_day_{_golden_days()[0]}.xlsx")
    from core.ingest.his_reader import standardize

    rows = standardize(raw, his_cfg)
    assert all(isinstance(r.booked_at, datetime) for r in rows)


# ---------------------------------------------------------------- khám chờ (12:00–13:00)
def test_waiting_slot_no_arrival_time(rules):
    from core.rules.review import decide

    for appt in (time(12, 0), time(12, 45)):
        res = compute_arrival(appt, ["Khám thai"], "thai moi", None, rules)
        assert res.waiting and res.arrival_time is None
        assert arrival_text(res, rules["arrival"]) == "Khám chờ"
        d = decide(arrival=res, note="thai moi", phone_ok=True, conflict=False, ai=None, ai_attempted=False,
                   mask_ok=True, rules=rules)
        assert d.status == "auto" and "NO_RULE" not in d.reasons
    assert not compute_arrival(time(13, 0), ["Khám thai"], "", None, rules).waiting
    assert not compute_arrival(time(11, 45), ["Khám thai"], "", None, rules).waiting


def test_waiting_slot_with_note_time_needs_review(rules):
    from core.rules.review import decide

    res = compute_arrival(time(12, 30), ["Khám thai"], "ycbs den 11h45", None, rules)
    assert res.waiting and "WAITING_NOTE_TIME" in res.reasons
    d = decide(arrival=res, note="ycbs den 11h45", phone_ok=True, conflict=False, ai=None, ai_attempted=False,
               mask_ok=True, rules=rules)
    assert d.status == "needs_review"


def test_export_waiting_label():
    row = ExportRow(phone="0900000001", full_name="A", pid="1", appt_date=D, start_time=time(12, 15),
                    arrival_time=None, status="auto", nationality="Việt Nam", waiting=True)
    out = build_exports([row], D, "Khám chờ")
    assert out[f"nhac_hen_VN_{D.isoformat()}.xlsx"].iloc[0]["P6"] == "Khám chờ"
    with pytest.raises(ExportLocked):  # không phải khám chờ mà thiếu giờ → vẫn khóa
        build_exports([ExportRow(phone="0900000001", full_name="A", pid="1", appt_date=D, start_time=time(9, 0),
                                 arrival_time=None, status="auto", nationality="Việt Nam")], D)


# ---------------- bác sĩ dặn đến trước một khoảng – đọc bằng regex, không cần AI
def test_doctor_early_note_without_ai(rules):
    r = compute_arrival(time(9, 0), ["Khám thai"], "đến trước 1 tiếng để nhịn ăn lấy máu", None, rules)
    assert r.arrival_time == time(8, 0) and "trước 60 phút" in r.rule_applied


def test_doctor_early_note_vs_rule_take_earlier(rules):
    # Dặn trước 15p, nhưng siêu âm 20' → 8:40.
    assert compute_arrival(time(9, 0), ["Siêu âm 2D"], "tới trước 15 phút", None, rules).arrival_time == time(8, 40)


def test_doctor_early_note_too_early_needs_review(rules):
    # Lịch 7:00, dặn trước 1 tiếng → 6:00 sớm hơn 06:30 → người kiểm tra, không bỏ qua im lặng.
    r = compute_arrival(time(7, 0), ["Khám thai"], "đến trước 1 tiếng", None, rules)
    assert "NO_RULE" in r.reasons


def test_rejected_ai_hint_needs_review(rules):
    r = compute_arrival(time(7, 0), ["Khám thai"], "", NoteAI(ok=True, confidence=1.0, minutes_early=60), rules)
    assert "AI_RULE_DIFF" in r.reasons


def test_waiting_slot_ignored_when_real_slot_exists(rules):
    slots = [Slot(time(11, 50), "Siêu âm 2D", ""), Slot(time(12, 0), "Khám thai", "")]
    r = compute_arrival(time(11, 50), ["Siêu âm 2D", "Khám thai"], "", None, rules, slots=slots)
    assert r.arrival_time == time(11, 30)


def test_export_vietnam_aliases_and_phone_format():
    from core.rules.export_worldsms import format_phone, is_vietnamese

    for n in ("Việt Nam", "VIET NAM", "Vietnam", "VN", "", "vietnamese"):
        assert is_vietnamese(n), n
    assert not is_vietnamese("Hàn Quốc")
    assert format_phone("0912345678", "intl") == "84912345678"
    assert format_phone("0912345678") == "0912345678"


def test_waiting_with_late_real_slot_needs_review(rules):
    """Khám chờ 12:00 + siêu âm 14:00 → giữ nhãn Khám chờ, vào Cần xác nhận."""
    slots = [Slot(time(12, 0), "Khám thai", ""), Slot(time(14, 0), "Siêu âm 2D", "")]
    r = compute_arrival(time(12, 0), ["Khám thai", "Siêu âm 2D"], "", None, rules, slots=slots)
    assert r.waiting and r.arrival_time is None and "WAITING_MIXED" in r.reasons
    d = decide(arrival=r, note="", phone_ok=True, conflict=False, ai=None, ai_attempted=False, mask_ok=True,
               rules=rules)
    assert d.status == "needs_review"


def test_waiting_with_close_real_slot_auto(rules):
    """Khám chờ 12:15 + siêu âm 13:15 (≤ 13:00 + 30') → giữ Khám chờ, không cần xác nhận."""
    slots = [Slot(time(12, 15), "Khám thai", ""), Slot(time(13, 15), "Siêu âm 2D", "")]
    r = compute_arrival(time(12, 15), ["Khám thai", "Siêu âm 2D"], "", None, rules, slots=slots)
    assert r.waiting and not r.reasons


# ---------------- chỉ gọi AI khi quy tắc chưa hiểu hết ghi chú
def test_read_fully_by_rules(rules):
    from core.rules.note_patterns import read_fully_by_rules

    v = rules["arrival"]["ai_skip_vocabulary"]
    for n in ("thai moi ycbs", "Thai 20w3d, khám định kỳ", "đến trước 1 tiếng để nhịn ăn lấy máu", "TT 33+2", ""):
        assert read_fully_by_rules(n, v), n
    for n in ("song thai 20w", "thai 20w, VMC cũ", "dọa sinh non", "thai hai mươi tuần", "BN xin đổi lịch"):
        assert not read_fully_by_rules(n, v), n


def test_pipeline_skips_ai_for_plain_note(rules):
    from core.pipeline import plan_appointment
    from core.rules.merge import MergedAppt

    calls: list[str] = []

    class Spy:
        model = "spy"

        def generate_json(self, prompt, schema, temperature=0.0, seed=None):  # noqa: ANN001
            calls.append(prompt)
            raise RuntimeError("không được gọi")

    def appt(note: str) -> MergedAppt:
        return MergedAppt(pid="1", appt_date=D, start_time=time(9, 0), end_time=None, services=["Khám thai"],
                          note=note, full_name="A", phone="0900000001", phone_raw="", nationality="Việt Nam",
                          department="", doctor="", booking_source="", booked_at=None)

    p = plan_appointment(appt("thai moi ycbs"), rules, Spy(), [], ner=False)
    assert not calls and p.arrival.arrival_time == time(8, 45) and p.decision.status == "auto"


def test_pipeline_records_why_ai_was_or_was_not_called(rules):
    """Màn hình hiện đúng một lý do: không ghi chú / tắt AI / quy tắc đọc hết / nhạy cảm / lỗi / có kết quả."""
    from core.ai.llm_client import LlmError
    from core.pipeline import plan_appointment
    from core.rules.merge import MergedAppt

    class Failing:
        model, name = "x", "openai:x"

        def generate_json(self, prompt, schema, temperature=0.0, seed=None):  # noqa: ANN001
            raise LlmError("hết ngân sách")

    def appt(note: str) -> MergedAppt:
        return MergedAppt(pid="1", appt_date=D, start_time=time(9, 0), end_time=None, services=["Khám thai"],
                          note=note, full_name="A", phone="0900000001", phone_raw="", nationality="Việt Nam",
                          department="", doctor="", booking_source="", booked_at=None)

    st = lambda note, client: plan_appointment(appt(note), rules, client, [], ner=False).ai_status  # noqa: E731
    assert st("", Failing()) == "no_note"
    assert st("BN đi cùng chồng", None) == "off"
    assert st("thai moi ycbs", Failing()) == "rules_read"
    assert st("TK sau đình chỉ thai", Failing()) == "sensitive"
    p = plan_appointment(appt("BN đi cùng chồng"), rules, Failing(), [], ner=False)
    assert p.ai_status == "failed" and "AI_FAILED" in p.decision.reasons
