from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from core.ai.combine import combine
from core.ai.form_reader import lexicon_findings, read_free_text
from core.ai.llm_client import MockClient
from core.ai.summary import numbers_ok, template_summary
from core.ai.trends import compute_trend
from core.levels import Level
from core.rules.triage_rules import triage

REDTEAM = Path(__file__).parent / "redteam" / "form_cases.yaml"


def lv(answers, ga=30, insulin=False, rules=None):
    return triage(answers, ga, insulin, rules).level


# ---------------- lớp 1: mỗi dòng bảng C1
@pytest.mark.parametrize("q", ["C1", "C2", "C3", "C6", "C8", "DYSPNEA", "CALF"])
def test_red_symptoms(rules, q):
    assert lv({q: "Có"}, rules=rules) == Level.RED


def test_contractions_before_37_red(rules):
    assert lv({"C9": "Có"}, ga=33, rules=rules) == Level.RED
    assert lv({"C9": "Có"}, ga=38, rules=rules) == Level.YELLOW


def test_bp_red_yellow(rules):
    assert lv({"BP_AM_SYS": 160, "BP_AM_DIA": 95}, rules=rules) == Level.RED
    assert lv({"BP_AM_SYS": 150, "BP_AM_DIA": 110}, rules=rules) == Level.RED
    assert lv({"BP_AM_SYS": 140, "BP_AM_DIA": 80}, rules=rules) == Level.YELLOW
    assert lv({"BP_AM_SYS": 130, "BP_AM_DIA": 90}, rules=rules) == Level.YELLOW
    assert lv({"BP_AM_SYS": 130, "BP_AM_DIA": 85}, rules=rules) == Level.GREEN


@pytest.mark.parametrize("q,v", [("GLU_FAST", 5.3), ("GLU_1H", 7.8), ("GLU_2H", 6.7)])
def test_glucose_yellow(rules, q, v):
    assert lv({q: v}, rules=rules) == Level.YELLOW
    assert lv({q: v - 0.1}, rules=rules) == Level.GREEN


def test_hypo_with_insulin_red(rules):
    assert lv({"HYPO": "Có"}, insulin=True, rules=rules) == Level.RED


@pytest.mark.parametrize("q", ["C4", "C5", "ITCH", "C7", "NAUSEA", "MISSED_MED"])
def test_yellow_symptoms(rules, q):
    assert lv({q: "Có"}, rules=rules) == Level.YELLOW


def test_fever_plus_pain_red(rules):
    r = triage({"C4": "Có", "C2": "Có"}, 30, False, rules)
    assert r.level == Level.RED and any(f.code == "FEVER_PLUS" for f in r.findings)


def test_edema_plus_c6_red(rules):
    r = triage({"C7": "Có", "C6": "Có"}, 30, False, rules)
    assert any(f.code == "EDEMA_PLUS" for f in r.findings)


def test_epds_item10_red_locked(rules):
    ans = {f"E{i}": 0 for i in range(1, 11)}
    ans["E10"] = 1
    assert lv(ans, rules=rules) == Level.RED
    assert rules["thresholds"]["epds_item10_red"]["locked"] is True


def test_epds_total_yellow(rules):
    ans = {f"E{i}": 2 for i in range(1, 10)}
    ans["E10"] = 0
    assert lv(ans, rules=rules) == Level.YELLOW


def test_implausible_values_flagged(rules):
    r = triage({"BP_AM_SYS": 300, "BP_AM_DIA": 90}, 30, False, rules)
    assert r.level == Level.YELLOW and r.implausible and not any(k == "bp_sys" for k, *_ in r.readings)
    r = triage({"GLU_FAST": 55}, 30, False, rules)
    assert r.level == Level.YELLOW and r.implausible


def test_all_no_is_green(rules):
    assert lv({"C1": "Không", "C2": "Không"}, rules=rules) == Level.GREEN


# ---------------- lớp 2: xu hướng
def _series(values):
    t0 = datetime(2026, 10, 1, 7)
    return [(t0 + timedelta(hours=12 * i), v) for i, v in enumerate(values)]


