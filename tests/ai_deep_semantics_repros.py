"""Opt-in round-two semantic reproductions; synthetic data and mocked LLM only.

Run explicitly: python -m pytest tests/ai_deep_semantics_repros.py -ra --tb=short
The failing assertions describe open problems; this filename is not auto-discovered.
"""

from __future__ import annotations

import pytest

from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms
from core.levels import Level
from core.rules.triage_rules import triage


def finding(concept, evidence, *, present=True, experiencer="patient", severity="unknown"):
    return {"concept": concept, "present": present, "temporality": "current",
            "experiencer": experiencer, "severity": severity, "evidence": evidence}


def assess(text, rules, findings=None, unmapped=None):
    client = MockClient(lambda *_: {"findings": findings or [], "unmapped_concerns": unmapped or []})
    return assess_answers("F5", {}, text, 30, rules, load_forms(), client=client, history={}, ner=False)


@pytest.mark.parametrize("text,items", [
    ("Ra máu thì không, đau bụng cũng không", [
        finding("BLEEDING", "Ra máu thì không", present=False),
        finding("ABD_PAIN", "đau bụng cũng không", present=False, severity="mild"),
    ]),
    ("Em không ra máu hay ra nước", [
        finding("BLEEDING", "không ra máu", present=False),
        finding("FLUID", "không ra máu hay ra nước", present=False),
    ]),
])
def test_sem01_negation_after_or_shared_across_list_must_not_be_red(rules, text, items):
    result = assess(text, rules, items)
    assert result.level < Level.RED, {"got": result.level.code,
                                     "findings": [vars(f) for f in result.reading.findings]}


@pytest.mark.parametrize("text,items", [
    ("Chồng em đang sốt và khó thở", [
        finding("FEVER", "Chồng em đang sốt", experiencer="other"),
        finding("DYSPNEA", "Chồng em đang sốt và khó thở", experiencer="other"),
    ]),
    ("Mẹ em bị sốt, khó thở và nhìn mờ", [
        finding("FEVER", "Mẹ em bị sốt", experiencer="other"),
        finding("DYSPNEA", "khó thở", experiencer="other"),
        finding("VISUAL", "nhìn mờ", experiencer="other"),
    ]),
])
def test_sem02_subject_must_survive_conjunction_and_comma(rules, text, items):
    result = assess(text, rules, items)
    assert result.level < Level.RED, {"got": result.level.code,
                                     "findings": [vars(f) for f in result.reading.findings]}


def test_sem03_conditional_instruction_is_not_current_symptom(rules):
    text = "Em không có triệu chứng. Bác sĩ dặn nếu ra máu thì đi khám."
    result = assess(text, rules)
    assert result.level < Level.RED, {"got": result.level.code,
                                     "findings": [vars(f) for f in result.reading.findings]}


def test_sem04_anatomical_site_must_match_vaginal_bleeding_concept(rules):
    text = "Em chảy máu chân răng khi đánh răng"
    result = assess(text, rules, unmapped=["chảy máu chân răng"])
    assert result.level < Level.RED, {"got": result.level.code,
                                     "findings": [vars(f) for f in result.reading.findings]}


def test_sem05_unrelated_degree_word_must_not_make_mild_pain_severe(rules):
    text = "Em đau bụng nhẹ và uống nhiều nước"
    result = assess(text, rules, [finding("ABD_PAIN", "đau bụng nhẹ", severity="mild")])
    assert result.level < Level.RED, {"got": result.level.code,
                                     "findings": [vars(f) for f in result.reading.findings]}


def test_sem06_explicit_bp_in_free_text_should_reach_same_red_threshold(rules):
    structured = triage({"BP_SYS": 170, "BP_DIA": 115}, 30, False, rules)
    result = assess("Huyết áp em đo là 170/115", rules, unmapped=["Huyết áp em đo là 170/115"])
    assert result.level >= structured.level, {
        "structured": structured.level.code, "free_text": result.level.code,
        "unmapped": result.reading.unmapped,
    }


@pytest.mark.parametrize("text,items,expected", [
    ("Em ra máu", [finding("BLEEDING", "ra máu")], Level.RED),
    ("Em không ra máu", [finding("BLEEDING", "không ra máu", present=False)], Level.GREEN),
    ("Chồng em khó thở", [finding("DYSPNEA", "Chồng em khó thở", experiencer="other")], Level.GREEN),
    ("Em đau bụng nhẹ", [finding("ABD_PAIN", "đau bụng nhẹ", severity="mild")], Level.YELLOW),
])
def test_control_supported_semantics(rules, text, items, expected):
    assert assess(text, rules, items).level == expected
