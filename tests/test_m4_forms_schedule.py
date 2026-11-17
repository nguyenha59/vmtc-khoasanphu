from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from core.channels.base import AlertPayload, SmsRow
from core.channels.sms_excel import ExcelSmsChannel
from core.channels.sms_test_api import TestApiSmsChannel
from core.rules.form_scheduler import PatientState, plan_for_patient, plan_forms

TODAY = date(2026, 11, 9)


def st_at(ga_w: int, ga_d: int = 0, *, next_in: int | None = 30, consent: str = "granted", **kw) -> PatientState:
    edd = TODAY + timedelta(days=280 - (ga_w * 7 + ga_d))
    nxt = TODAY + timedelta(days=next_in) if next_in is not None else None
    return PatientState(patient_id=1, code="R0001", consent=consent, edd=edd, next_appt=nxt, **kw)


def routine(rules, st):
    return [d for d in plan_for_patient(st, TODAY, rules) if d.kind == "routine"]


@pytest.mark.parametrize("ga,code", [((8, 0), "F1"), ((10, 6), "F1"), ((15, 2), "F2"), ((19, 0), "F3"),
                                     ((26, 6), "F4"), ((30, 1), "F5"), ((35, 6), "F6")])
def test_each_window(rules, ga, code):
    d = routine(rules, st_at(*ga))
    assert len(d) == 1 and d[0].form_code == code and d[0].send


@pytest.mark.parametrize("ga", [(7, 6), (11, 0), (18, 6), (28, 0), (32, 3)])
def test_outside_windows(rules, ga):
    assert routine(rules, st_at(*ga)) == []


def test_gap_le_14_not_sent(rules):
    d = routine(rules, st_at(25, next_in=14))
    assert not d[0].send and "Khoảng trống" in d[0].skip_reason
    assert routine(rules, st_at(25, next_in=15))[0].send


def test_appt_within_3_days_not_sent(rules):
    d = routine(rules, st_at(25, next_in=3))
    assert not d[0].send and "3 ngày" in d[0].skip_reason


def test_no_next_appt_sends(rules):
    assert routine(rules, st_at(25, next_in=None))[0].send


def test_ge_36_no_routine_but_module_ok(rules):
    st = st_at(37, modules=["M-THA"])
    ds = plan_for_patient(st, TODAY, rules)
    assert [d.form_code for d in ds] == ["M-THA"]


def test_no_consent_not_in_list(rules):
    assert plan_for_patient(st_at(25, consent="none"), TODAY, rules) == []
    assert plan_for_patient(st_at(25, consent="withdrawn", modules=["M-THA"]), TODAY, rules) == []


def test_resend_block_and_answered(rules):
    st = st_at(25, sent=[("F4", TODAY - timedelta(days=3))])
    assert not routine(rules, st)[0].send
    # docs/13 (13/11/2026): mỗi mốc gửi 1 lần (+ nhắc 1 lần) – KHÔNG gửi lại sau 7 ngày
    st = st_at(25, sent=[("F4", TODAY - timedelta(days=7))])
    r = routine(rules, st)[0]
    assert not r.send and "mỗi mốc gửi 1 lần" in (r.skip_reason or "")
    st = st_at(25, answered={"F4"})
    assert not routine(rules, st)[0].send


@pytest.mark.parametrize("days,code", [(3, "S1"), (7, "S2"), (14, "S3"), (42, "S4")])
def test_postpartum_days(rules, days, code):
    st = PatientState(patient_id=1, code="R1", consent="granted", delivery_date=TODAY - timedelta(days=days))
    ds = plan_for_patient(st, TODAY, rules)
    assert [d.form_code for d in ds] == [code] and ds[0].send


def test_postpartum_other_day_nothing(rules):
    # ngoài mốc + khoảng gửi bù (S1 ngày 3 + postpartum_grace_days) và trước S2 (ngày 7) → không gửi gì
    day = rules["forms"]["postpartum_days"]["S1"] + rules["forms"]["postpartum_grace_days"] + 1
    assert day < rules["forms"]["postpartum_days"]["S2"]
    st = PatientState(patient_id=1, code="R1", consent="granted", delivery_date=TODAY - timedelta(days=day))
    assert plan_for_patient(st, TODAY, rules) == []


def test_modules_frequency(rules):
    """docs/13: module gửi vào ngày cố định trong tuần (TODAY = Thứ 2, 09/11/2026)."""
    assert TODAY.weekday() == 0

    def mods(st, day):  # type: ignore[no-untyped-def]
        return [d.form_code for d in plan_for_patient(st, day, rules) if d.kind == "module" and (d.send or d.merged_into)]

    wed, thu = TODAY + timedelta(days=2), TODAY + timedelta(days=3)
    dtd = st_at(30, modules=["M-DTD"])
    assert mods(dtd, TODAY) == ["M-DTD"] and mods(dtd, thu) == []  # 1 lần/tuần (T2)
    dtd.on_insulin = True
    assert mods(dtd, thu) == ["M-DTD"]  # dùng insulin: T2, T5
    tha = st_at(30, modules=["M-THA"])
    assert mods(tha, TODAY) == ["M-THA"] and mods(tha, wed) == [] and mods(tha, thu) == ["M-THA"]
    tha.flags = ["PREECLAMPSIA"]
    assert mods(tha, wed) == ["M-THA"]  # tiền sản giật: T2, T4, T6
    tha_sent = st_at(30, modules=["M-THA"], sent=[("M-THA", TODAY)])
    assert mods(tha_sent, TODAY) == []  # không gửi 2 lần trong ngày


