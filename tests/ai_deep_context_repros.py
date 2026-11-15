"""Opt-in second-pass AI data/context review; only synthetic and temporary data.

Run explicitly: pytest tests/ai_deep_context_repros.py -ra --tb=short
Assertions describe desired behavior; failures reproduce current open findings.
"""

from __future__ import annotations

from datetime import time, timedelta
from types import SimpleNamespace

import pytest

from core.config import load_forms
from core.db import now


def _patient(s):
    from core.repo import upsert_patient

    p = upsert_patient(s, "DEEP-CONTEXT-ONLY", "Người Giả Lập", "0900000000", "Việt Nam")
    p.edd = now().date() + timedelta(days=70)
    p.consent_status = "granted"
    return p


def test_dc01_ogtt_must_not_be_relabelled_as_postmeal_monitoring(db, rules):
    from core.forms import clinical_context
    from core.models import Appointment
    from core.patients import save_visit_result

    with db.session_scope() as s:
        p = _patient(s)
        a = Appointment(patient_id=p.id, appt_date=now().date() - timedelta(days=1),
                        start_time=time(9), services=["Nghiệm pháp dung nạp glucose 75 g"])
        s.add(a)
        s.flush()
        save_visit_result(s, a.id, {"labs": [{"code": "OGTT_1", "value": 9.0, "unit": "mmol/L",
                                             "flag": "", "ref": [None, 9.9]}]},
                          source="synthetic", reference_version="synthetic-audit")
        s.flush()
        ctx = clinical_context(s, p, now().date(), rules)
        assert not any("sau ăn" in text for text in ctx["measurements"]), ctx


@pytest.mark.parametrize("consumer", ["history", "clinical_context"])
def test_dc02_future_measurements_must_not_enter_current_context(db, rules, consumer):
    from core.alerts import history_for
    from core.forms import clinical_context
    from core.models import VitalReading

    with db.session_scope() as s:
        p = _patient(s)
        future = now() + timedelta(days=1)
        for kind, value in (("bp_sys", 170), ("bp_dia", 115)):
            s.add(VitalReading(patient_id=p.id, measured_at=future, kind=kind, value=value))
        s.flush()
        out = (history_for(s, p.id) if consumer == "history"
               else clinical_context(s, p, now().date(), rules)["measurements"])
        assert not out, {"consumer": consumer, "future_data_received": out}


def test_dc03_previous_pregnancy_diagnosis_must_not_be_current_twins_flag(db, rules):
    from core.forms import selection_context
    from core.models import Appointment
    from core.patients import save_visit_result

    with db.session_scope() as s:
        p = _patient(s)
        old = Appointment(patient_id=p.id, appt_date=now().date() - timedelta(days=400),
                          start_time=time(9), services=["Khám thai cũ"])
        s.add(old)
        s.flush()
        save_visit_result(s, old.id, {"diagnoses": [{"code": "O30.0", "name": "Song thai"}]},
                          source="synthetic", reference_version="synthetic-audit")
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert "TWINS" not in ctx.flags, {"current_ga": ctx.ga, "flags": ctx.flags}


@pytest.mark.parametrize("plan", ["Ngừng aspirin từ hôm nay", "Cân nhắc aspirin nếu cần"])
def test_dc04_stopped_or_conditional_drug_must_not_be_current_medication(rules, plan):
    from core.rules.ai_context import medication_labels

    labels = medication_labels(False, [plan], rules)
    assert "Đang dùng aspirin liều thấp" not in labels, {"plan": plan, "labels": labels}


def test_dc05_missing_required_metric_must_raise_dashboard_blocking_flag(db, rules):
    from api.routers.quality import quality
    from core.ai.scorecard import blocking_failures, load_targets, scorecard
    from core.models import EvalRun

    checks = scorecard("forms", {}, load_targets())
    assert blocking_failures(checks)  # The shared gate already treats unmeasured as blocked.
    with db.session_scope() as s:
        s.add(EvalRun(ts=now(), dataset="forms", model="ollama:synthetic-audit",
                      rules_version=rules.rules_version,
                      metrics={"scorecard": [c.to_dict() for c in checks]}))
    result = quality(user=SimpleNamespace(user_id=None))
    assert result["blocking_failed"] is True, {
        "blocking_failed": result["blocking_failed"],
        "required_metric_states": [c["passed"] for c in result["scorecard"] if c["blocking"]],
    }


def test_dc06_unrelated_real_module_must_not_erase_unresolved_symptom_answer(db, rules):
    from core.forms import selection_context
    from core.models import FormResponse

    with db.session_scope() as s:
        p = _patient(s)
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=now() - timedelta(days=1),
                           answers={"C5": "Có"}, source="app"))
        s.add(FormResponse(patient_id=p.id, form_code="M-DTD", submitted_at=now(),
                           answers={"GLU_FAST": 4.7}, source="app"))
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert ctx.flagged_answers.get("C5") == "Có", {"flagged_answers": ctx.flagged_answers}


def test_control_active_drug_is_retained(rules):
    from core.rules.ai_context import medication_labels

    assert medication_labels(False, ["Tiếp tục aspirin theo chỉ định"], rules) == ["Đang dùng aspirin liều thấp"]


def test_control_current_measurements_are_retained(rules):
    from core.rules.ai_context import measurement_labels

    readings = {"bp_sys": [(now(), 170)], "bp_dia": [(now(), 115)]}
    assert "rất cao" in measurement_labels(readings, now().date(), rules)[0]


def test_control_explicitly_resolved_answer_is_not_carried(db, rules):
    from core.forms import selection_context
    from core.models import FormResponse

    with db.session_scope() as s:
        p = _patient(s)
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=now() - timedelta(days=1),
                           answers={"C5": "Có"}, source="app"))
        s.add(FormResponse(patient_id=p.id, form_code="F4", submitted_at=now(),
                           answers={"C5": "Không"}, source="app"))
        s.flush()
        ctx = selection_context(s, p, now().date(), rules, load_forms())
        assert "C5" not in ctx.flagged_answers
