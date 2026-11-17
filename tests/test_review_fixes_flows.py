"""Sequential workflow review; synthetic DB and mock transports only.

Assertions state correct behavior. Application sources are left unchanged.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import _block_outbound_network as _block_outbound_network  # noqa: E402, F401
from conftest import db as db  # noqa: E402, F401
from test_security_audit_review import login  # noqa: E402
from test_security_audit_review import review as review  # noqa: E402, F401


def record(record_property, case, **fields):
    record_property("evidence", json.dumps({"case": case, **fields}, ensure_ascii=False, default=str))


def enrol(review):
    from core.models import PatientProgram

    with review.db.session_scope() as s:
        s.add(PatientProgram(patient_id=review.pid, in_package=True, package_name="Synthetic audit"))


class RecordingSms:
    def __init__(self):
        self.rows = []

    def send_batch(self, template_code, rows):
        from core.channels.base import RowResult, SendResult

        self.rows += rows
        return SendResult("synthetic", template_code, [RowResult(r.patient_code, "sent") for r in rows])


def test_demo_token_must_not_generate_real_patient_reminder(review, record_property):
    from core.config import load_forms, load_rules
    from core.db import now
    from core.forms import send_reminders
    from core.models import FormToken

    enrol(review)
    with review.db.session_scope() as s:
        demo = s.get(FormToken, review.demo)
        demo.created_at = now() - timedelta(hours=49)
        demo.expires_at = now() + timedelta(days=3)
    channel = RecordingSms()
    with review.db.session_scope() as s:
        result = send_reminders(s, load_rules(), load_forms(), channel, None)
    with review.db.session_scope() as s:
        reminded = s.get(FormToken, review.demo).reminded_at
    record(record_property, "Y01", sms_count=len(channel.rows), n_ok=result.n_ok, reminded_at=reminded,
           sent_demo_link=any(review.demo in row.params.get("P1", "") for row in channel.rows))
    assert not channel.rows, "Staff-created demo token generates a reminder to the patient's phone"


def test_withdrawn_patient_must_not_receive_reminder(review, record_property):
    from core import consent
    from core.config import load_forms, load_rules
    from core.db import now
    from core.forms import send_reminders
    from core.models import FormToken

    enrol(review)
    with review.db.session_scope() as s:
        token = s.get(FormToken, review.normal)
        token.created_at = now() - timedelta(hours=49)
        consent.withdraw(s, review.pid, None, "Synthetic withdrawal")
    channel = RecordingSms()
    with review.db.session_scope() as s:
        send_reminders(s, load_rules(), load_forms(), channel, None)
    record(record_property, "CONTROL_WITHDRAWAL", sms_count=len(channel.rows))
    assert not channel.rows


def test_delivery_recorded_during_ai_must_invalidate_prenatal_submission(review, monkeypatch, record_property):
    from core import alerts
    from core.models import FormResponse, FormToken, Patient

    original = alerts.assess_answers

    def deliver_during_assessment(*args, **kwargs):
        result = original(*args, **kwargs)
        with review.db.session_scope() as s:
            s.get(Patient, review.pid).delivery_date = review.today
        return result

    monkeypatch.setattr(alerts, "assess_answers", deliver_during_assessment)
    result = review.client.post(f"/api/public/form/{review.normal}", json={"free_text": "Em thay khoe"})
    with review.db.session_scope() as s:
        consumed = s.get(FormToken, review.normal).used_at is not None
        count = s.scalar(select(func.count(FormResponse.id)))
    record(record_property, "Y02", http_status=result.status_code, token_consumed=consumed, responses=count)
    assert result.status_code == 409 and not consumed and count == 0, "Prenatal link is consumed after delivery invalidates it"


def test_delivery_recorded_before_submit_rejects_prenatal_link(review, record_property):
    from core.models import Patient

    with review.db.session_scope() as s:
        s.get(Patient, review.pid).delivery_date = review.today
    result = review.client.post(f"/api/public/form/{review.normal}", json={"free_text": "Em thay khoe"})
    record(record_property, "CONTROL_DELIVERY_BEFORE", http_status=result.status_code)
    assert result.status_code == 409


def make_his(review, *, note, start="08:00"):
    from core.config import load_his_columns

    fields = {"pid": "AUDIT-A", "full_name": "Synthetic Patient A", "phone": "0900000001", "nationality": "Việt Nam",
              "appt_date": review.today.strftime("%d/%m/%Y"), "start_time": start, "end_time": "09:00",
              "service": "Khám thai", "department": "Phòng khám Sản phụ khoa", "doctor": "", "note": note}
    columns = load_his_columns()
    row = {header: fields.get(field, "") for header, field in columns.columns.items()}
    return pd.DataFrame([row]).to_csv(index=False).encode("utf-8-sig")


def test_reimport_must_not_offer_old_export_as_current_locked_plan(review, record_property):
    login(review)
    endpoint = "/api/appointments/upload"
    first = review.client.post(endpoint, data={"d": review.today.isoformat(), "use_ai": "false"},
                               files={"file": ("synthetic.csv", make_his(review, note=""), "text/csv")})
    assert first.status_code == 200 and first.json()["ok"], first.text
    exported = review.client.post("/api/appointments/export", params={"d": review.today.isoformat()})
    assert exported.status_code == 200, exported.text
    before = review.client.get("/api/appointments", params={"d": review.today.isoformat()}).json()
    assert len(before["items"]) == 1 and before["exports"]
    original_name = next(f["name"] for f in exported.json()["files"] if f["rows"] > 0)
    old_bytes = review.client.get(f"/api/appointments/export/{original_name}").content
    imported = review.client.post(endpoint, data={"d": review.today.isoformat(), "use_ai": "false"},
                                  files={"file": ("synthetic.csv", make_his(review, note="Thai 38 tuần", start="08:30"), "text/csv")})
    assert imported.status_code == 200 and imported.json()["ok"], imported.text
    current = review.client.get("/api/appointments", params={"d": review.today.isoformat()}).json()
    assert len(current["items"]) == 1 and current["counts"]["need"] == 1, current
    fresh_export = review.client.post("/api/appointments/export", params={"d": review.today.isoformat()})
    assert fresh_export.status_code == 409
    download = review.client.get(f"/api/appointments/export/{original_name}")
    record(record_property, "Y03", current_start=current["items"][0]["start_time"], need=current["counts"]["need"],
           fresh_export_http=fresh_export.status_code, offered_old_file=original_name in current["exports"],
           old_download_http=download.status_code, identical_old_bytes=download.content == old_bytes)
    assert original_name not in current["exports"], "Current locked plan still offers the preceding export"
    assert download.status_code == 409, "Stale export of a locked plan is still downloadable"


def seed_alert(review):
    from core.db import now
    from core.models import Alert, Assessment, FormResponse

    with review.db.session_scope() as s:
        response = FormResponse(patient_id=review.pid, form_code="F6", source="app", submitted_at=now(), answers={})
        s.add(response)
        s.flush()
        assessment = Assessment(response_id=response.id, level="red", rule_level="red", trend_level="green",
                                ai_level="green", rules_version="audit", findings={}, raw={})
        s.add(assessment)
        s.flush()
        alert = Alert(assessment_id=assessment.id, level="red", status="called", note="ORIGINAL", created_at=now())
        s.add(alert)
        s.flush()
        return alert.id


def test_concurrent_note_edits_in_same_status_must_not_silently_overwrite(review, record_property):
    from core import db as dbmod
    from core.alerts import StaleAlert, handle_alert
    from core.models import Alert

    aid = seed_alert(review)
    first, second = dbmod._Session(), dbmod._Session()
    try:
        stale = second.get(Alert, aid)
        assert stale.note == "ORIGINAL"
        handle_alert(first, aid, None, status="called", note="FIRST_CLINICAL_NOTE", final_level=None)
        first.commit()
        conflict = False
        try:
            handle_alert(second, aid, None, status="called", note="STALE_SECOND_CLINICAL_NOTE", final_level=None)
            second.commit()
        except StaleAlert:
            conflict = True
            second.rollback()
        with review.db.session_scope() as s:
            final_note = s.get(Alert, aid).note
        record(record_property, "Y04", conflict=conflict, stored_note=final_note, status="called")
        assert conflict and final_note == "FIRST_CLINICAL_NOTE", "Status-only guard misses concurrent edits that leave status unchanged"
    finally:
        first.close()
        second.close()


def test_failing_mandatory_quality_checks_must_block_rule_version_activation(review, record_property):
    from core.config import load_rules
    from core.db import now
    from core.models import EvalRun

    with review.db.session_scope() as s:
        s.add(EvalRun(ts=now(), dataset="forms", model="synthetic-evaluation-record", prompt_version="audit",
                      rules_version=load_rules().rules_version,
                      metrics={"rules_plus_ai": {"red_sensitivity": 0.8, "under_triage": 2},
                               "redteam": {"red_sensitivity": 0.8, "under_triage": 1}}))
    login(review, "doctor")
    quality = review.client.get("/api/quality")
    assert quality.status_code == 200 and quality.json()["blocking_failed"] is True
    failed = [c["label"] for c in quality.json()["scorecard"] if c["blocking"] and c["passed"] is False]
    assert failed
    login(review, "nurse")
    proposed = review.client.post("/api/config/propose", json={"key": "thresholds.epds_threshold", "value": "14", "reason": "Synthetic audit"})
    assert proposed.status_code == 200 and proposed.json()["status"] == "pending", proposed.text
    login(review, "doctor")
    changes = review.client.get("/api/config").json()["pending"]
    assert len(changes) == 1
    old = load_rules().rules_version
    approved = review.client.post(f"/api/config/changes/{changes[0]['id']}/approve")
    new = load_rules().rules_version
    record(record_property, "Y05", quality_blocking_failed=True, measured_failed_checks=failed, approval_http=approved.status_code,
           previous_version=old, active_version=new, active_threshold=load_rules()["thresholds"]["epds_threshold"])
    assert approved.status_code == 409 and old == new, "The active rule version changes while mandatory evaluation is failing/unmeasured"


def test_complete_patient_to_doctor_workflow_without_external_ai(review, record_property):
    from core.forms import create_token
    from core.models import Alert, FormToken

    login(review)
    created = review.client.post("/api/patients", json={"pid": "AUDITFLOWC", "full_name": "Synthetic Flow Patient",
                                                       "phone": "0900000003", "edd": (review.today + timedelta(days=70)).isoformat(),
                                                       "in_package": True, "package_name": "Synthetic package"})
    assert created.status_code == 201, created.text
    pid, code = created.json()["patient_id"], created.json()["code"]
    assert review.client.post(f"/api/profile/{pid}/consent/grant", json={"note": "Synthetic consent"}).status_code == 200
    plan = review.client.get(f"/api/patients/{pid}/form-plan")
    assert plan.status_code == 200
    schedule = review.client.get("/api/form-schedule")
    assert schedule.status_code == 200 and any(d["code"] == code for d in schedule.json()["send"])
    exported = review.client.post("/api/form-schedule/export")
    assert exported.status_code == 200 and exported.json()["ok"] >= 1, exported.text
    assert exported.json()["file"]
    downloaded = review.client.get(f"/api/form-schedule/file/{exported.json()['file']}")
    assert downloaded.status_code == 200 and len(downloaded.content) > 100
    with review.db.session_scope() as s:
        token = s.scalar(select(FormToken.token).where(FormToken.patient_id == pid))
    assert token
    form = review.client.get(f"/api/public/form/{token}")
    assert form.status_code == 200 and "C1" in {q["key"] for q in form.json()["questions"]}
    submitted = review.client.post(f"/api/public/form/{token}", json={"answers": {"C1": "Có"}})
    assert submitted.status_code == 200 and submitted.json()["level"] == "red", submitted.text
    with review.db.session_scope() as s:
        aid = s.scalar(select(Alert.id))
    assert aid is not None
    assert review.client.get(f"/api/alerts/{aid}").status_code == 200
    assert review.client.post(f"/api/alerts/{aid}/reveal-phone").status_code == 200
    handled = review.client.post(f"/api/alerts/{aid}/handle", json={"close": True, "final_level": "keep_red", "note": "Synthetic nurse call"})
    assert handled.status_code == 200 and handled.json()["status"] == "awaiting_doctor", handled.text
    login(review, "doctor")
    decided = review.client.post(f"/api/alerts/{aid}/handle", json={"close": True, "final_level": "keep_red", "decision": "monitor", "note": "Synthetic doctor decision"})
    assert decided.status_code == 200 and decided.json()["status"] == "closed", decided.text
    profile = review.client.get("/api/profile", params={"q": code})
    assert profile.status_code == 200 and len(profile.json()["responses"]) == 1
    printed = review.client.get(f"/api/profile/{pid}/print")
    assert printed.status_code == 200 and len(printed.content) > 100
    with review.db.session_scope() as s:
        unused = create_token(s, pid, "F5", 7)
    withdrawn = review.client.post(f"/api/profile/{pid}/consent/withdraw", json={"note": "Synthetic withdrawal"})
    assert withdrawn.status_code == 200
    assert review.client.get(f"/api/public/form/{unused}").status_code == 404
    record(record_property, "CONTROL_COMPLETE_FLOW", exported=exported.json()["ok"], red_received=True,
           nurse_result=handled.json()["status"], doctor_result=decided.json()["status"],
           profile_responses=len(profile.json()["responses"]), print_bytes=len(printed.content), withdrawal_blocks_link=True)


def test_program_override_permissions_and_proposal_confirmation(review, record_property):
    from core.db import now
    from core.models import Appointment, NoteExtraction, Patient
    from core.security import encrypt

    enrol(review)
    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        p.proposed_risk_flags = ["GDM"]
        a = s.scalar(select(Appointment).where(Appointment.patient_id == p.id))
        a.note_enc = encrypt("ĐTĐ thai kỳ, theo dõi đường huyết")
        s.add(NoteExtraction(appointment_id=a.id, risk_flags=["GDM"], confidence=1.0, model="synthetic-audit",
                             rules_version="audit", masked_note="ĐTĐ thai kỳ, theo dõi đường huyết"))
    login(review)
    props = review.client.get("/api/form-schedule").json()["proposals"]
    proposal = next(p for p in props if p["code"] == review.code)
    codes = [i["code"] for i in proposal["items"]]
    assert "M-DTD" in codes and all(i["flags"] for i in proposal["items"])
    confirmed = review.client.post(f"/api/form-schedule/proposals/{review.pid}/confirm", json={"codes": ["M-DTD"]})
    assert confirmed.status_code == 200, confirmed.text
    again = review.client.post(f"/api/form-schedule/proposals/{review.pid}/confirm", json={"codes": ["M-DTD"]})
    assert again.status_code == 409
    body = {"in_package": False, "override_monitoring": True, "override_reason": "Synthetic doctor indication"}
    nurse = review.client.put(f"/api/patients/{review.pid}/program", json=body)
    assert nurse.status_code == 403
    login(review, "doctor")
    doctor = review.client.put(f"/api/patients/{review.pid}/program", json=body)
    assert doctor.status_code == 200 and doctor.json()["override_monitoring"] is True
    record(record_property, "CONTROL_PROPOSALS_OVERRIDE", confirmed=confirmed.status_code,
           stale_confirm=again.status_code, nurse_override=nurse.status_code, doctor_override=doctor.status_code,
           checked_at=now())