def test_no_forms_right_before_or_after_visit(rules):
    near = st_at(30, next_in=2, modules=["M-THA"])
    assert not [d for d in plan_for_patient(near, TODAY, rules) if d.send]  # sắp khám
    just = st_at(30, next_in=30, modules=["M-THA"], last_visit=TODAY - timedelta(days=1))
    assert not [d for d in plan_for_patient(just, TODAY, rules) if d.send]  # vừa khám


def test_five_sample_patients(rules):
    """Kiểm thử chấp nhận 3: 5 thai phụ mẫu ở các tuần thai khác nhau."""
    pts = [
        PatientState(1, "R0001", "granted", edd=TODAY + timedelta(days=280 - 9 * 7), next_appt=TODAY + timedelta(days=20)),
        PatientState(2, "R0002", "granted", edd=TODAY + timedelta(days=280 - 20 * 7), next_appt=TODAY + timedelta(days=10)),
        PatientState(3, "R0003", "granted", edd=TODAY + timedelta(days=280 - 30 * 7), next_appt=TODAY + timedelta(days=20),
                     modules=["M-THA"]),
        PatientState(4, "R0004", "none", edd=TODAY + timedelta(days=280 - 25 * 7)),
        PatientState(5, "R0005", "granted", delivery_date=TODAY - timedelta(days=14)),
    ]
    ds = plan_forms(pts, TODAY, rules)
    # Form có câu được hỏi hôm nay (kể cả form gộp vào đường dẫn chung)
    covered = sorted((d.code, d.form_code) for d in ds if d.send or d.merged_into)
    skipped = sorted((d.code, d.form_code) for d in ds if not d.send and not d.merged_into)
    assert covered == [("R0001", "F1"), ("R0003", "F5"), ("R0003", "M-THA"), ("R0005", "S3")]
    assert skipped == [("R0002", "F3")]
    # 13/11/2026: mỗi thai phụ 1 đường dẫn / ngày – M-THA gộp vào F5 (không gửi 2 SMS, không hỏi trùng C6, C7)
    links = sorted((d.code, d.form_code) for d in ds if d.send)
    assert links == [("R0001", "F1"), ("R0003", "F5"), ("R0005", "S3")]
    assert next(d for d in ds if d.form_code == "M-THA").merged_into == "F5"


# ---------------- kênh
def test_test_api_blocks_non_whitelist():
    ch = TestApiSmsChannel(whitelist={"0900000001"}, url="")
    res = ch.send_batch("gui_form", [SmsRow("0900000001", "R1"), SmsRow("0912345678", "R2")])
    assert [r.status for r in res.rows] == ["sent", "blocked"]
    assert "WHITELIST" in res.rows[1].error


def test_excel_channel_writes_file(tmp_path):
    res = ExcelSmsChannel(tmp_path).send_batch("gui_form", [SmsRow("0900000001", "R1", {"P": "F1", "P1": "x"})])
    assert res.file_path and res.file_path.exists() and res.n_ok == 1


def test_alert_payload_has_no_pii():
    from datetime import datetime

    p = AlertPayload(1, "R0412", "32+1", "red", ("VISUAL",), datetime(2026, 11, 9, 8, 0))
    assert set(p.to_dict()) == {"alert_id", "patient_code", "ga_text", "level", "reasons", "created_at"}


# ---------------- CSDL: đồng thuận, token
def _patient(s, consent="granted"):
    from core.repo import upsert_patient

    p = upsert_patient(s, "P100", "Lê Thị Hoa", "0900000001", "Việt Nam")
    p.consent_status = consent
    p.edd = TODAY + timedelta(days=280 - 25 * 7)
    s.flush()
    return p


def _in_package(s, p):
    from core.db import now
    from core.models import PatientProgram

    s.add(PatientProgram(patient_id=p.id, in_package=True, package_name="Gói thai sản (giả)", updated_at=now()))
    s.flush()


def test_withdraw_consent_stops_immediately(db, rules):
    from core import consent
    from core.config import load_forms
    from core.forms import build_states, check_token, export_form_sms, save_plan
    from core.models import FormSchedule
    from core.rules.form_scheduler import plan_forms as pf

    with db.session_scope() as s:
        p = _patient(s)
        _in_package(s, p)  # rules.yaml package_only: xuất SMS kiểm lại diện theo dõi
        save_plan(s, pf(build_states(s, TODAY), TODAY, rules), TODAY, rules.rules_version)
        assert s.scalar(select(FormSchedule).where(FormSchedule.status == "planned"))
        consent.withdraw(s, p.id, None)
        assert s.scalar(select(FormSchedule).where(FormSchedule.status == "planned")) is None
        assert build_states(s, TODAY) == []
        consent.grant(s, p.id, None)
        save_plan(s, pf(build_states(s, TODAY), TODAY, rules), TODAY, rules.rules_version)
        from core.channels.sms_mock import MockSmsChannel

        res = export_form_sms(s, TODAY, rules, load_forms(), MockSmsChannel(), None)
        assert res.n_ok == 1
        token = s.scalar(select(FormSchedule.token).where(FormSchedule.status == "exported"))
        assert token and len(token) == 22
        assert check_token(s, token)[1] is None
        consent.withdraw(s, p.id, None)
        assert check_token(s, token)[1] is not None
