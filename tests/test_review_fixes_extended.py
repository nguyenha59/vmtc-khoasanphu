"""Read-only source audit. All writes use fixture-owned databases / temporary paths.

Assertions specify expected correct behavior; failures are review evidence.
No real API key, model, production database, or outbound transport is used.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import _block_outbound_network as _block_outbound_network  # noqa: E402, F401
from conftest import db as db  # noqa: E402, F401
from test_security_audit_review import login  # noqa: E402
from test_security_audit_review import review as review  # noqa: E402, F401


def evidence(record_property, case, **data):
    entry = {"case": case, **data}
    encoded = json.dumps(entry, ensure_ascii=False, default=str)
    record_property("evidence", encoded)


def add_program(s, patient_id):
    from core.models import PatientProgram

    s.add(PatientProgram(patient_id=patient_id, in_package=True))


def test_known_no_show_must_not_suppress_followup(review, record_property):
    from core.config import load_rules
    from core.forms import build_states
    from core.models import Patient
    from core.rules.form_scheduler import plan_for_patient

    rules = load_rules()
    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        p.edd = review.today + timedelta(days=70)  # GA 30+0, F5 eligible.
        add_program(s, p.id)
        s.flush()
        state = next(st for st in build_states(s, review.today, rules) if st.patient_id == p.id)
        decisions = plan_for_patient(state, review.today, rules)
        assert any(d.form_code == "F5" for d in decisions), decisions
    login(review)
    response = review.client.get("/api/profile", params={"q": review.code})
    assert response.status_code == 200
    profile = response.json()
    assert profile["visits"][0]["attend"] == "no_show"  # Positive attendance evidence.
    evidence(record_property, "X01", last_visit=state.last_visit, attendance=profile["visits"][0]["attend"],
             decisions=[{"form": d.form_code, "send": d.send, "skip": d.skip_reason} for d in decisions],
             profile_last_visit=profile["last_visit"])
    assert any(d.form_code == "F5" and d.send for d in decisions), "Known missed appointment blocks F5 as a completed visit"


def test_real_attendance_still_suppresses_recent_followup(review, record_property):
    from core.config import load_rules
    from core.forms import build_states
    from core.models import Arrival, Patient
    from core.rules.form_scheduler import plan_for_patient

    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        p.edd = review.today + timedelta(days=70)
        add_program(s, p.id)
        s.add(Arrival(pid=p.pid, visit_date=review.today - timedelta(days=1)))
        s.flush()
        rules = load_rules()
        state = next(st for st in build_states(s, review.today, rules) if st.patient_id == p.id)
        decisions = plan_for_patient(state, review.today, rules)
        f5 = next(d for d in decisions if d.form_code == "F5")
        evidence(record_property, "CONTROL_ATTENDED", last_visit=state.last_visit, send=f5.send, skip=f5.skip_reason)
        assert not f5.send


def test_new_pregnancy_must_not_remain_postpartum(review, record_property):
    from test_m2_appointments import row

    from core.config import load_rules
    from core.forms import build_states
    from core.ingest.validators import ValidationReport
    from core.models import Patient
    from core.pipeline import ProcessResult, plan_appointment
    from core.repo import save_his_result, stage_text
    from core.rules.form_scheduler import plan_for_patient
    from core.rules.merge import merge_appointments

    rules = load_rules()
    incoming = row(2, pid="AUDIT-A", note="Thai 12 tuần", d=review.today)
    merged = merge_appointments([incoming], 180, rules["arrival"]["fasting_keywords"])[0]
    plan = plan_appointment(merged, rules, client=None, doctors=[], ner=False)
    assert plan.decision.ga_weeks == 12
    imported = ProcessResult(report=ValidationReport(), rows=[incoming], planned=[plan], rules_version=rules.rules_version)
    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        p.delivery_date = review.today - timedelta(days=400)
        p.edd = p.delivery_date + timedelta(days=3)
        add_program(s, p.id)
        s.flush()
        save_his_result(s, imported, "synthetic-new-pregnancy.xlsx", review.today, None)
        s.flush()
        assert p.edd == review.today + timedelta(days=196), "Ensure new EDD was accepted"
        state = next(st for st in build_states(s, review.today, load_rules()) if st.patient_id == p.id)
        decisions = plan_for_patient(state, review.today, load_rules())
        label = stage_text(p, review.today)
        evidence(record_property, "X02", edd=p.edd, delivery_date=p.delivery_date, stage=label,
                 decisions=[d.form_code for d in decisions], imported_ga=plan.decision.ga_weeks)
        assert label == "12+0", "Accepted new pregnancy is still classified postpartum"


def add_assessment(s, pid, *, at, source, level, summary, token=None):
    from core.models import Assessment, FormResponse

    response = FormResponse(patient_id=pid, form_code="F6", submitted_at=at, source=source,
                            answers={}, token=token)
    s.add(response)
    s.flush()
    s.add(Assessment(response_id=response.id, level=level, rule_level=level, trend_level="green",
                     ai_level="green", summary=summary, rules_version="audit", findings={}, raw={}, created_at=at))


def test_demo_response_must_not_become_clinical_summary(review, monkeypatch, record_property):
    from api.routers import profile as profile_api
    from core.db import now
    from core.models import FormToken

    at = now()
    with review.db.session_scope() as s:
        add_assessment(s, review.pid, at=at - timedelta(hours=1), source="app", level="green",
                       summary="REAL_RESPONSE_GREEN", token=review.normal)
        add_assessment(s, review.pid, at=at, source="demo", level="red",
                       summary="DEMO_RESPONSE_RED_NOT_CLINICAL", token=review.demo)
        s.get(FormToken, review.normal).used_at = at - timedelta(hours=1)
        s.get(FormToken, review.demo).used_at = at
    login(review)
    result = review.client.get("/api/profile", params={"q": review.code})
    assert result.status_code == 200
    profile = result.json()
    monkeypatch.setattr(profile_api, "profile_pdf", lambda *a, **kw: None)
    printed = review.client.get(f"/api/profile/{review.pid}/print")
    assert printed.status_code == 200 and "text/html" in printed.headers["content-type"]
    evidence(record_property, "X03", summary=profile["summary_lines"], metrics=profile["metrics"],
             responses=profile["responses"], demo_in_print="DEMO_RESPONSE_RED_NOT_CLINICAL" in printed.text)
    assert not any("DEMO_RESPONSE" in line for line in profile["summary_lines"]), "Demo red becomes latest clinical assessment"


def test_noshow_past_ga_must_not_change_with_later_pregnancy(review, record_property):
    from core.ai.noshow import build_features, load_visits
    from core.models import Appointment, Arrival, NoteExtraction, Patient

    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        appt = s.scalar(select(Appointment).where(Appointment.patient_id == p.id))
        appt.appt_date = review.today - timedelta(days=500)
        s.add(Arrival(pid="AUDIT-B", visit_date=appt.appt_date))  # Historical day has known attendance labels.
        p.edd = appt.appt_date + timedelta(days=140)  # GA 20 at historical visit.
        s.add(NoteExtraction(appointment_id=appt.id, ga_weeks=20, ga_days=0, risk_flags=[], rules_version="audit"))
        s.flush()
        before = build_features(load_visits(s)).set_index("appointment_id").loc[appt.id, "ga_weeks"]
        assert before == 20.0
        p.edd = p.edd + timedelta(days=500)  # Later pregnancy, same patient ID.
        s.flush()
        after = build_features(load_visits(s)).set_index("appointment_id").loc[appt.id, "ga_weeks"]
        note_ga = s.scalar(select(NoteExtraction.ga_weeks).where(NoteExtraction.appointment_id == appt.id))
        evidence(record_property, "X04", historical_appointment=appt.appt_date, ga_before=before,
                 ga_after=None if pd.isna(after) else float(after), stored_ga=note_ga)
        assert after == before, "Current EDD rewrites a historical training feature despite stored historical GA"


def backup_module():
    spec = importlib.util.spec_from_file_location("audit_backup", ROOT / "scripts" / "backup_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_counter_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value INTEGER)")
        conn.execute("INSERT INTO marker VALUES (1)")


def read_counter(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT value FROM marker").fetchone()[0]


def test_two_backups_in_one_second_must_preserve_both(tmp_path, monkeypatch, record_property):
    module = backup_module()
    fixed = datetime(2026, 11, 16, 8, 30, 0, tzinfo=module.TZ)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: fixed))
    source, out = tmp_path / "source.db", tmp_path / "backups"
    make_counter_db(source)
    first = module.backup(source, out, 14)
    assert read_counter(first) == 1
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE marker SET value = 2")
    second = module.backup(source, out, 14)
    evidence(record_property, "X05", same_path=first == second, files=len(list(out.glob("*.db"))),
             first_snapshot_value=read_counter(first), second_snapshot_value=read_counter(second))
    assert first != second and read_counter(first) == 1, "Second backup overwrites first recovery point"


def test_invalid_backup_retention_must_not_report_deleted_backup(tmp_path, record_property):
    module = backup_module()
    source, out = tmp_path / "source.db", tmp_path / "backups"
    make_counter_db(source)
    try:
        result = module.backup(source, out, -1)
    except (Exception, SystemExit) as error:
        evidence(record_property, "X06", keep=-1, exception=type(error).__name__, files=len(list(out.glob("*.db"))))
        assert isinstance(error, (ValueError, SystemExit)), "Invalid retention causes an OS error during backup deletion"
        return
    evidence(record_property, "X06", keep=-1, returned_exists=result.exists(), files=len(list(out.glob("*.db"))))
    assert result.exists(), "Backup reports success after removing the only newly created recovery point"


class FakeResponse:
    status_code = 200

    def json(self):
        return []  # Valid JSON but not the provider's expected object schema.

    def raise_for_status(self):
        return None


def test_ollama_embedding_bad_schema_must_raise_llm_error(monkeypatch, record_property):
    from core.ai.llm_client import LlmError
    from core.ai.reader import semantic

    embedder = semantic.OllamaEmbedder("synthetic-local-model")
    embedder._local = True  # Bypass model discovery. Transport is a fake; no Ollama call.
    monkeypatch.setattr(semantic.requests, "post", lambda *a, **kw: FakeResponse())
    caught = None
    try:
        embedder.embed(["synthetic"])
    except Exception as error:
        caught = error
    evidence(record_property, "X07_LOCAL", exception=type(caught).__name__)
    assert isinstance(caught, LlmError), "Malformed provider response escapes expected fallback boundary"


def test_hybrid_embedding_bad_schema_must_fall_back(tmp_path, monkeypatch, record_property):
    from core.ai import llm_client
    from core.ai.reader import semantic

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", "1")
    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "synthetic-spend.json")
    monkeypatch.setattr(semantic.requests, "post", lambda *a, **kw: FakeResponse())

    class Local:
        name = "synthetic-local"
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return [[1.0, 0.0] for _ in texts]

    local = Local()
    primary = semantic.OpenAIEmbedder(api_key="synthetic-no-real-key")
    hybrid = semantic.HybridEmbedder(primary, local)
    caught, vectors = None, None
    try:
        vectors = hybrid.embed(["synthetic"])
    except Exception as error:
        caught = error
    evidence(record_property, "X07_HYBRID", exception=type(caught).__name__, local_calls=local.calls, vectors=vectors)
    assert local.calls == 1 and vectors == [[1.0, 0.0]], "Bad primary response bypasses local fallback"


def test_reader_must_keep_a_result_when_embedding_schema_is_bad(monkeypatch, record_property):
    from test_ai_red_keep import TEXT, _llm, _rules

    from core.ai.form_reader import read_free_text
    from core.ai.reader import semantic
    from core.levels import Level

    rules = _rules()
    embedder = semantic.OllamaEmbedder("synthetic-local-model")
    embedder._local = True
    monkeypatch.setattr(semantic.requests, "post", lambda *a, **kw: FakeResponse())
    result, caught = None, None
    try:
        result = read_free_text(TEXT, 32, _llm(), rules, ner=False, embedder=embedder)
    except Exception as error:
        caught = error
    evidence(record_property, "X07_READER", enabled=rules["form_cap"]["ai_only_red_keep"]["enabled"],
             exception=type(caught).__name__, result_level=result.level.code if result else None)
    assert result is not None and result.level == Level.YELLOW, "Reader loses assessment instead of degrading safely"


def test_previous_pregnancy_sent_milestone_must_not_block_current_one(review, record_property):
    from core.config import load_rules
    from core.forms import build_states
    from core.models import FormSchedule
    from core.repo import upsert_patient
    from core.rules.form_scheduler import plan_for_patient

    rules = load_rules()
    assert rules["forms"]["resend_routine"] is False
    with review.db.session_scope() as s:
        p = upsert_patient(s, "AUDIT-C", "Synthetic Patient C", "0900000003", "Việt Nam")
        p.edd, p.consent_status = review.today + timedelta(days=70), "granted"
        add_program(s, p.id)
        old_day = review.today - timedelta(days=500)
        s.add(FormSchedule(patient_id=p.id, send_date=old_day, form_code="F5", status="exported",
                           reason="Previous pregnancy milestone", rules_version="audit"))
        s.flush()
        state = next(st for st in build_states(s, review.today, rules) if st.patient_id == p.id)
        f5 = next(d for d in plan_for_patient(state, review.today, rules) if d.form_code == "F5")
        assert state.last_visit is None and not state.answered and p.delivery_date is None
        evidence(record_property, "X08", edd=p.edd, current_ga=f5.ga, old_export=old_day,
                 sent=state.sent, send=f5.send, skip=f5.skip_reason)
        assert f5.send, "An export 500 days earlier blocks F5 in the current pregnancy"
