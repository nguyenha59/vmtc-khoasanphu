"""Clinical fact regressions and counterexamples for the AI core upgrade."""

from __future__ import annotations

import pytest

# Keep the original audit artifacts runnable while making their semantic cases
# part of normal pytest discovery.
from ai_audit_repros_20261114 import (
    test_ai01_current_symptom_must_not_be_resolved_by_past_keyword as test_ai01_current_symptom_must_not_be_resolved_by_past_keyword,
)
from ai_audit_repros_20261114 import (
    test_ai03_disagreement_about_unmapped_concern_must_not_be_confidence_one as test_ai03_disagreement_about_unmapped_concern_must_not_be_confidence_one,
)
from ai_audit_repros_20261114 import (
    test_ai14_findings_without_source_evidence_must_not_create_red_alert as test_ai14_findings_without_source_evidence_must_not_create_red_alert,
)
from ai_deep_semantics_repros import (
    test_control_supported_semantics as test_control_supported_semantics,
)
from ai_deep_semantics_repros import (
    test_sem01_negation_after_or_shared_across_list_must_not_be_red as test_sem01_negation_after_or_shared_across_list_must_not_be_red,
)
from ai_deep_semantics_repros import (
    test_sem02_subject_must_survive_conjunction_and_comma as test_sem02_subject_must_survive_conjunction_and_comma,
)
from ai_deep_semantics_repros import (
    test_sem03_conditional_instruction_is_not_current_symptom as test_sem03_conditional_instruction_is_not_current_symptom,
)
from ai_deep_semantics_repros import (
    test_sem04_anatomical_site_must_match_vaginal_bleeding_concept as test_sem04_anatomical_site_must_match_vaginal_bleeding_concept,
)
from ai_deep_semantics_repros import (
    test_sem05_unrelated_degree_word_must_not_make_mild_pain_severe as test_sem05_unrelated_degree_word_must_not_make_mild_pain_severe,
)
from ai_deep_semantics_repros import (
    test_sem06_explicit_bp_in_free_text_should_reach_same_red_threshold as test_sem06_explicit_bp_in_free_text_should_reach_same_red_threshold,
)

from core.ai.clinical_facts import blood_pressure_facts
from core.ai.form_reader import read_free_text
from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms
from core.levels import Level


def assess(text, rules, *, client=None, skip_llm=False, ner=False, answers=None):
    return assess_answers("F5", answers or {}, text, 30, rules, load_forms(),
                          client=client or MockClient(), history={}, skip_llm=skip_llm, ner=ner)


@pytest.mark.parametrize("text", [
    "Em không ra máu nhưng em có ra nước",
    "Mẹ em sốt và em khó thở",
    "Mẹ em bị sốt, sáng nay em nhìn mờ",
    "Em đau bụng nhiều và uống nước",
    "Em chảy máu chân răng nhưng cũng ra máu âm đạo",
    "Em ra máu, nếu nhiều hơn em sẽ đi khám",
    "Hôm qua ra máu, giờ lại ra máu",
    "Chồng đưa đi khám vì ra máu",
])
def test_scope_corrections_must_not_suppress_real_red(rules, text):
    assert assess(text, rules).level == Level.RED


@pytest.mark.parametrize("text,expected", [
    ("Hôm qua em nhìn mờ và hôm nay nhìn mờ nhiều hơn", Level.RED),
    ("Hôm qua em nhìn mờ", Level.RED),  # no proof it resolved
    ("Hôm qua em nhìn mờ, giờ hết rồi", Level.GREEN),
    ("Hôm qua em nhìn mờ, chồng đỡ rồi", Level.RED),
    ("Hôm qua em nhìn mờ, sáng nay hết thuốc", Level.RED),
])
def test_resolution_is_bound_to_the_event(rules, text, expected):
    assert assess(text, rules).level == expected


