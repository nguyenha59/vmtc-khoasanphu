"""Replay saved API failures and protect real danger, uncertain evidence and unknown concerns."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from core.ai.form_reader import _covered_brushing_concern, _vote_unmapped
from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms

SAVED = json.loads((Path(__file__).parent / "golden/openai_failure_responses.json").read_text(encoding="utf-8"))


def assess(text, rules, outputs, answers=None):
    replies = iter(copy.deepcopy(outputs))
    return assess_answers("F5", answers or {}, text, 30, rules, load_forms(),
                          client=MockClient(lambda *_: next(replies)), history={}, ner=False)


def finding(concept, evidence, *, present=True, experiencer="patient", temporality="current"):
    return {"concept": concept, "present": present, "experiencer": experiencer, "temporality": temporality,
            "severity": "unknown", "evidence": evidence}


@pytest.mark.parametrize("case", SAVED["cases"], ids=lambda case: case["id"])
def test_saved_openai_failure_now_matches_source_supported_level(case, rules):
    original = copy.deepcopy(case["outputs"])
    result = assess(case["text"], rules, case["outputs"])
    assert result.level.code == case["expected"] == "green"
    assert not result.combined.needs_human and result.reading.confidence == 1.0
    assert result.reading.raw["outputs"] == original  # repair never overwrites provider evidence
    if case["id"] == "negation_02":
        assert len(result.reading.raw["evidence_repairs"]) == 2
        assert not result.reading.raw.get("rejected_findings")


@pytest.mark.parametrize("text", [
    "Chị bị chảy máu chân răng rất nhiều khi đánh răng",
    "Em chảy máu chân răng cả ngày, không cầm khi đánh răng",
    "Em chảy máu chân răng khi đánh răng và chóng mặt",
    "Em chảy máu chân răng khi đánh răng và ra máu âm đạo",
    "Em nôn ra máu khi đánh răng",
    "Em chảy máu chân răng",  # no narrow brushing context
])
def test_brushing_filter_does_not_drop_severity_new_symptoms_or_unsupported_context(rules, text):
    assert not _covered_brushing_concern(text, text, rules)
    assert _vote_unmapped([[text]] * 3, text, rules)


@pytest.mark.parametrize("text", ["Chảy máu chân răng khi đánh răng", "Em bi chay mau chan rang luc danh rang"])
def test_narrow_brushing_duplicate_is_excluded(rules, text):
    assert _covered_brushing_concern(text, text, rules)
    assert not _vote_unmapped([[text]] * 3, text, rules)


def test_brushing_filter_respects_disabled_benign_cap(rules):
    data = copy.deepcopy(rules.data)
    data["form_cap"]["enabled"] = False
    disabled = type(rules)(data=data, path=rules.path)
    text = "Em chảy máu chân răng khi đánh răng"
    assert not _covered_brushing_concern(text, text, disabled)
    assert _vote_unmapped([[text]] * 3, text, disabled)


@pytest.mark.parametrize("text,evidence", [
    ("Em không ra máu hay ra nước", "không ra nước và khó thở"),
    ("Em không ra máu. Em ra nước", "không ra nước"),
    ("Em không ra máu nhưng ra nước", "không ra nước"),
])
def test_shared_negation_cannot_repair_unrelated_words_or_positive_event(rules, text, evidence):
    out = {"findings": [finding("FLUID", evidence, present=False)], "unmapped_concerns": []}
    result = assess(text, rules, [out] * 3)
    assert not result.reading.raw.get("evidence_repairs")
    assert result.reading.raw.get("rejected_findings") == 3
    assert result.combined.needs_human
    if "Em ra nước" in text or "nhưng ra nước" in text:
        assert result.level.code == "red"


def test_present_claim_with_invented_shared_negative_quote_is_not_repaired(rules):
    out = {"findings": [finding("FLUID", "không ra nước")], "unmapped_concerns": []}
    result = assess("Em không ra máu hay ra nước", rules, [out] * 3)
    assert result.reading.raw["rejected_findings"] == 3
    assert result.level.code == "yellow" and result.combined.needs_human


def test_source_does_not_support_model_claim_that_event_is_resolved(rules):
    text = "Hôm qua em nhìn mờ"
    a = {"findings": [finding("VISUAL", text, temporality="past_resolved")], "unmapped_concerns": []}
    b = {"findings": [], "unmapped_concerns": []}
    result = assess(text, rules, [a, a, b])
    assert result.level.code == "red" and result.combined.needs_human


def test_resolved_bleeding_still_uses_its_yellow_history_rule(rules):
    text = "Hôm qua em ra máu, giờ hết rồi"
    a = {"findings": [finding("BLEEDING", text, temporality="past_resolved")], "unmapped_concerns": []}
    b = {"findings": [], "unmapped_concerns": []}
    result = assess(text, rules, [a, a, b])
    assert result.level.code == "yellow" and result.combined.needs_human


def test_current_patient_sign_is_not_hidden_by_family_or_old_event(rules):
    text = "Mẹ em nhìn mờ nhưng em cũng đang nhìn mờ"
    a = {"findings": [finding("VISUAL", "Mẹ em nhìn mờ", experiencer="other")], "unmapped_concerns": []}
    result = assess(text, rules, [a] * 3)
    assert result.level.code == "red"


def test_structured_red_is_preserved_even_with_benign_free_text(rules):
    text = "Em chảy máu chân răng khi đánh răng"
    out = {"findings": [], "unmapped_concerns": [text]}
    result = assess(text, rules, [out] * 3, answers={"C1": "Có"})
    assert result.level.code == "red"
