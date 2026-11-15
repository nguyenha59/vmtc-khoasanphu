"""One-sign alert tiers: configured urgent signs and benign mentions differ."""

from __future__ import annotations

import pytest

from core.ai.form_reader import read_free_text
from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms
from core.rules.triage_rules import triage


@pytest.mark.parametrize("answers,expected", [
    ({"C4": "Có"}, "yellow"), ({"C5": "Có"}, "yellow"), ({"C7": "Có"}, "yellow"),
    ({"ITCH": "Có"}, "yellow"), ({"C1": "Có"}, "red"), ({"C2": "Có"}, "red"),
    ({"C3": "Có"}, "red"), ({"C6": "Có"}, "red"), ({"C8": "Có"}, "red"),
    ({"C1": "Không chắc"}, "yellow"), ({}, "green"),
])
def test_single_answer_uses_its_rule_not_symptom_count(rules, answers, expected):
    assert triage(answers, 30, False, rules).level.code == expected


@pytest.mark.parametrize("text,expected", [
    ("Em sốt", "yellow"), ("Em tiểu buốt", "yellow"), ("Em đau bụng nhẹ", "yellow"),
    ("Em ngứa lòng bàn tay", "yellow"), ("Em không ra máu", "green"),
    ("Em xoa bắp chân", "green"), ("Em đau sưng một bên bắp chân", "red"),
    ("Em không đau bắp chân", "green"), ("Bắp chân em không sưng", "green"),
    ("Chân răng em chảy máu khi đánh răng", "green"),
    ("Bắp chân không đau mà sưng đỏ", "red"),
    ("Bắp chân không đau nhưng sưng đỏ một bên", "red"),
    ("Em ra máu âm đạo", "red"), ("Em khỏe", "green"),
])
def test_single_free_text_sign_does_not_always_mean_red(rules, text, expected):
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=MockClient(), history={}, ner=False)
    assert result.level.code == expected


def test_evidence_offsets_refer_to_masked_input(rules):
    reading = read_free_text("Chị Nguyễn Thị Lan nói em đau bụng nhẹ", 30, MockClient(), rules,
                             patient_name="Nguyễn Thị Lan", ner=False)
    assert reading.findings
    for f in reading.findings:
        if f.evidence:
            assert reading.masked_text[f.evidence_start:f.evidence_end] == f.evidence
            assert "Nguyễn Thị Lan" not in f.evidence


def test_known_red_pressure_does_not_call_model(rules):
    client = MockClient(lambda *_: pytest.fail("Confirmed red must not wait for optional model"))
    result = assess_answers("F5", {}, "Huyết áp em đo là 170/115", 30, rules, load_forms(),
                            client=client, history={}, ner=False)
    assert result.level.code == "red" and client.calls == 0


def test_mild_headache_is_yellow_per_p01(rules):
    """P01 (người dùng chọn 15/11/2026): đau đầu nhẹ = Vàng; nặng / không đỡ / kèm dấu hiệu = Đỏ."""
    result = assess_answers("F5", {}, "Em hơi đau đầu", 30, rules, load_forms(),
                            client=MockClient(), history={}, ner=False)
    assert rules["symptom_concepts"]["HEADACHE"]["level"] == "yellow"
    assert result.level.code == "yellow"
    assert not any(f.level == "red" for f in result.reading.findings)


def test_model_unknown_severity_cannot_erase_explicit_mild_headache(rules):
    client = MockClient(lambda *_: {"findings": [{"concept": "HEADACHE", "present": True,
        "experiencer": "patient", "temporality": "current", "severity": "unknown", "evidence": "hơi đau đầu"}],
        "unmapped_concerns": []})
    result = assess_answers("F5", {}, "Em hơi đau đầu", 30, rules, load_forms(), client=client,
                            history={}, ner=False)
    assert result.level.code == "yellow"
    assert result.reading.findings[0].severity == "mild"
