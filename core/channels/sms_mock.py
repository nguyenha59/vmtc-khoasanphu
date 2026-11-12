"""Kênh SMS giả: không gửi gì, chỉ ghi nhận."""

from __future__ import annotations

from core.channels.base import RowResult, SendResult, SmsRow


class MockSmsChannel:
    name = "mock"

    def __init__(self) -> None:
        self.sent: list[tuple[str, SmsRow]] = []

    def send_batch(self, template_code: str, rows: list[SmsRow]) -> SendResult:
        self.sent += [(template_code, r) for r in rows]
        return SendResult(channel=self.name, template=template_code,
                          rows=[RowResult(r.patient_code, "sent") for r in rows])
