"""Việc định kỳ chạy nền trong máy chủ API (mỗi phút):

1. Gửi bản gom cảnh báo VÀNG đúng các mốc `channels.yellow_digest_times` (trước đây chỉ gửi khi có người bấm nút).
   Máy chủ khởi động lại sau mốc → gửi bù; mỗi mốc ghi vào audit_log nên không gửi trùng.
2. Gửi lại cảnh báo ĐỎ chưa gửi được (kênh gửi lỗi lúc tạo – xem core.alerts._send_after_commit).
3. Mỗi ngày một lần: xóa file uploads / exports / outbox quá `retention_days`, dọn phiên đăng nhập đã hết hạn.

Không gọi mạng trong lúc đang giữ khóa ghi CSDL: luôn đọc → gửi → ghi.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from core import audit
from core.alerts import payload_for, send_yellow_digest
from core.channels import get_alert_channel
from core.config import load_rules
from core.db import now, session_scope
from core.models import Alert, Assessment, AuditLog, FormResponse, Patient, UserSession
from core.retention import purge_all

log = logging.getLogger(__name__)
RED_RETRY_AFTER = timedelta(minutes=2)  # để lần gửi ngay sau khi lưu (after_commit) chạy trước


def due_digest_slots(times: list[str], at: datetime, done: set[str]) -> list[str]:
    """Các mốc hôm nay đã tới giờ mà chưa gửi. Hàm thuần."""
    today = at.date().isoformat()
    out = []
    for t in times:
        hh, mm = (int(x) for x in t.split(":"))
        slot = f"{today} {hh:02d}:{mm:02d}"
        if (at.hour, at.minute) >= (hh, mm) and slot not in done:
            out.append(slot)
    return out


def _done(action: str, prefix: str) -> set[str]:
    with session_scope() as s:
        return {e for (e,) in s.execute(select(AuditLog.entity_id).where(AuditLog.action == action,
                                                                         AuditLog.entity_id.like(f"{prefix}%")))}


def run_digests(at: datetime) -> int:
    rules = load_rules()
    slots = due_digest_slots(list(rules["channels"]["yellow_digest_times"]), at,
                             _done("auto_yellow_digest", at.date().isoformat()))
    if not slots:
        return 0
    with session_scope() as s:
        n = send_yellow_digest(s, get_alert_channel(rules))  # đọc → gửi → ghi sent_at
        for slot in slots:
            audit.log("auto_yellow_digest", "slot", slot, {"count": n}, session=s)
    return n


def retry_unsent_red(at: datetime) -> int:
    rules = load_rules()
    channel = get_alert_channel(rules)
    with session_scope() as s:
        rows = s.execute(
            select(Alert, Assessment, Patient)
            .join(Assessment, Alert.assessment_id == Assessment.id)
            .join(FormResponse, Assessment.response_id == FormResponse.id)
            .join(Patient, FormResponse.patient_id == Patient.id)
            .where(Alert.level == "red", Alert.sent_at.is_(None), Alert.status == "open",
                   Alert.created_at <= at - RED_RETRY_AFTER)
        ).all()
        todo = [(a.id, payload_for(a, p, list((asm.findings or {}).get("reasons", [])), at)) for a, asm, p in rows]
    sent = 0
    for alert_id, payload in todo:  # mỗi cảnh báo một giao dịch ngắn, gửi mạng ngoài giao dịch
        try:
            channel.send(payload)
        except Exception as e:  # noqa: BLE001
            audit.log("alert_send_failed", "alert", alert_id, {"error": type(e).__name__, "retry": True})
            continue
        with session_scope() as s:
            a = s.get(Alert, alert_id)
            if a is not None and a.sent_at is None:
                a.sent_at = now()
                audit.log("alert_resent", "alert", alert_id, session=s)
                sent += 1
    return sent


def daily_maintenance(at: datetime) -> dict[str, int] | None:
    day = at.date().isoformat()
    if _done("daily_maintenance", day):
        return None
    purged = purge_all(load_rules())
    with session_scope() as s:
        res = s.execute(delete(UserSession).where(UserSession.expires_at < at - timedelta(days=1)))
        out = {**purged, "sessions": res.rowcount or 0}  # type: ignore[attr-defined]
        audit.log("daily_maintenance", "date", day, out, session=s)
    return out


def tick(at: datetime | None = None) -> None:
    at = at or now()
    for job in (run_digests, retry_unsent_red, daily_maintenance):
        try:
            job(at)
        except Exception:  # một việc lỗi không làm dừng các việc khác / vòng lặp
            log.exception("Việc định kỳ %s lỗi", job.__name__)


_stop = threading.Event()
_thread: threading.Thread | None = None


def start(interval_seconds: int = 60) -> None:
    """Chạy nền trong tiến trình API. Tắt bằng APP_JOBS=0 (test, hoặc khi chạy nhiều tiến trình)."""
    global _thread
    if os.environ.get("APP_JOBS", "1") == "0" or (_thread is not None and _thread.is_alive()):
        return
    _stop.clear()

    def loop() -> None:
        while not _stop.is_set():
            tick()
            _stop.wait(interval_seconds)

    _thread = threading.Thread(target=loop, name="tl-jobs", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
