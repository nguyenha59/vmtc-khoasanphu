"""Chọn kênh theo config/rules.yaml (channels.sms, channels.alert)."""

from __future__ import annotations

import logging
import os

from sqlalchemy.orm import Session

from core.channels.alert_mock import MockAlertChannel
from core.channels.alert_teams import TeamsAlertChannel
from core.channels.base import AlertChannel, SendResult, SmsChannel
from core.channels.sms_excel import ExcelSmsChannel
from core.channels.sms_mock import MockSmsChannel
from core.channels.sms_test_api import TestApiSmsChannel
from core.config import Rules
from core.db import now
from core.models import MessageLog

log = logging.getLogger(__name__)

SMS_CHANNELS = {"excel": ExcelSmsChannel, "mock": MockSmsChannel, "test_api": TestApiSmsChannel}


def get_sms_channel(rules: Rules) -> SmsChannel:
    name = rules["channels"]["sms"]
    if name not in SMS_CHANNELS:
        raise ValueError(f"Kênh SMS '{name}' không hợp lệ: {list(SMS_CHANNELS)}")
    return SMS_CHANNELS[name]()


class AlertChannelMisconfigured(RuntimeError):
    """Cấu hình kênh cảnh báo thật nhưng thiếu thông tin → KHÔNG được coi cảnh báo là đã gửi."""


def get_alert_channel(rules: Rules) -> AlertChannel:
    """mock → ghi outbox. teams_webhook mà thiếu TEAMS_WEBHOOK_URL → ném lỗi khi gửi (cảnh báo giữ 'chưa gửi',
    tác vụ gửi lại đỏ thử lại, nhật ký có cảnh báo) – không lặng lẽ lùi về mock."""
    if rules["channels"]["alert"] == "teams_webhook":
        if os.environ.get("TEAMS_WEBHOOK_URL"):
            return TeamsAlertChannel()
        return _MisconfiguredAlertChannel()
    return MockAlertChannel()


class _MisconfiguredAlertChannel:
    name = "teams_webhook (thiếu TEAMS_WEBHOOK_URL)"

    def send(self, *args: object, **kwargs: object) -> None:
        log.error("channels.alert = teams_webhook nhưng thiếu TEAMS_WEBHOOK_URL – cảnh báo CHƯA được gửi")
        raise AlertChannelMisconfigured("Thiếu TEAMS_WEBHOOK_URL – cảnh báo chưa được gửi")

    def send_digest(self, *args: object, **kwargs: object) -> None:
        self.send()


def record_send(s: Session, result: SendResult) -> None:
    """Ghi message_log – không lưu nội dung tin nhắn."""
    for r in result.rows:
        s.add(MessageLog(channel=result.channel, template=result.template, patient_code=r.patient_code,
                         status=r.status, error=r.error, created_at=now()))
