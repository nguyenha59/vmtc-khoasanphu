"""Bộ câu hỏi cá nhân hóa (13/11/2026): cổng chất lượng câu hỏi, nhận tình trạng từ hồ sơ, ghép form, "Không chắc"."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.config import load_forms
from core.levels import Level
from core.question_quality import check_all, check_question, passing_questions
from core.rules.form_scheduler import PatientState, plan_for_patient, suggested_modules
from core.rules.personalize import build_plan, compose_questions, find_flags
from core.rules.triage_rules import RED_ITEMS, TRIAGED_YESNO, YELLOW_ITEMS, triage

FORMS = load_forms()
Q = FORMS.quality
TODAY = date(2026, 11, 13)


def _edd_for(weeks: int, days: int = 0) -> date:
    return TODAY + timedelta(days=280 - weeks * 7 - days)


# ---------------------------------------------------------------- cổng chất lượng
def test_quality_rejects_vague_and_keeps_anchored() -> None:
    assert check_question("C2", FORMS.questions["C2"], Q).blocking == ("VAGUE",)  # "đau bụng nhiều"
    assert check_question("C1", FORMS.questions["C1"], Q).passed
    # Có ví dụ trong ngoặc → mức độ đã được làm rõ, không tính mơ hồ
    assert check_question("C3", FORMS.questions["C3"], Q).passed


def test_quality_blocks_reversed_jargon_and_untriaged() -> None:
    assert "REVERSED" in check_question("C1", {"type": "yesno", "text": "Chị có uống và giữ được nước không?"}, Q).blocking
    assert "JARGON" in check_question("C3", {"type": "yesno", "text": "Chị có nghi rò ối không?"}, Q).blocking
    # Câu mà lớp quy tắc không phân mức → câu trả lời không nhận ra được vấn đề
    assert "NO_TRIAGE" in check_question("NEWQ", {"type": "yesno", "text": "Chị có mệt không?"}, Q).blocking
    ok, rejected = passing_questions(["C1", "KHONG_CO"], FORMS)
    assert ok == ["C1"] and rejected[0].code == "KHONG_CO"


def test_every_pack_question_sent_is_quality_checked_and_triaged() -> None:
    """Mọi câu thật sự gửi trong bộ theo tình trạng đều đạt cổng chất lượng và được quy tắc phân mức."""
    checks = check_all(FORMS)
    for code, fdef in FORMS.forms.items():
        if fdef.get("kind") != "pack":
            continue
        sent = compose_questions(code, [], (30, 0), {"forms": {"windows": {}}}, FORMS)  # type: ignore[arg-type]
        assert sent, f"{code}: không còn câu nào đạt chuẩn"
        for q in sent:
            assert checks[q].passed and q in TRIAGED_YESNO, q


# ---------------------------------------------------------------- nhận tình trạng từ hồ sơ
def test_flags_from_icd_risk_history_and_ai(rules) -> None:
    hist = {"risk_summary": ["TWINS"], "medical_history": ["Tăng huyết áp trước đây: không"],
            "obstetric_history": ["2021: mổ lấy thai 39 tuần do ngôi mông"]}
    flags = {f.flag: f for f in find_flags(hist, [{"code": "O99.0", "name": "Thiếu máu"}], ["GDM"], rules)}
    assert {e.source for e in flags["TWINS"].evidence} == {"risk"}
    assert flags["PREV_CSECTION"].evidence[0].source == "history"
    assert flags["ANEMIA"].evidence[0].source == "icd10"
    assert flags["GDM"].evidence[0].source == "ai_note"
    assert "HTN" not in flags  # "…: không" là phủ định → không tính


def test_plan_rejects_bad_questions_and_marks_pending(rules) -> None:
    flags = find_flags({"risk_summary": ["TWINS"]}, [{"code": "O99.0"}], [], rules)
    plan = build_plan(flags, [], (30, 0), False, rules, FORMS)
    item = next(i for i in plan.items if i.code == "P-SINHNON")
    assert "C2" in [r.code for r in item.rejected] and "C2" not in item.questions
    assert item.active_now and not item.confirmed
    assert plan.pending_flags == ["ANEMIA"]  # chưa có bộ câu đạt chuẩn → không đề xuất câu kém
    early = build_plan(flags, [], (18, 0), False, rules, FORMS)
    assert not next(i for i in early.items if i.code == "P-SINHNON").active_now  # ngoài 22–36+6 tuần


def test_stale_confirmed_is_reported(rules) -> None:
    plan = build_plan([], ["P-VETMO"], (30, 0), False, rules, FORMS)
    assert plan.stale_confirmed == ["P-VETMO"] and plan.items[0].confirmed


def test_suggested_modules_reads_config(rules) -> None:
    assert suggested_modules(["TWINS", "HTN"], rules) == ["M-THA", "P-SINHNON"]
    assert suggested_modules(["OTHER"], rules) == []


# ---------------------------------------------------------------- ghép form
def test_compose_adds_confirmed_pack_only_in_window(rules) -> None:
    base = FORMS.forms["F4"]["questions"]
    assert compose_questions("F4", [], (25, 0), rules, FORMS) == base
    with_pack = compose_questions("F4", ["P-SINHNON"], (25, 0), rules, FORMS)
    assert with_pack[: len(base)] == base and "C9" in with_pack and "C2" in with_pack  # C2 có sẵn trong F4
    assert len(with_pack) == len(set(with_pack))  # không hỏi trùng
    assert compose_questions("F2", ["P-SINHNON"], (15, 0), rules, FORMS) == FORMS.forms["F2"]["questions"]
    assert compose_questions("P-SINHNON", [], (25, 0), rules, FORMS) == ["C9", "C3", "C1"]
    assert compose_questions("M-THA", ["P-SINHNON"], (25, 0), rules, FORMS) == FORMS.forms["M-THA"]["questions"]


def test_pack_not_sent_alone_only_merged_or_mid_gap(rules) -> None:
    """docs/13: bộ theo tình trạng KHÔNG gửi SMS riêng; chỉ 1 lần giữa khoảng khi 2 lần khám cách nhau > 28 ngày."""
    def due(st_: PatientState, day: date) -> list[str]:
        return [d.form_code for d in plan_for_patient(st_, day, rules) if d.send or d.merged_into]

    normal = PatientState(patient_id=1, code="R1", consent="granted", edd=_edd_for(28), modules=["P-SINHNON"],
                          last_visit=TODAY - timedelta(days=10), next_appt=TODAY + timedelta(days=10))
    assert "P-SINHNON" not in due(normal, TODAY)
    far = PatientState(patient_id=1, code="R1", consent="granted", edd=_edd_for(28), modules=["P-SINHNON"],
                       last_visit=TODAY - timedelta(days=20), next_appt=TODAY + timedelta(days=20))
    assert "P-SINHNON" in due(far, TODAY)  # 40 ngày giữa 2 lần khám, đã qua nửa khoảng
    far.sent = [("P-SINHNON", TODAY - timedelta(days=1))]
    assert "P-SINHNON" not in due(far, TODAY)  # đã gửi 1 lần trong khoảng này
    early = PatientState(patient_id=1, code="R1", consent="granted", edd=_edd_for(18), modules=["P-SINHNON"],
                         last_visit=TODAY - timedelta(days=20), next_appt=TODAY + timedelta(days=20))
    assert "P-SINHNON" not in due(early, TODAY)  # ngoài cửa sổ 22–36+6 tuần


# ---------------------------------------------------------------- "Không chắc"
def test_unsure_is_never_green(rules) -> None:
    r = triage({"C1": "Không chắc"}, 30, False, rules)
    assert r.level >= Level.YELLOW and any("Không chắc" in f.reason for f in r.findings)
    assert triage({"C1": "Không"}, 30, False, rules).level == Level.GREEN
    assert triage({"C1": "Có"}, 30, False, rules).level == Level.RED


@settings(max_examples=150, deadline=None)
@given(st.dictionaries(st.sampled_from(sorted({*RED_ITEMS, *YELLOW_ITEMS, "C9"})),
                       st.sampled_from(["Có", "Không", "Không chắc"]), min_size=1))
def test_unsure_never_lowers_level(answers: dict[str, str]) -> None:
    """Đổi một câu từ "Có" sang "Không chắc" không bao giờ ra mức thấp hơn khi đổi sang "Không"."""
    from core.config import load_rules

    rules = load_rules()
    got = triage(answers, 30, False, rules).level
    as_no = triage({k: ("Không" if v == "Không chắc" else v) for k, v in answers.items()}, 30, False, rules).level
    assert got >= as_no
    if "Không chắc" in answers.values():
        assert got >= Level.YELLOW


@pytest.mark.parametrize("code", sorted(TRIAGED_YESNO))
def test_unsure_handled_for_every_triaged_question(rules, code: str) -> None:
    assert triage({code: "Không chắc"}, 30, False, rules).level >= Level.YELLOW


# ---------------------------------------------------------------- không sinh nhiều: mô phỏng 90 ngày
MAX_QUESTIONS_PER_LINK = 15  # docs/12 mục 5: mỗi lần ≤ 15 câu

# docs/13 mục 5 – số SMS tối đa / 90 ngày (không trả lời, CHƯA tính SMS nhắc), đi khám mỗi 4 tuần.
SCENARIOS = {
    "nguy cơ thấp": (24, [], False, [], 3),
    "song thai (bộ sinh non)": (24, ["P-SINHNON"], False, ["TWINS"], 4),
    "tăng huyết áp thai kỳ": (24, ["M-THA"], False, ["HTN"], 26),
    "tiền sản giật": (24, ["M-THA", "P-TSG"], False, ["PREECLAMPSIA"], 39),
    "ĐTĐ không insulin": (24, ["M-DTD"], False, ["GDM"], 13),
    "ĐTĐ dùng insulin + sinh non": (24, ["M-DTD", "P-SINHNON"], True, ["GDM", "PRETERM_RISK"], 26),
}


def _simulate(rules, weeks, mods, insulin, flags):  # type: ignore[no-untyped-def]
    from core.rules.form_scheduler import ga_at
    from core.rules.personalize import compose_merged

    edd = _edd_for(weeks)
    sent: list[tuple[str, date]] = []
    links, max_q, last_visit = 0, 0, TODAY - timedelta(days=1)
    for k in range(90):
        today = TODAY + timedelta(days=k)
        if (today - last_visit).days >= 28:
            last_visit = today  # đi khám mỗi 4 tuần
        st_ = PatientState(patient_id=1, code="R1", consent="granted", edd=edd, modules=mods, on_insulin=insulin,
                           flags=flags, sent=list(sent), last_visit=last_visit,
                           next_appt=last_visit + timedelta(days=28))
        decisions = plan_for_patient(st_, today, rules)
        due = [d for d in decisions if d.send]
        assert len(due) <= 1, f"{today}: {len(due)} SMS trong một ngày"
        for d in due:
            codes = [d.form_code, *[m.form_code for m in decisions if m.merged_into == d.form_code]]
            qs = compose_merged(codes, mods, ga_at(edd, today), rules, FORMS)
            assert len(qs) == len(set(qs)), f"{today}: hỏi trùng câu"
            max_q = max(max_q, len(qs))
            links += 1
        sent += [(d.form_code, today) for d in decisions if d.send or d.merged_into]
    return links, max_q


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_90_days_sms_within_plan(rules, name: str) -> None:
    weeks, mods, insulin, flags, limit = SCENARIOS[name]
    links, max_q = _simulate(rules, weeks, mods, insulin, flags)
    assert links <= limit, f"{name}: {links} SMS / 90 ngày (kế hoạch ≤ {limit})"
    assert max_q <= MAX_QUESTIONS_PER_LINK


def test_export_merges_into_one_sms_and_one_link(db, rules) -> None:
    """Qua CSDL: M-THA + P-SINHNON + F5 cùng ngày → 1 dòng SMS, 1 đường dẫn, câu hỏi = hợp, không trùng."""
    from sqlalchemy import select

    from core.channels.sms_mock import MockSmsChannel
    from core.forms import build_states, export_form_sms, save_plan, token_form_codes
    from core.models import FormSchedule, FormToken
    from core.repo import upsert_patient
    from core.rules.form_scheduler import ga_at, plan_forms
    from core.rules.personalize import compose_merged

    with db.session_scope() as s:
        p = upsert_patient(s, "P200", "Lê Thị Hoa", "0900000001", "Việt Nam")
        p.consent_status, p.edd, p.risk_groups = "granted", _edd_for(30), ["M-THA", "P-SINHNON"]
        from core.db import now
        from core.models import PatientProgram

        s.add(PatientProgram(patient_id=p.id, in_package=True, package_name="Gói (giả)", updated_at=now()))
        s.flush()
        monday = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7)  # M-THA gửi T2, T5
        p.edd = monday + timedelta(days=280 - 30 * 7)
        save_plan(s, plan_forms(build_states(s, monday), monday, rules), monday, rules.rules_version)
        ch = MockSmsChannel()
        export_form_sms(s, monday, rules, FORMS, ch, None)
        assert len(ch.sent) == 1  # một SMS
        assert "+" in ch.sent[0][1].params["P"]  # tên form gộp: "Theo dõi 29–31 tuần + Theo dõi huyết áp + …"
        rows = s.scalars(select(FormSchedule).where(FormSchedule.patient_id == p.id)).all()
        assert {r.status for r in rows} == {"exported"} and len({r.token for r in rows}) == 1
        tok = s.scalar(select(FormToken).where(FormToken.patient_id == p.id))
        codes = token_form_codes(s, tok)
        assert codes[0] == "F5" and set(codes) == {"F5", "M-THA"}  # bộ sinh non ghép thẳng vào câu của F5
        qs = compose_merged(codes, p.risk_groups, ga_at(p.edd, monday), rules, FORMS)
        assert len(qs) == len(set(qs)) and {"C6", "C7", "C9", "BP_AM_SYS"} <= set(qs)


def test_auto_apply_only_doctor_documented_sources(db, rules) -> None:
    """docs/13 mục 4: căn cứ bác sĩ ghi (nhóm nguy cơ hồ sơ) → tự áp dụng + audit; chỉ AI đọc ghi chú → chờ xác nhận."""
    from sqlalchemy import func, select

    from core.models import AuditLog
    from core.patients import auto_apply_form_plans, save_history
    from core.repo import upsert_patient

    with db.session_scope() as s:
        doc = upsert_patient(s, "P401", "Lê Thị Hoa", "0900000001", "Việt Nam")
        ai = upsert_patient(s, "P402", "Trần Thị Mai", "0900000002", "Việt Nam")
        for p in (doc, ai):
            p.consent_status, p.edd = "granted", _edd_for(28)
        save_history(s, doc.id, {"risk_summary": ["TWINS"]}, source="synthetic",
                     at=datetime.combine(TODAY, datetime.min.time()))
        ai.proposed_risk_flags = ["TWINS"]
        s.flush()
        applied = dict(auto_apply_form_plans(s, TODAY, rules, FORMS))
        assert applied.get(doc.code) == ["P-SINHNON"] and "P-SINHNON" in doc.risk_groups
        assert ai.code not in applied and "P-SINHNON" not in (ai.risk_groups or [])
        assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "auto_apply_form_plan")) == 1
        assert auto_apply_form_plans(s, TODAY, rules, FORMS) == []  # lần sau không áp dụng lại


def test_no_reply_call_list_only_risk_patients(db, rules) -> None:
    from datetime import datetime

    from core.forms import create_token, no_reply_call_list
    from core.models import FormToken
    from core.repo import upsert_patient

    with db.session_scope() as s:
        risk = upsert_patient(s, "P501", "Lê Thị Hoa", "0900000001", "Việt Nam")
        low = upsert_patient(s, "P502", "Trần Thị Mai", "0900000002", "Việt Nam")
        risk.consent_status, low.consent_status, risk.risk_groups = "granted", "granted", ["P-SINHNON"]
        s.flush()
        at = datetime(2026, 11, 20, 9, 0)
        for p in (risk, low):
            t = s.get(FormToken, create_token(s, p.id, "F5", 3))
            t.created_at, t.reminded_at, t.expires_at = at - timedelta(days=4), at - timedelta(days=2), at - timedelta(days=1)
        s.flush()
        assert [p.code for p, _f in no_reply_call_list(s, at, rules)] == [risk.code]
