"""Opt-in AI review reproductions; synthetic data and mocked LLM/network only.

Run explicitly (the filename is outside normal pytest discovery):
  .venv\\Scripts\\python.exe -m pytest tests/ai_audit_repros_20261114.py -ra --tb=short

Assertions describe the desired behavior. Failures are evidence of open findings,
not regression failures introduced by this review. No production code is changed.
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from types import SimpleNamespace

import pytest

from core.ai.llm_client import LlmError, MockClient, OllamaClient, OpenAIClient
from core.ai.masking import mask_text
from core.assess import assess_answers
from core.config import load_forms
from core.db import now
from core.levels import Level


def _finding(concept: str, evidence: str) -> dict:
    return {"concept": concept, "present": True, "temporality": "current",
            "experiencer": "patient", "severity": "unknown", "evidence": evidence}


@pytest.mark.parametrize("model_reads_current", [False, True])
def test_ai01_current_symptom_must_not_be_resolved_by_past_keyword(rules, model_reads_current):
    text = "Hôm qua em nhìn mờ và hôm nay nhìn mờ nhiều hơn"
    client = (MockClient(lambda *_: {"findings": [_finding("VISUAL", "nhìn mờ")],
                                    "unmapped_concerns": []}) if model_reads_current else MockClient())
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=client, history={}, ner=False)
    assert result.level == Level.RED, {
        "got": result.level.code, "findings": [vars(f) for f in result.reading.findings],
        "conflicts": result.reading.conflicts,
    }


def test_ai02_ner_failure_must_prevent_sending_unmasked_names():
    def broken_ner(_text):
        raise RuntimeError("synthetic NER failure")

    result = mask_text("Nhờ chị Trần Ngọc Mai liên hệ giúp", ner=broken_ner)
    assert not result.ok or "Trần Ngọc Mai" not in result.text, {"ok": result.ok, "text": result.text}


def test_ai02_uppercase_full_name_must_not_be_treated_as_medical_acronym():
    result = mask_text("Liên hệ NGUYEN VAN BAO giúp", ner=lambda _text: ["NGUYEN VAN BAO"])
    assert "NGUYEN VAN BAO" not in result.text, {"ok": result.ok, "text": result.text}


def test_ai03_disagreement_about_unmapped_concern_must_not_be_confidence_one(rules):
    client = MockClient(lambda _p, _s, seed: {
        "findings": [], "unmapped_concerns": ["đau lưng nhiều"] if seed == 1000 else [],
    })
    result = assess_answers("F5", {}, "Em đau lưng nhiều", 30, rules, load_forms(),
                            client=client, history={}, ner=False)
    assert result.level >= Level.YELLOW, {
        "got": result.level.code, "confidence": result.reading.confidence,
        "needs_human": result.combined.needs_human, "unmapped": result.reading.unmapped,
    }


def test_ai04_repeated_code_in_one_run_must_not_count_as_three_runs():
    from core.ai.question_selector import combine_runs

    outs = [{"scores": [{"code": "C5", "score": 0.9}] * 3, "confidence": 0.9},
            {"scores": [], "confidence": 0.9}, {"scores": [], "confidence": 0.9}]
    scores, unstable, confidence = combine_runs(outs, {"C5"}, 0.3)
    assert "C5" not in scores, {"scores": scores, "unstable": unstable, "confidence": confidence}


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_ai05_null_provider_content_must_fall_back_instead_of_raising(rules, monkeypatch, tmp_path, provider):
    from core.ai import llm_client

    if provider == "ollama":
        client = OllamaClient("synthetic-model", url="http://127.0.0.1:11434")
        client._local = True
        reply = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"response": None})
        monkeypatch.setattr(llm_client.requests, "post", lambda *_a, **_k: reply)
    else:
        monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "spend.json")
        monkeypatch.setenv("OPENAI_BUDGET_USD", "1")
        client = OpenAIClient(api_key="synthetic-placeholder")
        reply = SimpleNamespace(status_code=200,
                                json=lambda: {"choices": [{"message": {"content": None}}]})
        monkeypatch.setattr(client, "_post", lambda _body: reply)
    result = assess_answers("F5", {"C1": "Có"}, "Em đau lưng nhiều", 30, rules, load_forms(),
                            client=client, history={}, ner=False)
    assert result.level == Level.RED and not result.reading.ok


def test_ai06_disagreeing_ga_must_not_overwrite_edd_with_confidence_one(rules):
    from core.pipeline import plan_appointment
    from core.repo import _update_patient_from_note
    from core.rules.merge import MergedAppt

    variants = iter([12, 20, 28, 36, 40])

    def responder(*_args):
        return {"explicit_arrival_time": None, "minutes_early": None, "needs_fasting": False,
                "ga_weeks": next(variants), "ga_days": 0, "risk_flags": [],
                "asks_confirmation": False, "evidence": "Thai 30 tuần"}

    appt = MergedAppt(pid="AI-AUDIT-ONLY", appt_date=now().date(),
                      start_time=time(9), end_time=None, services=["Khám thai"],
                      note="Thai 30 tuần, đến đúng giờ theo hướng dẫn", full_name="", phone="0900000000",
                      phone_raw="", nationality="Việt Nam", department="", doctor="",
                      booking_source="", booked_at=None)
    plan = plan_appointment(appt, rules, MockClient(responder), [], ner=False)
    patient = SimpleNamespace(edd=None, delivery_date=None, proposed_risk_flags=[])
    _update_patient_from_note(patient, plan)
    expected = appt.appt_date + timedelta(days=280 - 30 * 7)
    assert patient.edd in (None, expected), {
        "expected_edd": str(expected), "actual_edd": str(patient.edd),
        "confidence": plan.extraction.confidence, "chosen_ga": plan.decision.ga_weeks,
        "review": plan.decision.status,
    }


def test_ai07_unrelated_negation_must_not_remove_positive_clinical_context(rules):
    from core.rules.ai_context import medication_labels
    from core.rules.personalize import find_flags

    flags = find_flags({"medical_history": ["Có tăng huyết áp nhưng không đái tháo đường"]}, [], [], rules)
    meds = medication_labels(False, ["Không dùng aspirin vì đang dùng thuốc hạ áp"], rules)
    assert "HTN" in {f.flag for f in flags} and "Đang dùng thuốc hạ áp" in meds, {
        "flags": [f.flag for f in flags], "medications": meds,
    }


def _patient(session):
    from core.repo import upsert_patient

    p = upsert_patient(session, "AI-AUDIT-ONLY", "Người Giả Lập", "0900000000", "Việt Nam")
    p.consent_status = "granted"
    return p


def test_ai08_postpartum_selection_must_not_add_fetal_movement_questions(db, rules):
    from core.forms import create_token, snapshot_selection
    from core.patients import save_history

    with db.session_scope() as s:
        p = _patient(s)
        p.edd, p.delivery_date = now().date() + timedelta(days=4), now().date() - timedelta(days=3)
        save_history(s, p.id, {"risk_summary": ["TWINS"]}, source="synthetic-ai-audit")
        token = create_token(s, p.id, "S1", 3)
        s.flush()
        row = snapshot_selection(s, p, token, ["S1"], rules, load_forms(), client=None)
        codes = {q["code"] for q in row.questions}
        assert not {"C8", "C9"} & codes, {"postpartum_questions": sorted(codes)}


def test_ai09_summary_rewrite_must_not_swap_bp_or_change_alert_level(rules):
    from core.ai.summary import summarize

    data = copy.deepcopy(rules.data)
    data["llm"]["summary_llm_rewrite"] = True
    enabled = type(rules)(data=data, path=rules.path)
    source = {"ga": "30+0", "level": "red", "vitals": {"bp": ("160/90", "")},
              "risk_groups": [], "concepts": [], "reasons": ["Huyết áp cao"]}
    client = MockClient(lambda *_: {"text": "Huyết áp 90/160. Mức XANH, không có dấu hiệu bất thường."})
    text, method = summarize(source, enabled, client, ner=False)
    assert method == "template" or ("90/160" not in text and "XANH" not in text), {
        "method": method, "text": text,
    }


def test_ai10_known_red_form_must_be_persisted_before_waiting_for_optional_ai(db, rules):
    from core.alerts import submit_response
    from core.models import Alert

    observed_red_counts = []
    with db.session_scope() as s:
        p = _patient(s)
        s.flush()

        def responder(*_args):
            observed_red_counts.append(s.query(Alert).filter_by(level="red").count())
            raise LlmError("synthetic timeout")

        submit_response(s, p, "F5", {"C1": "Có"}, "Em thấy không khỏe", rules=rules,
                        forms_cfg=load_forms(), client=MockClient(responder), alert_channel=None, ner=False)
    assert all(n > 0 for n in observed_red_counts), {
        "red_alerts_present_at_each_llm_call": observed_red_counts,
        "default_wait_seconds_if_all_timeout": 3 * 120,
    }


def test_ai11_parallel_spend_updates_must_not_lose_one_call(monkeypatch, tmp_path):
    from core.ai import llm_client

    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "spend.json")
    barrier = threading.Barrier(2, timeout=5)

    def overlapping_add():
        barrier.wait()
        return llm_client._add_spend(0.01, 1, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(overlapping_add) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    actual = llm_client._read_spend()["days"][llm_client._today()]["usd"]
    assert actual == pytest.approx(0.02), {"expected_usd": 0.02, "recorded_usd": actual}


def test_ai11_budget_estimate_must_bound_requested_output(monkeypatch, tmp_path):
    from core.ai import llm_client

    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "spend.json")
    monkeypatch.setenv("OPENAI_BUDGET_USD", "0.005")
    monkeypatch.setenv("OPENAI_PRICE_IN", "0.4")
    monkeypatch.setenv("OPENAI_PRICE_OUT", "1.6")
    client = OpenAIClient(api_key="synthetic-placeholder")
    sent = []

    def fake_post(body):
        sent.append(body)
        return SimpleNamespace(status_code=200, json=lambda: {
            "usage": {"prompt_tokens": 1, "completion_tokens": 10000},
            "choices": [{"message": {"content": '{"text": "ok"}'}}],
        })

    monkeypatch.setattr(client, "_post", fake_post)
    try:
        client.generate_json("synthetic", {"type": "object"}, 0, 1)
    except llm_client.BudgetExceeded:
        return  # Refusing the call is also a valid way to preserve the budget.
    limits = [sent[0][k] for k in ("max_tokens", "max_completion_tokens") if k in sent[0]]
    assert limits and 0 < min(limits) * client.price_out / 1e6 <= client.budget, {
        "sent_body_keys": sorted(sent[0]), "daily_budget": client.budget,
        "actual_recorded_spend": llm_client.openai_spent(),
    }


def test_ai12_fallback_only_eval_must_not_be_counted_as_stable_ai(rules):
    from scripts.eval_question_selection import run_case

    def unavailable(*_args):
        raise LlmError("synthetic AI unavailable")

    case = {"id": "AI-AUDIT-ONLY", "ga": [30, 0], "flags": [], "confirmed": [], "form": "F5"}
    result = run_case(case, rules, load_forms(), MockClient(unavailable))
    assert result["ai_stable"] is not True, {
        "ai_ok": result["ai_ok"], "ai_stable": result["ai_stable"], "error": result["ai_error"],
    }


def test_ai13_demo_form_must_not_replace_real_history_for_selection(db, rules):
    from core.forms import selection_context
    from core.models import FormResponse

    with db.session_scope() as s:
        p = _patient(s)
        p.edd = now().date() + timedelta(days=70)
        s.add(FormResponse(patient_id=p.id, form_code="F5", submitted_at=now() - timedelta(days=1),
                           answers={"C5": "Có"}, source="app"))
        s.add(FormResponse(patient_id=p.id, form_code="F5", submitted_at=now(),
                           answers={"C5": "Không"}, source="demo"))
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert ctx.flagged_answers.get("C5") == "Có", {"flagged_answers": ctx.flagged_answers}


def test_ai14_findings_without_source_evidence_must_not_create_red_alert(rules):
    client = MockClient(lambda *_: {"findings": [_finding("BLEEDING", "ra máu")],
                                    "unmapped_concerns": []})
    result = assess_answers("F5", {}, "Cảm ơn bác sĩ, em thấy khỏe", 30, rules, load_forms(),
                            client=client, history={}, ner=False)
    assert result.level < Level.RED, {
        "got": result.level.code, "confidence": result.reading.confidence,
        "findings": [vars(f) for f in result.reading.findings],
    }


@pytest.mark.parametrize("text,expected", [
    ("Hôm nay em nhìn mờ", Level.RED),
    ("Em nhìn mờ từ hôm qua đến giờ", Level.RED),
    ("Hôm qua em nhìn mờ, giờ hết rồi", Level.GREEN),
])
def test_control_temporal_phrases_already_supported(rules, text, expected):
    result = assess_answers("F5", {}, text, 30, rules, load_forms(), client=MockClient(),
                            history={}, ner=False)
    assert result.level == expected


def test_control_standard_llm_error_keeps_known_red(rules):
    def unavailable(*_args):
        raise LlmError("synthetic timeout")

    result = assess_answers("F5", {"C1": "Có"}, "Em đau lưng nhiều", 30, rules, load_forms(),
                            client=MockClient(unavailable), history={}, ner=False)
    assert result.level == Level.RED and result.combined.needs_human


def test_control_known_patient_name_is_masked():
    result = mask_text("Trần Ngọc Mai nhờ gọi lại", full_name="Trần Ngọc Mai", ner=False)
    assert result.ok and "Trần Ngọc Mai" not in result.text


def test_control_missing_question_score_in_two_runs_is_normally_rejected():
    from core.ai.question_selector import combine_runs

    outs = [{"scores": [{"code": "C5", "score": 0.9}], "confidence": 0.9},
            {"scores": [], "confidence": 0.9}, {"scores": [], "confidence": 0.9}]
    scores, unstable, _confidence = combine_runs(outs, {"C5"}, 0.3)
    assert "C5" not in scores and "C5" in unstable
