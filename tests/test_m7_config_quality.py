from __future__ import annotations

import pytest
from sqlalchemy import select

from core.config import load_rules
from core.models import AuditLog, RuleChange
from core.rule_config import RuleChangeError, approve, bump_minor, list_rules, propose, reject
from core.security import PermissionDenied


def test_bump_minor():
    assert bump_minor("0.1.0") == "0.2.0"
    assert bump_minor("1.9.3") == "1.10.0"


def test_doctor_approval_bumps_version_and_audits(db, tmp_rules):
    rules = load_rules(tmp_rules)
    v0 = rules.rules_version
    with db.session_scope() as s:
        ch = propose(s, "thresholds.epds_threshold", 11, "Theo bản tiếng Việt", role="nurse", user_id=1, rules=rules)
        assert ch.status == "pending"
        assert load_rules(tmp_rules).get("thresholds.epds_threshold") == 13  # chưa có hiệu lực
        new_v = approve(s, ch.id, role="doctor", user_id=2, rules=rules)
    r2 = load_rules(tmp_rules)
    assert r2.get("thresholds.epds_threshold") == 11
    assert r2.rules_version == new_v != v0
    assert "[CẦN XÁC NHẬN]" in tmp_rules.read_text(encoding="utf-8")  # giữ chú thích
    with db.session_scope() as s:
        actions = {a.action for a in s.scalars(select(AuditLog))}
    assert {"propose_rule", "approve_rule"} <= actions


def test_nurse_cannot_approve(db, tmp_rules):
    rules = load_rules(tmp_rules)
    with db.session_scope() as s:
        ch = propose(s, "thresholds.bp_yellow", {"sys": 135, "dia": 85}, "thử", role="nurse", user_id=1, rules=rules)
        with pytest.raises(PermissionDenied):
            approve(s, ch.id, role="nurse", user_id=1, rules=rules)
        with pytest.raises(PermissionDenied):
            approve(s, ch.id, role="admin", user_id=3, rules=rules)
        reject(s, ch.id, role="doctor", user_id=2)
        assert s.get(RuleChange, ch.id).status == "rejected"
    assert load_rules(tmp_rules).get("thresholds.bp_yellow") == {"sys": 140, "dia": 90}


@pytest.mark.parametrize("key", ["thresholds.epds_item10_red", "combine.ai_only_escalates"])
def test_locked_keys_cannot_change(db, tmp_rules, key):
    rules = load_rules(tmp_rules)
    with db.session_scope() as s:
        with pytest.raises(RuleChangeError):
            propose(s, key, {"value": False, "locked": False}, "x", role="doctor", user_id=2, rules=rules)


def test_wrong_shape_rejected(db, tmp_rules):
    rules = load_rules(tmp_rules)
    with db.session_scope() as s:
        with pytest.raises(RuleChangeError):
            propose(s, "thresholds.bp_red", 160, "x", role="doctor", user_id=2, rules=rules)


def test_operational_key_applies_without_doctor(db, tmp_rules):
    rules = load_rules(tmp_rules)
    with db.session_scope() as s:
        ch = propose(s, "forms.resend_block_days", 5, "giảm", role="nurse", user_id=1, rules=rules)
        assert ch.status == "approved"
    assert load_rules(tmp_rules).get("forms.resend_block_days") == 5


def test_channel_change_admin_only(db, tmp_rules):
    rules = load_rules(tmp_rules)
    with db.session_scope() as s:
        with pytest.raises(PermissionDenied):
            propose(s, "channels.sms", "mock", "x", role="nurse", user_id=1, rules=rules)
        propose(s, "channels.sms", "mock", "demo", role="admin", user_id=3, rules=rules)
    assert load_rules(tmp_rules).get("channels.sms") == "mock"


def test_list_rules_marks_locked(db, tmp_rules):
    rules = load_rules(tmp_rules)
    with db.session_scope() as s:
        rows = {r.key: r for r in list_rules(s, rules)}
    assert rows["thresholds.epds_item10_red"].locked
    assert not rows["thresholds.bp_red"].locked


