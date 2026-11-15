"""Regression coverage for temporal clinical facts, follow-up memory and question selection."""

from __future__ import annotations

import asyncio
from datetime import time, timedelta
from types import SimpleNamespace

import pytest

from core.ai.llm_client import LlmError, MockClient
from core.ai.question_selector import combine_runs
from core.config import load_forms
from core.db import now
from core.rules.ai_context import measurement_labels, medication_labels, medication_statuses


def _patient(s):
    from core.repo import upsert_patient

    p = upsert_patient(s, "CONTEXT-UPGRADE", "Người Giả Lập", "0900000000", "Việt Nam")
    p.edd = now().date() + timedelta(days=70)
    p.consent_status = "granted"
    return p


@pytest.mark.parametrize("latest_source", ["app", "demo"])
def test_memory_keeps_each_question_until_explicitly_answered(db, rules, latest_source):
    from core.forms import selection_context
    from core.models import FormResponse

    with db.session_scope() as s:
        p = _patient(s)
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=now() - timedelta(days=1),
                           answers={"C5": "Có", "C1": "Không chắc"}, source="app"))
        s.add(FormResponse(patient_id=p.id, form_code="M-DTD", submitted_at=now(),
                           answers={"GLU_FAST": 4.7, **({"C5": "Không"} if latest_source == "demo" else {})},
                           source=latest_source))
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert ctx.flagged_answers == {"C5": "Có", "C1": "Không chắc"}
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=now(), answers={"C5": "Không"}, source="app"))
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert ctx.flagged_answers == {"C1": "Không chắc"}


def test_memory_ignores_future_answers_at_replay_time(db, rules):
    from core.forms import selection_context
    from core.models import FormResponse

    at = now() - timedelta(hours=2)
    with db.session_scope() as s:
        p = _patient(s)
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=at - timedelta(hours=1),
                           answers={"C5": "Có"}, source="app"))
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=at + timedelta(hours=1),
                           answers={"C5": "Không"}, source="app"))
        s.flush()
        assert selection_context(s, p, at.date(), rules, load_forms(), as_of=at).flagged_answers == {"C5": "Có"}


def test_same_response_free_text_concern_is_not_cleared_by_conflicting_checkbox(db, rules):
    from core.forms import selection_context
    from core.models import Assessment, FormResponse

    with db.session_scope() as s:
        p = _patient(s)
        response = FormResponse(patient_id=p.id, form_code="F4", submitted_at=now(),
                                answers={"C5": "Không"}, source="app")
        s.add(response)
        s.flush()
        s.add(Assessment(response_id=response.id, level="yellow", rule_level="green", trend_level="green",
                         ai_level="yellow", rules_version=rules.rules_version,
                         findings={"ai": [{"concept": "DYSURIA", "present": True,
                                           "experiencer": "patient", "temporality": "current"}]}))
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert "DYSURIA" in ctx.recent_concepts
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=now(), answers={"C5": "Không"}, source="app"))
        s.flush()
        assert "DYSURIA" not in selection_context(s, p, now().date(), rules, load_forms()).recent_concepts


def test_current_pregnancy_diagnoses_exclude_old_and_future_visits(db, rules):
    from core.forms import selection_context
    from core.models import Appointment
    from core.patients import save_visit_result

    at = now()
    with db.session_scope() as s:
        p = _patient(s)
        for days, code in ((-400, "O30.0"), (-1, "O13"), (1, "O14")):
            a = Appointment(patient_id=p.id, appt_date=at.date() + timedelta(days=days),
                            start_time=time(9), services=["Khám thai"])
            s.add(a)
            s.flush()
            save_visit_result(s, a.id, {"diagnoses": [{"code": code}]}, source="synthetic",
                              reference_version="test", at=at - timedelta(hours=1))
        s.flush()
        ctx = selection_context(s, p, at.date(), rules, load_forms(), as_of=at)
        assert "HTN" in ctx.flags
        assert "TWINS" not in ctx.flags and "PREECLAMPSIA" not in ctx.flags


def test_context_excludes_demo_and_future_measurements(db, rules):
    from core.forms import clinical_context
    from core.models import FormResponse, VitalReading

    at = now()
    with db.session_scope() as s:
        p = _patient(s)
        demo = FormResponse(patient_id=p.id, form_code="M-THA", submitted_at=at, answers={}, source="demo")
        s.add(demo)
        s.flush()
        for measured, response, sys, dia in ((at - timedelta(hours=2), None, 115, 75),
                                             (at - timedelta(hours=1), demo.id, 170, 115),
                                             (at + timedelta(hours=1), None, 175, 120)):
            for kind, value in (("bp_sys", sys), ("bp_dia", dia)):
                s.add(VitalReading(patient_id=p.id, measured_at=measured, kind=kind, value=value,
                                   source_response_id=response))
        s.flush()
        labels = clinical_context(s, p, at.date(), rules, as_of=at)["measurements"]
        assert len(labels) == 1 and "dưới ngưỡng" in labels[0]


def test_measurement_filter_honors_exact_as_of(rules):
    at = now()
    series = {"glu_fast": [(at - timedelta(minutes=1), 4.7), (at + timedelta(minutes=1), 10.0)]}
    assert measurement_labels(series, at.date(), rules, as_of=at) == ["Đường huyết lúc đói: dưới ngưỡng 5.3"]


