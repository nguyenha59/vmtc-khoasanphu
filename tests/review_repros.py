"""Opt-in reproductions for the detailed code review (2026-11-14).

Run explicitly: pytest tests/review_repros.py -ra --tb=short
This filename is intentionally outside normal pytest discovery. Assertions state
the expected correct behavior; failures are the review findings, not XFAILs.
All database/API tests use the existing isolated synthetic review fixture.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from test_security_audit_review import login
from test_security_audit_review import review as review


def test_postpartum_snapshot_must_not_add_fetal_movement_questions(review):
    from core.config import load_forms, load_rules
    from core.forms import create_token, snapshot_selection
    from core.models import Patient
    from core.patients import save_history

    with review.db.session_scope() as s:
        patient = s.get(Patient, review.pid)
        patient.delivery_date = review.today - timedelta(days=3)
        patient.edd = review.today + timedelta(days=4)
        save_history(s, patient.id, {"risk_summary": ["TWINS"]}, source="synthetic-review")
        token = create_token(s, patient.id, "S1", 3)
        s.flush()
        snapshot_selection(s, patient, token, ["S1"], load_rules(), load_forms(), client=None)
    response = review.client.get(f"/api/public/form/{token}")
    assert response.status_code == 200
    keys = {q["key"] for q in response.json()["questions"]}
    assert not {"C8", "C9"} & keys, f"postpartum S1 contains pregnancy questions: {sorted(keys)}"


def test_demo_answers_must_not_block_real_routine_schedule(review):
    from core.config import load_rules
    from core.db import now
    from core.forms import build_states, create_token
    from core.models import Appointment, FormResponse, Patient, PatientProgram
    from core.rules.form_scheduler import plan_forms

    with review.db.session_scope() as s:
        patient = s.get(Patient, review.pid)
        patient.edd = review.today + timedelta(days=70)  # F5, 30 weeks
        for appointment in s.query(Appointment).filter_by(patient_id=patient.id):
            appointment.appt_date = review.today - timedelta(days=10)
        s.add(PatientProgram(patient_id=patient.id, in_package=True, package_name="Synthetic package",
                             package_since=review.today, updated_at=now()))
        s.flush()
        before = plan_forms(build_states(s, review.today, load_rules()), review.today, load_rules())
        assert any(d.send and d.form_code == "F5" and d.patient_id == review.pid for d in before)
        token = create_token(s, patient.id, "F5", 3, demo=True)
        s.add(FormResponse(patient_id=patient.id, token=token, form_code="F5", source="demo",
                           submitted_at=now(), answers={"C1": "Không"}))
    with review.db.session_scope() as s:
        decisions = plan_forms(build_states(s, review.today, load_rules()), review.today, load_rules())
    own = [d for d in decisions if d.patient_id == review.pid and d.form_code == "F5"]
    assert own and own[0].send, [d.skip_reason for d in own]


def test_export_must_not_hold_write_lock_while_waiting_for_question_ai(review, monkeypatch):
    from core import audit
    from core.ai.llm_client import MockClient
    from core.channels.sms_excel import ExcelSmsChannel
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms
    from core.models import FormSchedule

    in_ai, release, writer_started, writer_done = (threading.Event() for _ in range(4))

    def slow(prompt, schema, seed):
        in_ai.set()
        assert release.wait(8), "reproduction synchronization timed out"
        return {"scores": [], "confidence": 0.9}

    monkeypatch.setattr("core.ai.question_selector.selector_client", lambda rules: MockClient(slow))
    with review.db.session_scope() as s:
        s.add(FormSchedule(patient_id=review.pid, send_date=review.today, form_code="F5",
                           status="planned", rules_version="review"))

    def export():
        with review.db.session_scope() as s:
            export_form_sms(s, review.today, load_rules(), load_forms(),
                            ExcelSmsChannel(review.tmp_path / "exports"), None)

    def write():
        writer_started.set()
        audit.log("synthetic_concurrent_write")
        writer_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        export_future = pool.submit(export)
        try:
            assert in_ai.wait(8), "AI was not reached"
            write_future = pool.submit(write)
            assert writer_started.wait(3)
            completed_during_ai = writer_done.wait(1)
        finally:
            release.set()
        export_future.result(timeout=12)
        write_future.result(timeout=12)
    assert completed_during_ai, "unrelated audit write blocked until the question-selection AI returned"


def test_one_sms_per_day_must_survive_new_module_confirmation():
    from core.config import load_rules
    from core.rules.form_scheduler import PatientState, plan_for_patient

    today = date(2026, 11, 12)  # Thursday, an M-THA sending day
    state = PatientState(patient_id=1, code="SYNTHETIC", consent="granted",
                         edd=today + timedelta(days=70), modules=["M-THA"],
                         last_visit=today - timedelta(days=10), next_appt=today + timedelta(days=20),
                         sent=[("F5", today)])
    decisions = plan_for_patient(state, today, load_rules())
    assert not any(d.send for d in decisions), [(d.form_code, d.send) for d in decisions]


def test_explicit_pack_removal_must_not_be_silently_reversed_on_read(review):
    from core.models import Patient
    from core.patients import save_history

    with review.db.session_scope() as s:
        patient = s.get(Patient, review.pid)
        patient.edd = review.today + timedelta(days=70)
        save_history(s, patient.id, {"risk_summary": ["TWINS"]}, source="synthetic-review")
    login(review)
    path = f"/api/patients/{review.pid}/form-plan"
    assert "P-SINHNON" in review.client.get(path).json()["confirmed"]
    removed = review.client.put(path, json={"codes": [], "insulin": False})
    assert removed.status_code == 200 and removed.json()["confirmed"] == []
    assert review.client.get(path).json()["confirmed"] == []


def test_duplicate_ai_items_must_not_count_as_agreement_between_runs():
    from core.ai.question_selector import combine_runs

    outs = [{"scores": [{"code": "C8", "score": 0.9}] * 3, "confidence": 0.9},
            {"scores": [], "confidence": 0.9}, {"scores": [], "confidence": 0.9}]
    scores, _, _ = combine_runs(outs, {"C8"}, 0.3)
    assert "C8" not in scores, "one run's repeated item is accepted as three-run agreement"


def test_failed_sms_delivery_must_not_permanently_mark_schedule_exported(review, monkeypatch):
    from core.channels.base import RowResult, SendResult
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms
    from core.models import FormSchedule

    class FailedChannel:
        def send_batch(self, template_code, rows):
            return SendResult(channel="synthetic-failure", template=template_code,
                              rows=[RowResult(r.patient_code, "error", "simulated failure") for r in rows])

    monkeypatch.setattr("core.ai.question_selector.selector_client", lambda rules: None)
    with review.db.session_scope() as s:
        schedule = FormSchedule(patient_id=review.pid, send_date=review.today, form_code="F5",
                                status="planned", rules_version="review")
        s.add(schedule)
        s.flush()
        schedule_id = schedule.id
    with review.db.session_scope() as s:
        result = export_form_sms(s, review.today, load_rules(), load_forms(), FailedChannel(), None)
        assert result.n_ok == 0
    with review.db.session_scope() as s:
        assert s.get(FormSchedule, schedule_id).status != "exported"


def test_insulin_schedule_display_must_match_actual_scheduler(review):
    from core.config import load_forms, load_rules
    from core.models import Patient
    from core.patients import form_plan_for
    from core.rules.form_scheduler import schedule_text

    with review.db.session_scope() as s:
        patient = s.get(Patient, review.pid)
        patient.risk_groups, patient.on_insulin = ["M-DTD"], True
        plan = form_plan_for(s, patient, review.today, load_rules(), load_forms())
        displayed = next(i.schedule for i in plan.items if i.code == "M-DTD")
    expected = schedule_text("M-DTD", load_rules()["forms"]["modules"]["M-DTD"], on_insulin=True)
    assert displayed == expected


def test_rules_fallback_accepts_red_form_when_ai_configuration_is_invalid(review, monkeypatch):
    """Control: recent make_client_safe change should keep the form path working."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "https://example.invalid")
    response = review.client.post(f"/api/public/form/{review.normal}", json={"answers": {"C1": "Có"}})
    assert response.status_code == 200
    assert response.json()["level"] == "red"
