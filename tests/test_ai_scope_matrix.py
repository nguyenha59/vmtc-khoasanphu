"""Independent source-labelled permutations across symptoms, subjects, time and conditionals."""

from __future__ import annotations

import json
import unicodedata

import pytest

from core.ai.form_reader import _vote_unmapped
from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms
from core.textutil import deaccent

SYMPTOMS = [
    ("ra máu âm đạo", "red", "yellow"),
    ("nhìn mờ", "red", "green"),
    ("khó thở", "red", "green"),
    ("đau đầu", "yellow", "green"),
    ("đau bụng", "yellow", "green"),
    ("sốt", "yellow", "green"),
]


def source_cases():
    cases = []
    for symptom, active, resolved in SYMPTOMS:
        cases.append((f"Em đang {symptom}", active))
        for negative in ["không", "không hề bị", "không hề thấy bị", "chưa", "không có dấu hiệu",
                         "không có triệu chứng", "không còn tình trạng", "phủ nhận"]:
            cases.append((f"Em {negative} {symptom}", "green"))
        cases.append((f"{symptom}: Không", "green"))
        for subject in ["Mẹ em", "Chồng em", "Chị gái em"]:
            cases.append((f"{subject} đang {symptom}", "green"))
            cases.append((f"{subject} hôm nay bị {symptom}", "green"))
            cases.append((f"{subject} tuần trước bị {symptom}", "green"))
        cases.append((f"Người bị {symptom} là mẹ em", "green"))
        cases.append((f"Hôm qua em {symptom}, giờ hết rồi", resolved))
        cases.append((f"Em đã hết {symptom}", resolved))
        persistent = "red" if symptom == "đau đầu" else active  # P01: đau đầu không đỡ là dấu hiệu nặng.
        cases.append((f"Em vẫn chưa hết {symptom}", persistent))
        cases.append((f"Em vẫn không hết {symptom}", persistent))
        cases.append((f"Bác sĩ dặn nếu em {symptom} thì đi khám", "green"))
    for connector in ["và", "hoặc", "hay"]:
        cases.append((f"Nếu ra máu âm đạo {connector} khó thở thì đi khám", "green"))
        cases.append((f"Nếu em ra máu âm đạo {connector} em khó thở thì đi khám", "green"))
        cases.append((f"Em không ra máu âm đạo {connector} khó thở", "green"))
    cases += [
        ("Em đau bụng nhẹ và em uống nhiều nước", "yellow"),
        ("Em đau đầu nhẹ và em uống nhiều nước", "yellow"),
        ("Em đau đầu, giờ đã đỡ nhiều", "yellow"),
        ("Mẹ em nhìn mờ nhưng em đang khó thở", "red"),
        ("Em đã hết nhìn mờ nhưng em lại nhìn mờ", "red"),
        ("Em không ra máu âm đạo nhưng khó thở", "red"),
        ("Em ra máu âm đạo nhưng không khó thở", "red"),
    ]
    return cases


CASES = source_cases()


@pytest.mark.parametrize("text,expected", CASES)
@pytest.mark.parametrize("form", ["original", "ascii", "nfd", "upper"])
def test_source_scope_variants(rules, text, expected, form):
    text = {"original": text, "ascii": deaccent(text), "nfd": unicodedata.normalize("NFD", text),
            "upper": text.upper()}[form]
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=MockClient(), history={}, ner=False)
    assert result.level.code == expected, (text, result.reasons)


@pytest.mark.parametrize("text", [
    "Bác sĩ dặn nếu đau lưng nhiều thì gọi lại",
    "Hôm qua đau lưng nhiều, giờ hết rồi",
    "Em không đau lưng nhiều hay mất ngủ",
])
def test_unmapped_concern_keeps_source_modality(rules, text):
    assert _vote_unmapped([["đau lưng nhiều"]] * 3, text, rules) == []


@pytest.mark.parametrize("text", [
    "Em đau lưng nhiều",
    "Hôm qua đau lưng nhiều, giờ vẫn đau lưng nhiều",
    "Mẹ em đau lưng nhiều nhưng em cũng đau lưng nhiều",
])
def test_real_unmapped_concern_is_not_erased(rules, text):
    assert _vote_unmapped([["đau lưng nhiều"]] * 3, text, rules)


@pytest.mark.parametrize("text", [
    "Em thấy khó chịu", "Em mệt quá", "Em thấy lạ trong người", "Em đau mà không rõ đau ở đâu",
    "Em không chắc có ra máu", "Ra máu có nguy hiểm không?", "Cho em hỏi nhìn mờ có nguy hiểm không?",
])
def test_incomplete_or_doubtful_health_description_requires_review(rules, text):
    client = MockClient(lambda *_: {"findings": [], "unmapped_concerns": []})
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=client, history={}, ner=False)
    assert result.level.code == "yellow" and result.combined.needs_human


@pytest.mark.parametrize("text", ["Em không mệt", "Mẹ em hôm nay mệt", "Em đã hết đau", "Em vẫn khỏe"])
def test_unresolved_cues_respect_source_context(rules, text):
    client = MockClient(lambda *_: {"findings": [], "unmapped_concerns": []})
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=client, history={}, ner=False)
    assert result.level.code == "green"


@pytest.mark.parametrize("text", ["Em đang ra máu có nguy hiểm không?", "Em bị ra máu có sao không?",
                                 "Cho em hỏi em bị ra máu có nguy hiểm không?"])
def test_question_about_real_current_event_stays_red(rules, text):
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=MockClient(), history={}, ner=False)
    assert result.level.code == "red"


@pytest.mark.parametrize("text", ["Em không đi lại được", "Em không ngủ được", "Em không ăn uống được"])
def test_functional_difficulty_is_not_dismissed_as_negation(rules, text):
    assert _vote_unmapped([[text]] * 3, text, rules)


def test_unclear_description_audit_is_masked(rules):
    client = MockClient(lambda *_: {"findings": [], "unmapped_concerns": []})
    name = "Nguyễn Thị Lan"
    result = assess_answers("F5", {}, f"{name} nói em mệt", 30, rules, load_forms(), client=client,
                            patient_name=name, history={}, ner=False)
    assert result.combined.needs_human
    assert name not in json.dumps(result.reading.raw, ensure_ascii=False)


def test_mild_or_negated_mention_does_not_erase_another_severe_event(rules):
    text = "Em không đau đầu nhưng trán nhức như búa bổ"
    out = {"findings": [{"concept": "HEADACHE", "present": True, "temporality": "current",
                        "experiencer": "patient", "severity": "severe", "evidence": "trán nhức như búa bổ"}],
           "unmapped_concerns": []}
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=MockClient(lambda *_: out), history={}, ner=False)
    assert result.level.code == "red"


def test_explicit_mild_abdominal_pain_not_made_severe_by_another_activity(rules):
    text = "Em đau bụng nhẹ và em uống nhiều nước"
    out = {"findings": [{"concept": "ABD_PAIN", "present": True, "temporality": "current",
                        "experiencer": "patient", "severity": "severe", "evidence": "đau bụng nhẹ"}],
           "unmapped_concerns": []}
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=MockClient(lambda *_: out), history={}, ner=False)
    assert result.level.code == "yellow"
    assert result.reading.findings[0].severity == "mild"
