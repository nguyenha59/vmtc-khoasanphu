"""Kênh thử nghiệm API: CHỈ gửi tới số trong SMS_WHITELIST; số khác bị chặn và ghi lỗi."""

from __future__ import annotations

import os

import requests

from core.channels.base import RowResult, SendResult, SmsRow
from core.config import sms_whitelist
from core.security import normalize_phone


class TestApiSmsChannel:
    name = "test_api"
    __test__ = False  # không phải lớp test của pytest

    def __init__(self, whitelist: set[str] | None = None, url: str | None = None) -> None:
        self.whitelist = {normalize_phone(p) for p in (whitelist if whitelist is not None else sms_whitelist())}
        self.url = url if url is not None else os.environ.get("SMS_TEST_API_URL", "")

    def send_batch(self, template_code: str, rows: list[SmsRow]) -> SendResult:
        res = SendResult(channel=self.name, template=template_code)
        for r in rows:
            phone = normalize_phone(r.phone)
            if phone not in self.whitelist:
                res.rows.append(RowResult(r.patient_code, "blocked", "Số không nằm trong SMS_WHITELIST"))
                continue
            if not self.url:  # mô phỏng, không gọi mạng
                res.rows.append(RowResult(r.patient_code, "sent"))
                continue
            try:
                resp = requests.post(self.url, json={"to": phone, "template": template_code, "params": r.params},
                                     timeout=10)
                resp.raise_for_status()
                res.rows.append(RowResult(r.patient_code, "sent"))
            except requests.RequestException as e:
                res.rows.append(RowResult(r.patient_code, "error", str(e)[:200]))
        return res
