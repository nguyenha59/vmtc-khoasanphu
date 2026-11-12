"""Quy trình ca đỏ (thống nhất 11/11/2026) + form sau sinh.

- "Giữ đỏ": điều dưỡng → Chờ bác sĩ; bác sĩ đóng bằng một quyết định soạn sẵn.
- Hạ vàng / báo nhầm: điều dưỡng đóng ngay; vào danh sách bác sĩ xem lại theo tỷ lệ mẫu, lý do nguy hiểm nhất luôn xem.
- Ca đỏ phải có "mức sau gọi".
- Form sau sinh S1–S4 gửi bù được trong postpartum_grace_days.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from core.ai.llm_client import MockClient
from core.config import Rules

REVIEW = {"downgrade_sample_rate": 1.0, "always_review_rule_codes": ["C1", "C8", "EPDS_10"],
          "always_review_concepts": ["BLEEDING", "REDUCED_FM", "SELF_HARM"]}


def _with(rules: Rules, **review) -> Rules:
    """Bản cấu hình trong bộ nhớ có đủ khóa của quy trình mới (không phụ thuộc file thật đã cập nhật chưa)."""
    data = copy.deepcopy(rules.data)
    data["alert_review"] = {**REVIEW, **data.get("alert_review", {}), **review}
    data["forms"].setdefault("postpartum_grace_days", 2)
    return Rules(data=data, path=rules.path)


def test_rules_file_has_valid_review_policy(rules):
    """config/rules.yaml thật: đủ khóa, tỷ lệ 0–1, mã câu hỏi / khái niệm phải tồn tại (gõ sai = không bao giờ xem)."""
    from core.rules.triage_rules import RED_ITEMS

    cfg = rules["alert_review"]
    assert 0.0 <= float(cfg["downgrade_sample_rate"]) <= 1.0
    known_codes = set(RED_ITEMS) | {"EPDS_10"}
    assert set(cfg["always_review_rule_codes"]) <= known_codes
    assert set(cfg["always_review_concepts"]) <= set(rules["symptom_concepts"])
    assert int(rules["forms"]["postpartum_grace_days"]) >= 0
    for key in ("alert_review.downgrade_sample_rate", "forms.postpartum_grace_days"):
        assert key in rules["rule_meta"]
    assert rules["rule_meta"]["alert_review.downgrade_sample_rate"]["requires_doctor"] is True


# ------------------------------------------------------------------ chọn ca cần bác sĩ xem lại
def test_always_review_dangerous_reasons_even_at_zero_rate(rules):
    from core.alerts import needs_downgrade_review

    r = _with(rules, downgrade_sample_rate=0.0)
    assert needs_downgrade_review(1, {"rule": [{"code": "C1"}]}, r)  # ra máu
    assert needs_downgrade_review(2, {"ai": [{"concept": "SELF_HARM", "present": True, "level": "red"}]}, r)
    assert not needs_downgrade_review(3, {"rule": [{"code": "DYSPNEA"}]}, r)
    assert not needs_downgrade_review(4, {"ai": [{"concept": "BLEEDING", "present": False, "level": "green"}]}, r)


def test_sample_rate_is_deterministic_and_proportional(rules):
    from core.alerts import needs_downgrade_review

    f = {"rule": [{"code": "DYSPNEA"}]}
    assert all(needs_downgrade_review(i, f, _with(rules, downgrade_sample_rate=1.0)) for i in range(50))
    half = [needs_downgrade_review(i, f, _with(rules, downgrade_sample_rate=0.2)) for i in range(2000)]
    assert half == [needs_downgrade_review(i, f, _with(rules, downgrade_sample_rate=0.2)) for i in range(2000)]
    assert 0.15 < sum(half) / len(half) < 0.25


# ------------------------------------------------------------------ đóng ca đỏ
@pytest.fixture()
def red_alerts(db, rules):
    """Tạo cảnh báo đỏ theo yêu cầu: make(answer_code) → alert_id."""
    from core.alerts import submit_response
    from core.config import load_forms
    from core.models import Patient
    from core.repo import upsert_patient

    forms = load_forms()
    with db.session_scope() as s:
        upsert_patient(s, "P0001", "Phạm Thị Thu", "0900000001", "Việt Nam")
        p = s.query(Patient).one()
        p.edd, p.consent_status = date.today() + timedelta(days=60), "granted"
    base = {q: "Không" for q in forms.forms["F6"]["questions"] if forms.questions[q]["type"] == "yesno"}

    def make(code: str) -> int:
        with db.session_scope() as s:
            _r, _a, alert, _ = submit_response(s, s.query(Patient).one(), "F6", base | {code: "Có"}, "", rules=rules,
                                               forms_cfg=forms, client=MockClient(), alert_channel=None, ner=False)
            assert alert.level == "red"
            return alert.id

    return make


def _close(db, aid, role, final, decision=None, rules=None):
    from core.alerts import close_alert

    with db.session_scope() as s:
        return close_alert(s, aid, user_id=7, role=role, final_level=final, note="đã gọi", decision=decision, rules=rules)


def _reviews(db, aid):
    from core.models import AlertReview

    with db.session_scope() as s:
        return [(r.kind, r.status, r.decision, r.outcome) for r in s.query(AlertReview).filter_by(alert_id=aid)]


def test_nurse_keep_red_goes_to_doctor_then_doctor_decides(db, rules, red_alerts):
    from core.alerts import HandleError
    from core.models import Alert

    r = _with(rules)
    aid = red_alerts("C1")
    assert _close(db, aid, "nurse", "keep_red", rules=r) == "awaiting_doctor"
    with db.session_scope() as s:
        assert s.get(Alert, aid).status == "escalated"
    assert _reviews(db, aid) == [("doctor_decision", "pending", None, None)]
    with pytest.raises(HandleError):  # bác sĩ giữ đỏ mà không chọn quyết định
        _close(db, aid, "doctor", "keep_red", rules=r)
    assert _close(db, aid, "doctor", "keep_red", decision="visit_today", rules=r) == "closed"
    with db.session_scope() as s:
        assert s.get(Alert, aid).status == "closed"
    assert _reviews(db, aid) == [("doctor_decision", "done", "visit_today", None)]


def test_red_requires_level_after_call(db, rules, red_alerts):
    from core.alerts import HandleError

    with pytest.raises(HandleError):
        _close(db, red_alerts("C1"), "nurse", None, rules=_with(rules))


def test_nurse_downgrade_goes_to_review_list_doctor_downgrade_does_not(db, rules, red_alerts):
    r0 = _with(rules, downgrade_sample_rate=0.0)
    a_bleed, a_dysp, a_doc = red_alerts("C1"), red_alerts("DYSPNEA"), red_alerts("C1")
    assert _close(db, a_bleed, "nurse", "false_alarm", rules=r0) == "closed"
    assert _reviews(db, a_bleed) == [("downgrade_review", "pending", None, None)]  # ra máu: luôn xem lại
    _close(db, a_dysp, "nurse", "down_yellow", rules=r0)
    assert _reviews(db, a_dysp) == []  # tỷ lệ mẫu 0 và không thuộc lý do luôn xem
    _close(db, a_doc, "doctor", "down_yellow", rules=r0)
    assert _reviews(db, a_doc) == []  # bác sĩ tự hạ thì không cần ai xem lại


def test_ack_and_reopen(db, rules, red_alerts):
    from core.alerts import ack_downgrade_reviews, reopen_from_review
    from core.models import Alert, AlertReview

    r = _with(rules)
    a1, a2 = red_alerts("C1"), red_alerts("C8")
    _close(db, a1, "nurse", "down_yellow", rules=r)
    _close(db, a2, "nurse", "false_alarm", rules=r)
    with db.session_scope() as s:
        rid = {x.alert_id: x.id for x in s.query(AlertReview).filter_by(kind="downgrade_review")}
    with db.session_scope() as s:
        assert ack_downgrade_reviews(s, [rid[a1]], 9) == 1
    with db.session_scope() as s:
        assert reopen_from_review(s, rid[a2], 9) == a2
    with db.session_scope() as s:
        assert s.get(Alert, a2).status == "escalated"
    assert _reviews(db, a1) == [("downgrade_review", "done", None, "agree")]
    assert ("doctor_decision", "pending", None, None) in _reviews(db, a2)


# ------------------------------------------------------------------ form sau sinh
def test_postpartum_forms_catch_up_within_grace(rules):
    from core.rules.form_scheduler import PatientState, plan_for_patient

    r = _with(rules)
    today = date(2026, 11, 11)

    def plan(days_after: int, answered: tuple[str, ...] = ()) -> list[str]:
        st = PatientState(patient_id=1, code="R0001", edd=None, delivery_date=today - timedelta(days=days_after),
                          consent="granted", answered=set(answered))
        return [d.form_code for d in plan_for_patient(st, today, r) if d.send]

    grace = r["forms"]["postpartum_grace_days"]
    assert plan(3) == ["S1"] and plan(3 + grace) == ["S1"]  # đúng ngày và gửi bù
    assert plan(3 + grace + 1) == []  # quá hạn gửi bù
    assert plan(4, answered=("S1",)) == []  # đã trả lời thì không gửi lại
    assert plan(42) == ["S4"]