def test_trend_stable_no_alert(rules):
    r = compute_trend("bp_sys", _series([120, 123, 118, 121, 122, 119, 121, 120, 123]), rules)
    assert r.level == Level.GREEN


def test_trend_rising_alert(rules):
    r = compute_trend("bp_sys", _series([120, 122, 119, 121, 124, 128, 132, 136]), rules)
    assert r.level == Level.YELLOW


def test_trend_too_few_skipped(rules):
    r = compute_trend("bp_sys", _series([120, 150]), rules)
    assert r.level == Level.GREEN and "Chưa đủ" in r.reason


# ---------------- gộp: AI chỉ nâng mức
levels = st.sampled_from([Level.GREEN, Level.YELLOW, Level.RED])


@settings(max_examples=500, deadline=None)  # không giới hạn thời gian mỗi ví dụ: tránh chập chờn khi máy tải nặng
@given(rule=levels, trend=levels, ai=st.one_of(st.none(), levels), ok=st.booleans(),
       conf=st.one_of(st.none(), st.floats(0, 1)))
def test_combine_never_below_rule_level(rule, trend, ai, ok, conf):
    c = combine(rule, trend, ai, ai_ok=ok, ai_confidence=conf, min_confidence=0.8)
    assert c.level >= rule
    assert c.level >= trend


def test_combine_ai_error_is_yellow():
    c = combine(Level.GREEN, Level.GREEN, None, ai_ok=False, ai_confidence=None, min_confidence=0.8)
    assert c.level == Level.YELLOW and c.needs_human
    c = combine(Level.GREEN, Level.GREEN, Level.GREEN, ai_ok=True, ai_confidence=0.5, min_confidence=0.8)
    assert c.level == Level.YELLOW


def test_combine_ai_cannot_lower_red():
    c = combine(Level.RED, Level.GREEN, Level.GREEN, ai_ok=True, ai_confidence=1.0, min_confidence=0.8)
    assert c.level == Level.RED


# ---------------- lớp 3: đọc phần tự viết
def test_assertion_rules(rules):
    f = {x["concept"]: x for x in lexicon_findings("không ra máu, mẹ chồng bị sốt, hôm qua đau đầu giờ hết", rules=rules)}
    assert f["BLEEDING"]["present"] is False
    assert f["FEVER"]["experiencer"] == "other"
    assert f["HEADACHE"]["temporality"] == "past_resolved"
    f = {x["concept"]: x for x in lexicon_findings("bé ít đạp từ hôm qua đến giờ", rules=rules)}
    assert f["REDUCED_FM"]["temporality"] == "current"


def test_negation_window_does_not_hide_red(rules):
    r = read_free_text("không biết sao hôm nay ra máu", 30, MockClient(), rules, ner=False)
    assert r.level == Level.RED


def test_llm_misses_but_lexicon_catches(rules):
    """LLM trả rỗng nhưng văn bản có dấu hiệu đỏ → vẫn đỏ, độ tin cậy giảm."""
    empty = MockClient(lambda p, s, seed: {"findings": [], "unmapped_concerns": []})
    r = read_free_text("bé ít đạp", 30, empty, rules, ner=False)
    assert r.level == Level.RED and r.conflicts and r.confidence < 1.0


def test_llm_says_negated_rule_says_present_keeps_safer(rules):
    llm = MockClient(lambda p, s, seed: {"findings": [{
        "concept": "BLEEDING", "present": False, "temporality": "current", "experiencer": "patient",
        "severity": "unknown", "evidence": "sáng nay ra máu"}], "unmapped_concerns": []})
    r = read_free_text("sáng nay ra máu", 30, llm, rules, ner=False)
    assert r.level == Level.RED


def test_ai_failure_gives_yellow_floor(rules):
    from core.ai.llm_client import LlmError

    def boom(p, s, seed):
        raise LlmError("x")

    r = read_free_text("em hơi mệt", 30, MockClient(boom), rules, ner=False)
    c = combine(Level.GREEN, Level.GREEN, r.level, ai_ok=r.ok, ai_confidence=r.confidence, min_confidence=0.8)
    assert c.level == Level.YELLOW