@pytest.mark.parametrize("text", [
    "Chồng em đo huyết áp 170/115 mmHg",
    "Huyết áp mẹ em đo là 170/115",
    "Bác sĩ dặn nếu huyết áp 170/115 thì đi khám",
    "Ngưỡng cảnh báo huyết áp 160/110 mmHg",
    "Huyết áp em không phải 170/115",
])
def test_bp_source_scope_does_not_create_false_current_measurements(rules, text):
    facts = blood_pressure_facts(text)
    assert facts and not any(f.usable for f in facts)
    assert assess(text, rules).level < Level.RED


@pytest.mark.parametrize("text", [
    "Huyết áp em đo là 170/115 mmHg",
    "Em vừa đo HA 165/112",
    "BP = 160 / 110 mm Hg",
    "Huyết áp em đo là 1600/115",  # invalid systolic cannot hide valid high diastolic
])
def test_explicit_current_bp_uses_existing_threshold_engine(rules, text):
    result = assess(text, rules)
    assert result.rule_level == Level.RED and result.level == Level.RED
    assert any(f.code.startswith("TEXT_") for f in result.rule.findings)


@pytest.mark.parametrize("text", [
    "Hôm qua huyết áp em là 170/115",
    "Huyết áp em đo là 17/11 kPa",
    "Huyết áp em đo là 170/115 psi",
    "Huyết áp em không tới 170/115",
])
def test_unclear_bp_time_or_unit_requires_review_without_silent_conversion(rules, text):
    result = assess(text, rules)
    assert result.level == Level.YELLOW and result.combined.needs_human
    assert not result.rule.readings


def test_skip_llm_avoids_all_model_calls_and_keeps_deterministic_red(rules):
    client = MockClient(lambda *_: pytest.fail("structured red path must not call LLM"))
    result = assess("Huyết áp em là 170/115", rules, client=client, skip_llm=True, answers={"C1": "Có"})
    assert result.level == Level.RED and result.summary_method == "template"
    assert client.calls == 0 and result.reading.raw["llm_status"] == "skipped_confirmed_red"


def test_bp_red_automatically_skips_llm_even_without_explicit_skip_flag(rules):
    client = MockClient(lambda *_: pytest.fail("confirmed free-text BP red must not wait for LLM"))
    result = assess("Huyết áp em là 170/115", rules, client=client)
    assert result.level == Level.RED and client.calls == 0
    assert result.reading.raw["llm_status"] == "skipped_confirmed_red"


def test_mask_failure_does_not_expose_original_lexicon_evidence(rules):
    def broken_ner(_text):
        raise RuntimeError("synthetic NER failure")

    client = MockClient(lambda *_: pytest.fail("failed masking must prevent LLM call"))
    reading = read_free_text("Trần Ngọc Mai hôm nay nhìn mờ", 30, client, rules, ner=broken_ner)
    assert not reading.mask_ok and reading.masked_text == ""
    assert reading.findings and all(f.evidence == "" for f in reading.findings)


def test_entire_evidence_must_be_grounded_not_just_first_forty_characters(rules):
    text = "Cảm ơn bác sĩ, hiện tại em thấy khỏe và đang nghỉ ngơi"
    bad = {"concept": "BLEEDING", "present": True, "temporality": "current", "experiencer": "patient",
           "severity": "unknown", "evidence": text[:45] + " nhưng có ra máu"}
    result = assess(text, rules, client=MockClient(lambda *_: {"findings": [bad], "unmapped_concerns": []}))
    assert result.level == Level.YELLOW and result.combined.needs_human
    assert result.reading.raw["rejected_findings"] == 3


def test_fact_source_spans_cover_the_concept_without_unbounded_text(rules):
    reading = read_free_text("Em nhìn mờ", 30, MockClient(), rules, ner=False)
    finding = reading.findings[0]
    assert finding.evidence_start is not None and finding.evidence_end is not None
    quote = "Em nhìn mờ"[finding.evidence_start:finding.evidence_end]
    assert quote == finding.evidence and "nhìn mờ" in quote and len(quote) <= 120
