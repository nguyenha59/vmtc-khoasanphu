"""Hồi quy cho các lỗi luồng tìm được ở đợt rà soát 15–16/11/2026 (reports/flow-audit-2026-11-15/audit_probes.py).

Mỗi test tái hiện đúng tình huống lỗi trên CSDL / cấu hình TẠM (fixture `review`), kênh gửi giả – không mạng ngoài.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import _block_outbound_network as _block_outbound_network  # noqa: F401
from conftest import db as db  # noqa: F401
from sqlalchemy import func, select
from test_security_audit_review import login
from test_security_audit_review import review as review  # noqa: F401


def _alert(review, *, level="red", status="open", pending=False, minutes_ago=10):
    from core.db import now
    from core.models import Alert, AlertReview, Assessment, FormResponse

    with review.db.session_scope() as s:
        r = FormResponse(patient_id=review.pid, form_code="F6", submitted_at=now(), answers={}, source="app")
        s.add(r)
        s.flush()
        a = Assessment(response_id=r.id, level=level, rule_level=level, trend_level="green", ai_level="green",
                       summary="Synthetic", findings={}, rules_version="test", created_at=now())
        s.add(a)
        s.flush()
        al = Alert(assessment_id=a.id, level=level, status=status, created_at=now() - timedelta(minutes=minutes_ago))
        s.add(al)
        s.flush()
        if pending:
            s.add(AlertReview(alert_id=al.id, kind="doctor_decision", status="pending", created_at=now()))
        return al.id


def _schedule(review, code="F5", *, eligible=True):
    from core.db import now
    from core.models import FormSchedule, PatientProgram

    with review.db.session_scope() as s:
        if eligible and s.scalar(select(PatientProgram).where(PatientProgram.patient_id == review.pid)) is None:
            s.add(PatientProgram(patient_id=review.pid, in_package=True, package_name="Synthetic", updated_at=now()))
        row = FormSchedule(patient_id=review.pid, send_date=review.today, form_code=code, status="planned",
                           rules_version="test")
        s.add(row)
        s.flush()
        return row.id


class _Sms:
    def __init__(self, status="sent", wait=None):
        self.rows, self.status, self.wait = [], status, wait

    def send_batch(self, template_code, rows):
        from core.channels.base import RowResult, SendResult

        if self.wait:
            self.wait()
        self.rows.extend(rows)
        return SendResult("test", template_code, [RowResult(r.patient_code, self.status) for r in rows])


# ---------------------------------------------------------------- an toàn lâm sàng
def test_incomplete_epds_is_rejected_and_token_kept(review):
    from core.forms import create_token
    from core.models import FormResponse, FormToken

    with review.db.session_scope() as s:
        token = create_token(s, review.pid, "S3", 3)
    r = review.client.post(f"/api/public/form/{token}", json={"answers": {"E1": 0}})
    assert r.status_code == 422
    with review.db.session_scope() as s:
        assert s.get(FormToken, token).used_at is None
        assert not s.scalar(select(func.count(FormResponse.id)).where(FormResponse.token == token))


def test_bp_morning_and_evening_keep_their_own_pairs(review):
    from api.charts import bp_series
    from core.alerts import history_for
    from core.config import load_rules
    from core.forms import create_token

    with review.db.session_scope() as s:
        token = create_token(s, review.pid, "M-THA", 3)
    r = review.client.post(f"/api/public/form/{token}",
                           json={"answers": {"BP_AM_SYS": 120, "BP_AM_DIA": 80, "BP_PM_SYS": 150, "BP_PM_DIA": 95}})
    assert r.status_code == 200
    with review.db.session_scope() as s:
        rows = bp_series(history_for(s, review.pid), load_rules())["rows"]
    assert {(x["sys"], x["dia"]) for x in rows} == {(120, 80), (150, 95)}


def test_open_alerts_are_never_truncated_and_counters_match(review):
    for _ in range(3):
        _alert(review, status="called")
    _alert(review, status="escalated", pending=True)
    login(review)
    body = review.client.get("/api/alerts").json()
    shown_red = sum(i["level"] == "red" and i["status"] != "closed" for i in body["items"])
    assert body["counts"]["red"] == shown_red == 4


def test_alert_list_shows_more_than_300_open(review):
    from core.db import now
    from core.models import Alert, Assessment, FormResponse

    with review.db.session_scope() as s:
        rs = [FormResponse(patient_id=review.pid, form_code="F6", submitted_at=now(), answers={}, source="app")
              for _ in range(301)]
        s.add_all(rs)
        s.flush()
        asms = [Assessment(response_id=r.id, level="red", rule_level="red", trend_level="green", ai_level="green",
                           summary="S", findings={}, rules_version="t", created_at=now()) for r in rs]
        s.add_all(asms)
        s.flush()
        s.add_all([Alert(assessment_id=a.id, level="red", status="open", created_at=now()) for a in asms])
    login(review)
    body = review.client.get("/api/alerts").json()
    assert len(body["items"]) == 301 and body["counts"]["red"] == 301


# ---------------------------------------------------------------- trạng thái xử trí
def test_saving_call_keeps_awaiting_doctor(review):
    from core.models import Alert, AlertReview

    aid = _alert(review, status="escalated", pending=True)
    login(review)
    r = review.client.post(f"/api/alerts/{aid}/handle", json={"close": False, "final_level": "keep_red", "note": "x"})
    assert r.status_code == 200 and r.json()["status"] == "awaiting_doctor"
    with review.db.session_scope() as s:
        assert s.get(Alert, aid).status == "escalated"
        assert s.scalar(select(func.count(AlertReview.id)).where(AlertReview.alert_id == aid,
                                                                 AlertReview.status == "pending")) == 1


def test_closing_escalated_yellow_finishes_doctor_task(review):
    from core.models import AlertReview

    aid = _alert(review, level="yellow", status="escalated", pending=True)
    login(review, "doctor")
    assert review.client.post(f"/api/alerts/{aid}/handle", json={"close": True, "note": "ok"}).status_code == 200
    with review.db.session_scope() as s:
        assert not s.scalar(select(func.count(AlertReview.id)).where(AlertReview.alert_id == aid,
                                                                     AlertReview.status == "pending"))


def test_stale_view_cannot_overwrite_doctor_decision(review):
    from core.alerts import HandleError, close_alert
    from core.config import load_rules
    from core.models import Alert

    aid = _alert(review)
    with review.db.session_scope() as nurse:
        assert nurse.get(Alert, aid).status == "open"  # màn hình điều dưỡng mở trước
        with review.db.session_scope() as doctor:
            close_alert(doctor, aid, user_id=None, role="doctor", final_level="keep_red", note="bác sĩ",
                        decision="emergency", rules=load_rules())
        with pytest.raises(HandleError):  # StaleAlert hoặc "đã đóng" – miễn bị chặn
            close_alert(nurse, aid, user_id=None, role="nurse", final_level="false_alarm", note="cũ", decision=None,
                        rules=load_rules())
        nurse.rollback()
    with review.db.session_scope() as s:
        assert s.get(Alert, aid).final_level == "keep_red"


def test_called_but_unsent_red_is_retried(review, monkeypatch):
    from core import jobs
    from core.db import now

    aid = _alert(review, status="called")
    sent = []
    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: type("C", (), {"send": lambda self, p: sent.append(p.alert_id)})())
    jobs.retry_unsent_red(now())
    assert sent == [aid]


def test_parallel_red_retries_send_once(review, monkeypatch):
    from core import jobs
    from core.db import now

    aid = _alert(review)
    barrier, sent = threading.Barrier(2), []

    class C:
        def send(self, payload):
            sent.append(payload.alert_id)

    real = jobs.session_scope
    first = {"done": set()}

    def gated_scope():  # hai tiến trình cùng đọc danh sách "chưa gửi" rồi mới giành quyền
        ctx = real()
        tid = threading.get_ident()
        if tid not in first["done"]:
            first["done"].add(tid)
            barrier.wait(timeout=6)
        return ctx

    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: C())
    monkeypatch.setattr(jobs, "session_scope", gated_scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for f in [pool.submit(jobs.retry_unsent_red, now()) for _ in range(2)]:
            f.result(timeout=15)
    assert sent.count(aid) == 1


def test_failed_red_send_releases_claim_for_retry(review, monkeypatch):
    from core import jobs
    from core.db import now
    from core.models import Alert

    aid = _alert(review)

    class Broken:
        def send(self, payload):
            raise OSError("offline")

    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: Broken())
    jobs.retry_unsent_red(now())
    with review.db.session_scope() as s:
        assert s.get(Alert, aid).sent_at is None


# ---------------------------------------------------------------- gửi tin
def test_failed_reminder_stays_due(review):
    from core.config import load_forms, load_rules
    from core.db import now
    from core.forms import due_reminders, send_reminders
    from core.models import FormToken, PatientProgram

    with review.db.session_scope() as s:
        s.get(FormToken, review.normal).created_at = now() - timedelta(hours=60)
        s.add(PatientProgram(patient_id=review.pid, in_package=True, package_name="S", updated_at=now()))
    sms = _Sms(status="error")
    with review.db.session_scope() as s:
        send_reminders(s, load_rules(), load_forms(), sms, None)
    assert sms.rows  # đã thật sự gọi kênh (khách thuộc diện theo dõi) – kênh báo lỗi
    with review.db.session_scope() as s:
        assert s.get(FormToken, review.normal).reminded_at is None
        assert review.normal in [t.token for t in due_reminders(s, now(), load_rules()["forms"]["no_reply_reminder_hours"])]


def test_concurrent_exports_send_one_link(review):
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms
    from core.models import FormToken

    _schedule(review)
    sms = _Sms()
    with review.db.session_scope() as s:
        before = s.scalar(select(func.count(FormToken.token)))

    def run():
        with review.db.session_scope() as s:
            return export_form_sms(s, review.today, load_rules(), load_forms(), sms, None, precomputed={}).n_ok

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = [f.result(timeout=20) for f in [pool.submit(run) for _ in range(2)]]
    with review.db.session_scope() as s:
        after = s.scalar(select(func.count(FormToken.token)))
    assert len(sms.rows) == 1 and sorted(counts) == [0, 1] and after == before + 1


def test_export_does_not_hold_write_lock_while_channel_waits(review):
    from core import audit
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms

    _schedule(review)
    entered, release, wrote = threading.Event(), threading.Event(), threading.Event()

    def wait():
        entered.set()
        assert release.wait(10)

    def export():
        with review.db.session_scope() as s:
            export_form_sms(s, review.today, load_rules(), load_forms(), _Sms(wait=wait), None, precomputed={})

    def other():
        audit.log("other_writer")
        wrote.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(export)
        assert entered.wait(10)
        f2 = pool.submit(other)
        ok = wrote.wait(3)
        release.set()
        f1.result(timeout=10)
        f2.result(timeout=10)
    assert ok


def test_broken_channel_returns_schedule_for_reexport(review):
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms
    from core.models import FormSchedule, FormToken

    sid = _schedule(review)

    class Broken:
        def send_batch(self, template_code, rows):
            raise OSError("down")

    with pytest.raises(OSError), review.db.session_scope() as s:
        export_form_sms(s, review.today, load_rules(), load_forms(), Broken(), None, precomputed={})
    with review.db.session_scope() as s:
        row = s.get(FormSchedule, sid)
        assert row.status == "planned" and row.token is None
        assert not s.scalar(select(func.count(FormToken.token)).where(FormToken.form_code == "F5"))


def test_patient_removed_from_monitoring_is_not_sent(review):
    from core.config import load_forms, load_rules
    from core.forms import export_form_sms
    from core.models import FormSchedule

    sid = _schedule(review)
    login(review)
    r = review.client.put(f"/api/patients/{review.pid}/program", json={"in_package": False, "override_monitoring": False})
    assert r.status_code == 200
    sms = _Sms()
    with review.db.session_scope() as s:
        export_form_sms(s, review.today, load_rules(), load_forms(), sms, None, precomputed={})
    with review.db.session_scope() as s:
        assert not sms.rows and s.get(FormSchedule, sid).status == "cancelled"


# ---------------------------------------------------------------- thai kỳ
def test_delivery_invalidates_pregnancy_links_but_not_postpartum(review):
    from core.forms import create_token

    with review.db.session_scope() as s:
        post = create_token(s, review.pid, "S1", 3)
    login(review)
    assert review.client.post(f"/api/profile/{review.pid}/delivery",
                              json={"delivery_date": review.today.isoformat()}).status_code == 200
    assert review.client.get(f"/api/public/form/{review.normal}").status_code in (404, 409)  # form thai kỳ cũ
    assert review.client.get(f"/api/public/form/{post}").status_code == 200  # form sau sinh vẫn mở


def test_previous_pregnancy_answer_does_not_block_current(review):
    from core.config import load_rules
    from core.db import now
    from core.forms import build_states
    from core.models import Appointment, FormResponse, Patient, PatientProgram
    from core.rules.form_scheduler import plan_forms

    with review.db.session_scope() as s:
        p = s.get(Patient, review.pid)
        p.edd = review.today + timedelta(days=70)
        for a in s.query(Appointment).filter_by(patient_id=p.id):
            a.appt_date = review.today - timedelta(days=10)
        s.add(PatientProgram(patient_id=p.id, in_package=True, package_name="S", updated_at=now()))
        s.add(FormResponse(patient_id=p.id, form_code="F5", source="app", answers={"C1": "Không"},
                           submitted_at=now() - timedelta(days=500)))
    with review.db.session_scope() as s:
        d = [x for x in plan_forms(build_states(s, review.today, load_rules()), review.today, load_rules())
             if x.patient_id == review.pid and x.form_code == "F5"]
    assert d and d[0].send


# ---------------------------------------------------------------- nhập dữ liệu
def _his(review, rows):
    import io

    import pandas as pd

    from core.config import load_his_columns

    mapping = load_his_columns().columns
    data = []
    for item in rows:
        v = {"pid": "SYN1", "full_name": "Synthetic", "phone": "0900000003", "nationality": "Việt Nam",
             "appt_date": review.today.strftime("%d/%m/%Y"), "start_time": "08:00", "end_time": "08:30",
             "service": "Khám thai", "department": "Phòng khám Sản phụ khoa", "note": "", **item}
        data.append({col: v.get(field, "") for col, field in mapping.items()})
    buf = io.BytesIO()
    pd.DataFrame(data).to_excel(buf, index=False)
    return buf.getvalue()


def _upload(review, content):
    login(review)
    return review.client.post("/api/appointments/upload", data={"d": review.today.isoformat(), "use_ai": "false"},
                              files={"file": ("syn.xlsx", content,
                                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_his_without_department_rows_keeps_existing_appointments(review):
    from datetime import time

    from core.models import Appointment

    with review.db.session_scope() as s:
        s.add(Appointment(patient_id=review.pid, appt_date=review.today, start_time=time(9), services=[]))
    r = _upload(review, _his(review, [{"department": "Khoa Khác"}]))
    assert not r.json().get("ok") and any(x["code"] == "E08" for x in r.json()["report"])
    with review.db.session_scope() as s:
        assert s.scalar(select(func.count(Appointment.id)).where(Appointment.appt_date == review.today)) == 1


def test_his_blank_pid_is_rejected(review):
    from core.models import Patient

    r = _upload(review, _his(review, [{"pid": "", "full_name": "A", "phone": "0900000003"},
                                      {"pid": "", "full_name": "B", "phone": "0900000004", "start_time": "10:00",
                                       "end_time": "10:30"}]))
    assert not r.json().get("ok") and any(x["code"] == "E07" for x in r.json()["report"])
    with review.db.session_scope() as s:
        assert not s.scalar(select(func.count(Patient.id)).where(Patient.pid == ""))


def test_arrivals_file_with_bad_row_is_rejected_and_keeps_day(review):
    import io

    from core.ingest.arrivals_reader import import_arrivals
    from core.models import Arrival

    day = review.today - timedelta(days=1)
    from datetime import time

    with review.db.session_scope() as s:
        s.add(Arrival(pid="KEEP-A", visit_date=day, checkin_time=time(8, 30)))
    f = io.StringIO(f"pid,visit_date,checkin_time\nNEW-B,{day},08:00\nKEEP-A,sai-ngay,08:30\n")
    f.name = "x.csv"
    with pytest.raises(ValueError, match="Dòng 3"), review.db.session_scope() as s:
        import_arrivals(s, f, f.name, None)
    with review.db.session_scope() as s:
        kept = set(s.scalars(select(Arrival.pid).where(Arrival.visit_date == day)))
    assert "KEEP-A" in kept and "NEW-B" not in kept  # không nhập phần nào, lượt đến cũ còn nguyên


# ---------------------------------------------------------------- cấu hình
@pytest.mark.parametrize("key,value", [("channels.yellow_digest_times", '["25:99"]'),
                                       ("thresholds.bp_red", "{sys: .nan, dia: .nan}"),
                                       ("thresholds.bp_red", "{sys: .inf, dia: 110}")])
def test_invalid_rule_values_are_rejected(review, key, value):
    login(review, "admin" if key.startswith("channels") else "nurse")
    r = review.client.post("/api/config/propose", json={"key": key, "value": value, "reason": "test giá trị sai"})
    assert r.status_code == 422


def test_rules_file_restored_when_transaction_rolls_back(review):
    import os
    from pathlib import Path

    from core.config import load_rules
    from core.rule_config import propose

    path = Path(os.environ["APP_RULES_FILE"])
    before_text = path.read_text(encoding="utf-8")
    before = load_rules().get("forms.token_ttl_days")
    with pytest.raises(RuntimeError), review.db.session_scope() as s:
        propose(s, "forms.token_ttl_days", before + 1, "test", role="nurse", user_id=None)
        raise RuntimeError("hủy giao dịch")
    assert load_rules().get("forms.token_ttl_days") == before and path.read_text(encoding="utf-8") == before_text


def test_rules_file_kept_when_transaction_commits(review):
    from core.config import load_rules
    from core.rule_config import propose

    before = load_rules().get("forms.token_ttl_days")
    with review.db.session_scope() as s:
        propose(s, "forms.token_ttl_days", before + 1, "test", role="nurse", user_id=None)
    assert load_rules().get("forms.token_ttl_days") == before + 1


# ---------------------------------------------------------------- vòng dò lỗi thứ 2 (Claude, 16/11/2026)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_link_ttl_cannot_become_zero_or_negative(review, value):
    login(review)
    r = review.client.post("/api/config/propose", json={"key": "forms.token_ttl_days", "value": value, "reason": "t"})
    assert r.status_code == 422


def test_delivery_before_pregnancy_start_rejected(review):
    from core.models import Patient

    with review.db.session_scope() as s:
        s.get(Patient, review.pid).edd = review.today + timedelta(days=60)
    login(review)
    r = review.client.post(f"/api/profile/{review.pid}/delivery",
                           json={"delivery_date": (review.today - timedelta(days=300)).isoformat()})
    assert r.status_code == 422


def test_arrival_time_before_opening_rejected(review):
    r = _upload(review, _his(review, [{"start_time": "08:00", "end_time": "08:30"}]))
    assert r.json().get("ok"), r.text
    items = review.client.get(f"/api/appointments?d={review.today.isoformat()}").json()["items"]
    pid = items[0]["plan_id"]
    bad = review.client.post(f"/api/appointments/plan/{pid}/confirm",
                             json={"d": review.today.isoformat(), "arrival_time": "00:05"})
    good = review.client.post(f"/api/appointments/plan/{pid}/confirm",
                              json={"d": review.today.isoformat(), "arrival_time": "07:30"})
    assert bad.status_code == 422 and good.status_code == 200


def test_text_only_message_on_epds_form_is_kept_and_read(review):
    """Không bao giờ làm mất lời khách viết vì EPDS chưa đủ câu: "em muốn chết" → nhận, Đỏ."""
    from core.forms import create_token
    from core.models import Assessment, FormResponse

    with review.db.session_scope() as s:
        token = create_token(s, review.pid, "S3", 3)
    r = review.client.post(f"/api/public/form/{token}", json={"answers": {}, "free_text": "em muốn chết"})
    assert r.status_code == 200 and r.json()["level"] == "red"
    with review.db.session_scope() as s:
        resp = s.scalar(select(FormResponse).where(FormResponse.token == token))
        asm = s.scalar(select(Assessment).where(Assessment.response_id == resp.id))
        assert any(f["code"] == "EPDS_INCOMPLETE" for f in asm.findings["rule"])


def test_partial_epds_with_text_is_at_least_yellow(review):
    from core.forms import create_token

    with review.db.session_scope() as s:
        token = create_token(s, review.pid, "S3", 3)
    r = review.client.post(f"/api/public/form/{token}", json={"answers": {"E1": 0}, "free_text": "em ổn"})
    assert r.status_code == 200 and r.json()["level"] in ("yellow", "red")


def test_reminder_not_sent_to_patient_outside_monitoring(review):
    from core.config import load_forms, load_rules
    from core.db import now
    from core.forms import send_reminders
    from core.models import FormToken

    with review.db.session_scope() as s:
        s.get(FormToken, review.normal).created_at = now() - timedelta(hours=60)
    sms = _Sms()
    with review.db.session_scope() as s:  # fixture: không có gói thai sản, rules package_only = true
        send_reminders(s, load_rules(), load_forms(), sms, None)
    assert not sms.rows


# ---------------------------------------------------------------- vòng dò lỗi thứ 3 (Claude, 16/11/2026)
def test_parallel_yellow_digests_send_each_alert_once(review):
    import time

    from core.alerts import send_yellow_digest

    aid = _alert(review, level="yellow")
    sent = []

    class C:
        def send_digest(self, payloads):
            time.sleep(1.0)  # tiến trình kia đọc "chưa gửi" trong lúc này
            sent.extend(p.alert_id for p in payloads)

    def run():
        with review.db.session_scope() as s:
            return send_yellow_digest(s, C())

    with ThreadPoolExecutor(max_workers=2) as pool:
        [f.result(timeout=20) for f in [pool.submit(run) for _ in range(2)]]
    assert sent.count(aid) == 1


def test_failed_yellow_digest_is_retried(review):
    from core.alerts import send_yellow_digest
    from core.models import Alert

    aid = _alert(review, level="yellow")

    class Broken:
        def send_digest(self, payloads):
            raise OSError("down")

    with pytest.raises(OSError), review.db.session_scope() as s:
        send_yellow_digest(s, Broken())
    with review.db.session_scope() as s:
        assert s.get(Alert, aid).sent_at is None


def test_double_rule_approval_applies_once(review):
    from core.models import AuditLog, RuleChange
    from core.rule_config import approve

    login(review)
    assert review.client.post("/api/config/propose", json={"key": "thresholds.bp_red", "value": "{sys: 165, dia: 110}",
                                                           "reason": "t"}).status_code == 200
    with review.db.session_scope() as s:
        cid = s.scalar(select(RuleChange.id).where(RuleChange.key == "thresholds.bp_red"))
    barrier = threading.Barrier(2)

    def run():
        with review.db.session_scope() as s:
            s.get(RuleChange, cid)
            barrier.wait(timeout=6)
            try:
                return approve(s, cid, role="doctor", user_id=None)
            except Exception as e:  # noqa: BLE001
                return type(e).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        out = [f.result(timeout=20) for f in [pool.submit(run) for _ in range(2)]]
    with review.db.session_scope() as s:
        assert s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "approve_rule")) == 1, out


def test_double_reopen_creates_one_doctor_task(review):
    from core.alerts import reopen_from_review
    from core.db import now
    from core.models import AlertReview

    aid = _alert(review, status="closed")
    with review.db.session_scope() as s:
        rv = AlertReview(alert_id=aid, kind="downgrade_review", status="pending", created_at=now())
        s.add(rv)
        s.flush()
        rid = rv.id
    barrier = threading.Barrier(2)

    def run():
        with review.db.session_scope() as s:
            s.get(AlertReview, rid)
            barrier.wait(timeout=6)
            try:
                return reopen_from_review(s, rid, None)
            except Exception as e:  # noqa: BLE001
                return type(e).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        [f.result(timeout=20) for f in [pool.submit(run) for _ in range(2)]]
    with review.db.session_scope() as s:
        assert s.scalar(select(func.count(AlertReview.id)).where(AlertReview.alert_id == aid,
                                                                 AlertReview.kind == "doctor_decision")) == 1


def test_printed_summary_keeps_full_clinical_text_and_all_rows():
    from core.reports import _row_texts, profile_pdf

    long = "Tuần thai 30+2. Nhóm nguy cơ: không. Mức ĐỎ: AI: Ra máu âm đạo; AI: Khó thở; Quy tắc: C1"
    rows = [{"Thời điểm": f"{d:02d}/01 08:00", "Form": "F5", "Mức": "red", "Tóm tắt": long} for d in range(1, 61)]
    texts = _row_texts(rows)
    assert len(texts) == 60 and all("Khó thở" in body for _h, body in texts) and "ĐỎ" in texts[0][0]
    pdf = profile_pdf("t", ["x"], rows)
    assert pdf is None or pdf.startswith(b"%PDF")


def test_withdraw_cancels_merged_rows_too(review):
    from core import consent
    from core.models import FormSchedule

    with review.db.session_scope() as s:
        s.add(FormSchedule(patient_id=review.pid, send_date=review.today, form_code="F5", status="planned",
                           rules_version="t"))
        s.add(FormSchedule(patient_id=review.pid, send_date=review.today, form_code="M-THA", status="merged",
                           rules_version="t"))
    with review.db.session_scope() as s:
        consent.withdraw(s, review.pid, None)
    with review.db.session_scope() as s:
        assert not s.scalar(select(func.count(FormSchedule.id)).where(
            FormSchedule.patient_id == review.pid, FormSchedule.status.in_(["planned", "merged"])))


def test_nan_measurement_rejected(review):
    from core.forms import create_token

    with review.db.session_scope() as s:
        token = create_token(s, review.pid, "M-THA", 3)
    r = review.client.post(f"/api/public/form/{token}", content='{"answers": {"BP_AM_SYS": NaN, "BP_AM_DIA": 80}}',
                           headers={"Content-Type": "application/json"})
    assert r.status_code == 422