def test_redteam_100pct_red_caught(rules):
    cases = yaml.safe_load(REDTEAM.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 50
    client = MockClient()
    missed = []
    for c in cases:
        r = read_free_text(c["text"], c["ga_weeks"], client, rules, ner=False)
        if c["expected_level"] == "red" and r.level != Level.RED:
            missed.append(c["text"])
    assert missed == []


def test_redteam_levels_match(rules):
    cases = yaml.safe_load(REDTEAM.read_text(encoding="utf-8"))["cases"]
    wrong = [(c["text"], c["expected_level"], read_free_text(c["text"], c["ga_weeks"], MockClient(), rules,
                                                               ner=False).level.code)
             for c in cases]
    wrong = [w for w in wrong if w[1] != w[2]]
    assert wrong == []


# ---------------- tóm tắt
def test_summary_template():
    s = template_summary({"ga": "32+1", "risk_groups": ["M-THA"], "vitals": {"bp": ("145/92", "xu hướng tăng")},
                          "concepts": [("Rối loạn thị giác", "current")], "level": "red", "reasons": ["AI: x"]})
    assert "32+1" in s and "145/92" in s and "ĐỎ" in s


def test_summary_number_check():
    assert numbers_ok("HA 145/92", {"bp": "145/92"})
    assert not numbers_ok("HA 170/100", {"bp": "145/92"})


# ---------------- kiểm thử chấp nhận 4 (qua CSDL)
def test_acceptance_r0412_red_alert(db, rules):
    from core.alerts import submit_response
    from core.channels.alert_mock import MockAlertChannel
    from core.config import load_forms
    from core.models import Patient
    from core.repo import upsert_patient

    ch = MockAlertChannel(path=Path(db.db_url().replace("sqlite:///", "")).parent / "alerts.jsonl")
    with db.session_scope() as s:
        for i in range(412):
            upsert_patient(s, f"P{i:04d}", "Phạm Thị Thu", f"09000{i:05d}", "Việt Nam")
        p = s.query(Patient).filter_by(code="R0412").one()
        p.edd = date.today() + timedelta(days=280 - 32 * 7)
        p.consent_status = "granted"
        answers = {q: "Không" for q in load_forms().forms["F6"]["questions"]}
        _resp, asm, alert, res = submit_response(
            s, p, "F6", answers, "chiều nay hoa mắt, mặt hơi sưng", rules=rules, forms_cfg=load_forms(),
            client=MockClient(), alert_channel=ch, ner=False)
        assert asm.level == "red" and alert is not None
        assert alert.sent_at is None and not ch.sent  # chỉ gửi SAU khi lưu xong
        assert "Rối loạn thị giác" in asm.summary
        alert_id = alert.id
    from core.models import Alert

    with db.session_scope() as s:
        assert s.get(Alert, alert_id).sent_at is not None
    payload = json.dumps(ch.sent[0].to_dict(), ensure_ascii=False)
    assert "R0412" in payload and "Thu" not in payload
    assert not re.search(r"0\d{9}", payload)


def test_implausible_systolic_does_not_hide_red_diastolic(rules):
    """1600/115: tâm thu nhập sai nhưng tâm trương 115 ≥ 110 → vẫn ĐỎ (trước đây chỉ vàng)."""
    from core.rules.triage_rules import triage

    r = triage({"BP_SYS": "1600", "BP_DIA": "115"}, 30, False, rules)
    assert r.level.code == "red"
    assert any("phi lý" in f.reason for f in r.findings)
    assert ("bp_sys", 1600.0, None) not in r.readings  # số phi lý không lưu vào chuỗi đo


def test_missing_systolic_shown_as_dash(rules):
    from core.rules.triage_rules import triage

    r = triage({"BP_DIA": "112"}, 30, False, rules)
    assert r.level.code == "red" and "—/112" in r.findings[0].reason
