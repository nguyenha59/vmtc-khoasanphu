"""Independent checks against the frozen source. No application modifications."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from test_security_audit_review import login
from test_security_audit_review import review as review  # noqa: F401


@pytest.fixture()
def seen(request, record_property):
    def emit(**values):
        record_property("observed", json.dumps(values, ensure_ascii=False, default=str))
    return emit


def add_alert(review, *, level="red", status="open", pending=False):
    from core.db import now
    from core.models import Alert, AlertReview, Assessment, FormResponse

    with review.db.session_scope() as s:
        response = FormResponse(patient_id=review.pid, form_code="F6", submitted_at=now(), answers={}, source="app")
        s.add(response)
        s.flush()
        assessment = Assessment(response_id=response.id, level=level, rule_level=level, trend_level="green",
                                ai_level="green", summary="Synthetic review", findings={}, rules_version="audit", created_at=now())
        s.add(assessment)
        s.flush()
        alert = Alert(assessment_id=assessment.id, level=level, status=status,
                      created_at=now() - timedelta(minutes=10))
        s.add(alert)
        s.flush()
        if pending:
            s.add(AlertReview(alert_id=alert.id, kind="doctor_decision", status="pending", created_at=now()))
        return alert.id


def add_schedule(review, code="F5"):
    from core.models import FormSchedule

    with review.db.session_scope() as s:
        row = FormSchedule(patient_id=review.pid, send_date=review.today, form_code=code,
                           status="planned", rules_version="audit")
        s.add(row)
        s.flush()
        return row.id




def enrol(review):
    from core.db import now
    from core.models import PatientProgram

    with review.db.session_scope() as s:
        row = s.get(PatientProgram, review.pid)
        if row is None:
            row = PatientProgram(patient_id=review.pid, updated_at=now())
            s.add(row)
        row.in_package, row.package_name = True, "Synthetic review package"


def test_eligible_failed_sms_rows_must_not_remain_exported(review, seen):
    from core.channels.base import RowResult, SendResult
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms
    from core.models import FormSchedule

    enrol(review)
    sid = add_schedule(review)
    attempted = []

    class Channel:
        def send_batch(self, template_code, rows):
            attempted.extend(rows)
            return SendResult("synthetic-failure", template_code, [RowResult(r.patient_code, "error", "offline") for r in rows])

    with review.db.session_scope() as s:
        result = export_form_sms(s, review.today, load_rules(), load_forms(), Channel(), None, precomputed={})
    assert len(attempted) == 1 and result.n_ok == 0
    with review.db.session_scope() as s:
        schedule = s.get(FormSchedule, sid)
        seen(attempted=len(attempted), channel_statuses=[r.status for r in result.rows], schedule_status=schedule.status)
        assert schedule.status != "exported"


def test_red_claim_must_recover_after_process_stops_before_delivery(review, monkeypatch, seen):
    from core import jobs
    from core.db import now
    from core.models import Alert

    class SimulatedProcessStop(BaseException):
        pass

    class InterruptedChannel:
        def send(self, payload):
            raise SimulatedProcessStop("Synthetic process termination before delivery")

    aid = add_alert(review)
    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: InterruptedChannel())
    with pytest.raises(SimulatedProcessStop):
        jobs.retry_unsent_red(now())
    sent = []

    class RecoveredChannel:
        def send(self, payload):
            sent.append(payload.alert_id)

    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: RecoveredChannel())
    recovered = jobs.retry_unsent_red(now() + timedelta(hours=1))
    with review.db.session_scope() as s:
        marked_sent = s.get(Alert, aid).sent_at
    seen(delivered=sent, recovered_count=recovered, database_sent_at=marked_sent)
    assert aid in sent


def test_eligible_concurrent_exports_send_exactly_one_link(review, monkeypatch, seen):
    from core import forms
    from core.channels.base import RowResult, SendResult
    from core.config import load_forms, load_rules
    from core.models import FormToken

    enrol(review)
    add_schedule(review)
    barrier = threading.Barrier(2)
    original = forms.create_token
    sent = []

    def synchronized(*args, **kwargs):
        barrier.wait(timeout=8)
        return original(*args, **kwargs)

    class Channel:
        def send_batch(self, template_code, rows):
            sent.extend(rows)
            return SendResult("synthetic", template_code, [RowResult(r.patient_code, "sent") for r in rows])

    monkeypatch.setattr(forms, "create_token", synchronized)

    def export():
        with review.db.session_scope() as s:
            return forms.export_form_sms(s, review.today, load_rules(), load_forms(), Channel(), None, precomputed={}).n_ok

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(export) for _ in range(2)]
        counts = [f.result(timeout=15) for f in futures]
    with review.db.session_scope() as s:
        token_count = s.scalar(select(func.count(FormToken.token)))
    seen(deliveries=len(sent), worker_counts=counts, token_count=token_count)
    assert len(sent) == 1 and sum(counts) == 1 and token_count == 3


def test_one_link_per_day_must_hold_after_module_added(seen):
    from core.config import load_rules
    from core.rules.form_scheduler import PatientState, plan_for_patient

    day = date(2026, 11, 12)
    state = PatientState(patient_id=1, code="SYNTHETIC", consent="granted", edd=day + timedelta(days=70),
                         modules=["M-THA"], last_visit=day - timedelta(days=10), next_appt=day + timedelta(days=20),
                         sent=[("F5", day)])
    decisions = plan_for_patient(state, day, load_rules())
    seen(already_sent="F5", decisions=[{"code": d.form_code, "send": d.send} for d in decisions])
    assert not any(d.send for d in decisions)


def test_insulin_schedule_in_profile_must_match_scheduler(review, seen):
    from core.config import load_forms, load_rules
    from core.models import Patient
    from core.patients import form_plan_for
    from core.rules.form_scheduler import schedule_text

    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        p.risk_groups, p.on_insulin = ["M-DTD"], True
        plan = form_plan_for(s, p, review.today, load_rules(), load_forms())
        shown = next(i.schedule for i in plan.items if i.code == "M-DTD")
    actual = schedule_text("M-DTD", load_rules()["forms"]["modules"]["M-DTD"], on_insulin=True)
    seen(profile_schedule=shown, scheduler_schedule=actual)
    assert shown == actual


def test_old_rule_proposal_must_not_silently_overwrite_newer_approval(review, seen):
    from core.config import load_rules
    from core.models import RuleChange

    login(review)
    key = "thresholds.epds_threshold"
    before = load_rules().get(key)
    for value in (before + 1, before + 2):
        r = review.client.post("/api/config/propose", json={"key": key, "value": str(value), "reason": "Synthetic stale approval"})
        assert r.status_code == 200
    with review.db.session_scope() as s:
        ids = list(s.scalars(select(RuleChange.id).where(RuleChange.key == key).order_by(RuleChange.id)))
    login(review, "doctor")
    first = review.client.post(f"/api/config/changes/{ids[1]}/approve")
    assert first.status_code == 200
    newer_value = load_rules().get(key)
    stale = review.client.post(f"/api/config/changes/{ids[0]}/approve")
    final = load_rules().get(key)
    seen(initial=before, after_newer_approval=newer_value, stale_http=stale.status_code, final=final)
    assert stale.status_code in (409, 422) and final == newer_value