def test_quality_metrics_from_db(db):
    from datetime import time

    from core.models import Alert, Appointment, ArrivalPlan, Assessment, FormResponse, Patient
    from core.quality import human_agreement

    with db.session_scope() as s:
        p = Patient(code="R1", pid="1", full_name_enc="x", phone_enc="x")
        s.add(p)
        s.flush()
        from datetime import date, datetime

        a = Appointment(patient_id=p.id, appt_date=date(2026, 11, 10), start_time=time(8))
        s.add(a)
        s.flush()
        s.add_all([
            ArrivalPlan(appointment_id=a.id, arrival_time=time(7, 45), proposed_time=time(7, 45), method="ai",
                        status="confirmed", reasons=["AI_RULE_DIFF"], rules_version="0.1.0"),
            ArrivalPlan(appointment_id=a.id, arrival_time=time(7, 30), proposed_time=time(7, 45), method="manual",
                        status="confirmed", reasons=["LOW_CONF"], rules_version="0.1.0"),
            ArrivalPlan(appointment_id=a.id, arrival_time=time(8), proposed_time=time(8), method="rule",
                        status="auto", reasons=[], rules_version="0.1.0"),
        ])
        r = FormResponse(patient_id=p.id, form_code="F1", submitted_at=datetime(2026, 11, 9), answers={})
        s.add(r)
        s.flush()
        asm = Assessment(response_id=r.id, level="red", rule_level="green", trend_level="green", ai_level="red",
                         rules_version="0.1.0", findings={})
        s.add(asm)
        s.flush()
        s.add(Alert(assessment_id=asm.id, level="red", status="closed", final_level="false_alarm"))
        s.flush()
        m = human_agreement(s)
    assert m["arrival_reviewed"] == 2 and m["arrival_agreement"] == 0.5
    assert m["transfer_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["alert_reviewed"] == 1 and m["alert_agreement"] == 0.0


def test_write_rule_keeps_other_lines_and_comments(tmp_path) -> None:
    import shutil

    import yaml

    from core.config import CONFIG_DIR
    from core.rule_config import write_rule

    src = CONFIG_DIR / "rules.yaml"
    dst = tmp_path / "rules.yaml"
    shutil.copy(src, dst)
    before = dst.read_text(encoding="utf-8").splitlines()
    v = write_rule(dst, "arrival.ai_confidence_min", 0.85)
    after = dst.read_text(encoding="utf-8").splitlines()
    assert len(before) == len(after)
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert len(changed) == 2  # chỉ dòng giá trị + rules_version
    data = yaml.safe_load(dst.read_text(encoding="utf-8"))
    assert data["arrival"]["ai_confidence_min"] == 0.85
    assert str(data["rules_version"]) == v
    # giá trị null giữ là null, danh sách ghi đúng
    write_rule(dst, "thresholds.weight_jump_kg_per_week", 1.5)
    write_rule(dst, "channels.yellow_digest_times", ["07:30", "13:30"])
    data = yaml.safe_load(dst.read_text(encoding="utf-8"))
    assert data["thresholds"]["weight_jump_kg_per_week"] == 1.5
    assert data["channels"]["yellow_digest_times"] == ["07:30", "13:30"]
    assert "# [CẦN XÁC NHẬN]" in dst.read_text(encoding="utf-8")


def test_latest_eval_prefers_real_model(db):
    from datetime import datetime

    from core.models import EvalRun
    from core.quality import latest_eval

    with db.session_scope() as s:
        s.add(EvalRun(ts=datetime(2026, 11, 10, 9), dataset="notes", model="ollama:x", metrics={"n": 1}))
        s.add(EvalRun(ts=datetime(2026, 11, 10, 10), dataset="notes", model="mock", metrics={"n": 2}))
        s.flush()
        assert latest_eval(s, "notes").model == "ollama:x"
        assert latest_eval(s, "notes", include_mock=True).model == "mock"
