"""Việc định kỳ (core/jobs.py): gom vàng theo giờ, gửi lại đỏ, xóa file quá hạn (#7, #8 rà soát 11/11/2026)."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import pytest

from core import jobs
from core.ai.llm_client import MockClient


class FakeChannel:
    def __init__(self, fail: bool = False) -> None:
        self.sent, self.digests, self.fail = [], [], fail

    def send(self, payload):
        if self.fail:
            raise ConnectionError("down")
        self.sent.append(payload)

    def send_digest(self, payloads):
        self.digests.append(list(payloads))


def test_due_digest_slots_catch_up_and_no_duplicates():
    at = datetime(2026, 11, 11, 15, 0)
    assert jobs.due_digest_slots(["08:00", "14:00"], at, set()) == ["2026-11-11 08:00", "2026-11-11 14:00"]
    assert jobs.due_digest_slots(["08:00", "14:00"], at, {"2026-11-11 08:00"}) == ["2026-11-11 14:00"]
    assert jobs.due_digest_slots(["08:00", "14:00"], datetime(2026, 11, 11, 7, 59), set()) == []


@pytest.fixture()
def alerts_db(db, rules, monkeypatch):
    """Một cảnh báo vàng + một cảnh báo đỏ chưa gửi (kênh lỗi lúc tạo)."""
    from core.alerts import submit_response
    from core.config import load_forms
    from core.models import Alert, Patient
    from core.repo import upsert_patient

    forms = load_forms()
    with db.session_scope() as s:
        upsert_patient(s, "P0001", "Phạm Thị Thu", "0900000001", "Việt Nam")
        p = s.query(Patient).filter_by(code="R0001").one()
        p.edd, p.consent_status = date.today() + timedelta(days=60), "granted"
    base = {q: "Không" for q in forms.forms["F6"]["questions"] if forms.questions[q]["type"] == "yesno"}
    for extra in ({"C4": "Có"}, {"C1": "Có"}):  # sốt → vàng; ra máu → đỏ
        with db.session_scope() as s:
            submit_response(s, s.query(Patient).one(), "F6", base | extra, "", rules=rules, forms_cfg=forms,
                            client=MockClient(), alert_channel=FakeChannel(fail=True), ner=False)
    with db.session_scope() as s:
        for a in s.query(Alert):
            a.created_at = a.created_at - timedelta(minutes=10)
    ch = FakeChannel()
    monkeypatch.setattr(jobs, "get_alert_channel", lambda _r: ch)
    return ch


def test_run_digests_sends_once_per_slot(db, alerts_db):
    from core.models import Alert

    at = jobs.now().replace(hour=23, minute=0)
    assert jobs.run_digests(at) == 1
    assert len(alerts_db.digests) == 1
    assert jobs.run_digests(at) == 0 and len(alerts_db.digests) == 1  # đã gửi các mốc hôm nay → không gửi lại
    with db.session_scope() as s:
        assert s.query(Alert).filter_by(level="yellow").one().sent_at is not None


def test_retry_unsent_red(db, alerts_db):
    from core.models import Alert

    assert jobs.retry_unsent_red(jobs.now()) == 1
    assert len(alerts_db.sent) == 1
    assert jobs.retry_unsent_red(jobs.now()) == 0
    with db.session_scope() as s:
        assert s.query(Alert).filter_by(level="red").one().sent_at is not None


def test_purge_all_removes_old_exports(tmp_path, rules):
    from core.retention import purge_all

    old = time.time() - (int(rules["retention_days"]) + 1) * 86400
    for folder in ("uploads", "exports", "outbox"):
        (tmp_path / folder).mkdir()
        f_old, f_new = tmp_path / folder / "old.xlsx", tmp_path / folder / "new.xlsx"
        f_old.write_text("x")
        f_new.write_text("x")
        os.utime(f_old, (old, old))
    assert purge_all(rules, tmp_path) == {"uploads": 1, "exports": 1, "outbox": 1}
    assert all((tmp_path / f / "new.xlsx").exists() and not (tmp_path / f / "old.xlsx").exists()
               for f in ("uploads", "exports", "outbox"))


def test_daily_maintenance_once_a_day_and_cleans_sessions(db, monkeypatch):
    from core.models import UserSession

    monkeypatch.setattr(jobs, "purge_all", lambda _r: {"uploads": 0, "exports": 0, "outbox": 0})
    at = jobs.now()
    with db.session_scope() as s:
        s.add(UserSession(sid_hash="a" * 64, username="nurse", user_id=1, created_at=at - timedelta(days=3),
                          expires_at=at - timedelta(days=2)))
        s.add(UserSession(sid_hash="b" * 64, username="nurse", user_id=1, created_at=at,
                          expires_at=at + timedelta(hours=12)))
    assert jobs.daily_maintenance(at)["sessions"] == 1
    assert jobs.daily_maintenance(at) is None  # đã chạy hôm nay
    with db.session_scope() as s:
        assert [x.sid_hash for x in s.query(UserSession)] == ["b" * 64]
