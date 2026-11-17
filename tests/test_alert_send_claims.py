"""Quyền gửi cảnh báo có thời hạn (core.alerts.claim_send) và tự thêm cột mới vào CSDL cũ (core.db)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from test_review_fixes_final import add_alert
from test_security_audit_review import review as review  # noqa: F401


def test_old_database_gets_new_nullable_columns(tmp_path, monkeypatch):
    from core import db as dbmod

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:  # bảng alert của phiên bản trước: chưa có send_claimed_at / version
        c.execute("CREATE TABLE alert (id INTEGER PRIMARY KEY, assessment_id INTEGER, level VARCHAR(8), "
                  "status VARCHAR(16), created_at DATETIME, sent_at DATETIME, handled_by INTEGER, "
                  "handled_at DATETIME, note TEXT, final_level VARCHAR(16))")
        c.execute("INSERT INTO alert (id, level, status) VALUES (1, 'red', 'open')")
    url = f"sqlite:///{path.as_posix()}"
    monkeypatch.setenv("APP_DB_URL", url)
    try:
        dbmod.init_db(url)
        with sqlite3.connect(path) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(alert)")}
            assert {"send_claimed_at", "version"} <= cols
            assert c.execute("SELECT level FROM alert WHERE id = 1").fetchone() == ("red",)  # dữ liệu cũ giữ nguyên
    finally:
        dbmod._engine = None
        dbmod._Session = None


def test_retry_job_during_immediate_send_does_not_send_twice(review, monkeypatch):
    """Lần gửi ngay sau khi lưu đang chờ kênh (vd AI chạy lâu nên cảnh báo đã quá RED_RETRY_AFTER) mà tác vụ nền
    chạy đúng lúc đó → chỉ một lần gửi."""
    from core import jobs
    from core.alerts import _send_after_commit
    from core.db import now
    from core.models import Alert

    aid = add_alert(review)  # created_at = 10 phút trước, chưa gửi
    sent: list[int] = []

    class Channel:
        def send(self, payload):
            sent.append(payload.alert_id)
            if len(sent) == 1:
                jobs.retry_unsent_red(now())  # tác vụ nền chạy giữa lúc đang gửi

    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: Channel())
    from core.alerts import payload_for

    with review.db.session_scope() as s:
        a = s.get(Alert, aid)
        from core.models import Patient

        _send_after_commit(s, aid, payload_for(a, s.get(Patient, review.pid), [], now()), Channel())
    with review.db.session_scope() as s:
        a = s.get(Alert, aid)
        assert sent == [aid] and a.sent_at is not None and a.send_claimed_at is None


def test_failed_send_releases_claim_and_retry_resends(review, monkeypatch):
    from core import jobs
    from core.db import now
    from core.models import Alert

    aid = add_alert(review)

    class Broken:
        def send(self, payload):
            raise ConnectionError("offline")

    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: Broken())
    assert jobs.retry_unsent_red(now()) == 0
    with review.db.session_scope() as s:
        a = s.get(Alert, aid)
        assert a.sent_at is None and a.send_claimed_at is None  # trả quyền ngay, không phải chờ hết hạn
    sent: list[int] = []

    class Ok:
        def send(self, payload):
            sent.append(payload.alert_id)

    monkeypatch.setattr(jobs, "get_alert_channel", lambda rules: Ok())
    assert jobs.retry_unsent_red(now() + timedelta(minutes=1)) == 1 and sent == [aid]