def test_ogtt_keeps_lis_reference_and_does_not_enter_postmeal_series(db, rules):
    from core.forms import clinical_context
    from core.models import Appointment
    from core.patients import save_visit_result

    with db.session_scope() as s:
        p = _patient(s)
        a = Appointment(patient_id=p.id, appt_date=now().date() - timedelta(days=1),
                        start_time=time(9), services=["NPDN 75 g"])
        s.add(a)
        s.flush()
        save_visit_result(s, a.id, {"labs": [{"code": "OGTT_1", "value": 9.0, "unit": "mmol/L",
                                             "flag": "", "ref": [None, 9.9]}]}, source="synthetic", reference_version="test")
        s.flush()
        labels = clinical_context(s, p, now().date(), rules)["measurements"]
        assert len(labels) == 1 and "NPDN 75 g OGTT_1" in labels[0] and "9.9" in labels[0]
        assert "sau ăn" not in labels[0] and "9.0" not in labels[0] and "cao hơn ngưỡng 7.8" not in labels[0]


@pytest.mark.parametrize("plan,status", [
    ("Tiếp tục aspirin theo chỉ định", "active"),
    ("Đang dùng aspirin", "active"),
    ("Ngừng aspirin từ hôm nay", "stopped"),
    ("Tạm dừng aspirin", "stopped"),
    ("Cân nhắc dùng aspirin nếu cần", "planned"),
    ("Aspirin", "unknown"),
])
def test_medication_mentions_preserve_status(rules, plan, status):
    assert medication_statuses(False, [plan], rules)["ASPIRIN"] == status
    labels = medication_labels(False, [plan], rules)
    assert ("Đang dùng aspirin liều thấp" in labels) is (status == "active")


def test_negation_is_scoped_to_its_condition_or_drug(rules):
    from core.rules.personalize import find_flags

    flags = find_flags({"medical_history": ["Có tăng huyết áp nhưng không đái tháo đường"]}, [], [], rules)
    assert {f.flag for f in flags} == {"HTN"}
    assert medication_labels(False, ["Không dùng aspirin vì đang dùng thuốc hạ áp"], rules) == ["Đang dùng thuốc hạ áp"]
    assert not find_flags({"medical_history": ["Tăng huyết áp trước đây: không"]}, [], [], rules)


def test_postpartum_bank_filters_base_carry_and_ai_candidates(db, rules):
    from core.ai.question_selector import build_payload
    from core.forms import selection_context
    from core.patients import save_history
    from core.rules.question_select import select_questions

    forms = load_forms()
    with db.session_scope() as s:
        p = _patient(s)
        p.delivery_date = now().date() - timedelta(days=3)
        save_history(s, p.id, {"risk_summary": ["TWINS"]}, source="synthetic")
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, forms)
        ctx.flagged_answers = {"C8": "Có", "C9": "Có"}
        ctx.recent_concepts = ["REDUCED_FM", "CONTRACTIONS"]
        assert ctx.ga is None and ctx.postpartum
        codes = {q.code for q in select_questions(["S1"], ctx, rules, forms, {"C8": (1.0, ""), "C9": (1.0, "")})}
        assert not {"C8", "C9", "C3", "NAUSEA"} & codes
        payload = build_payload(["S1"], ctx, rules, forms)
        assert payload["stage"] == "postpartum"
        assert not {"C8", "C9"} & {q["code"] for q in payload["mandatory"] + payload["candidates"]}


def test_duplicate_question_code_cannot_replace_independent_votes():
    outs = [{"scores": [{"code": "C5", "score": 0.9}] * 3, "confidence": 0.9},
            {"scores": [], "confidence": 0.9}, {"scores": [], "confidence": 0.9}]
    scores, unstable, _confidence = combine_runs(outs, {"C5"}, 0.3)
    assert "C5" not in scores and "C5" in unstable


def test_stability_excludes_all_fallback_pairs(rules):
    from scripts.eval_question_selection import run_case, stability_summary

    def fail(*_args):
        raise LlmError("synthetic unavailable")

    case = {"id": "REGRESSION", "ga": [30, 0], "flags": [], "confirmed": [], "form": "F5"}
    result = run_case(case, rules, load_forms(), MockClient(fail))
    assert not result["ai_ok"] and result["ai_stable"] is None
    summary = stability_summary([result])
    assert summary["ai_success_rate"] == 0 and summary["ai_stable_rate"] is None
    assert summary["ai_stability_cases"] == 0


def test_dashboard_missing_required_metrics_is_blocking(db, rules):
    from api.routers.quality import quality
    from core.ai.scorecard import load_targets, scorecard
    from core.models import EvalRun

    with db.session_scope() as s:
        checks = scorecard("forms", {}, load_targets())
        s.add(EvalRun(ts=now(), dataset="forms", model="ollama:synthetic-test", rules_version=rules.rules_version,
                      metrics={"scorecard": [c.to_dict() for c in checks]}))
    result = quality(user=SimpleNamespace(user_id=None))
    assert result["blocking_failed"]


def test_dashboard_without_eval_is_unmeasured_and_blocking(db):
    from api.routers.quality import quality

    result = quality(user=SimpleNamespace(user_id=None))
    assert result["blocking_failed"]
    assert any(c["blocking"] and c["passed"] is None for c in result["scorecard"])


def test_noshow_eval_readiness_is_http_conflict(monkeypatch):
    from fastapi import HTTPException

    from api.routers.quality import run_eval
    from core.ai.noshow import ModelNotReady
    from scripts import run_eval as eval_script

    def unavailable(*_args):
        raise ModelNotReady("Chưa đủ dữ liệu đánh giá mô hình bỏ hẹn")

    monkeypatch.setattr(eval_script, "run", unavailable)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(run_eval("noshow", user=SimpleNamespace(user_id=None)))
    assert caught.value.status_code == 409
    assert "Chưa đủ dữ liệu" in caught.value.detail
