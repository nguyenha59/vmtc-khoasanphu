"""Gửi cảnh báo qua Teams Workflows webhook (HTTP POST). Payload không định danh."""

from __future__ import annotations

import os
from typing import Any

import requests

from core.channels.base import AlertPayload

LEVEL_VI = {"red": "ĐỎ", "yellow": "VÀNG", "green": "XANH"}


def build_card(alerts: list[AlertPayload], title: str) -> dict[str, Any]:
    body: list[dict[str, Any]] = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"}]
    for a in alerts:
        body.append({
            "type": "TextBlock", "wrap": True,
            "text": f"[{LEVEL_VI.get(a.level, a.level)}] {a.patient_code} · {a.ga_text} · " + "; ".join(a.reasons),
        })
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "body": body},
        }],
    }


class TeamsAlertChannel:
    name = "teams_webhook"

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("TEAMS_WEBHOOK_URL", "")
        if not self.url:
            raise ValueError("Chưa đặt TEAMS_WEBHOOK_URL")

    def _post(self, payload: dict[str, Any]) -> None:
        r = requests.post(self.url, json=payload, timeout=10)
        r.raise_for_status()

    def send(self, alert: AlertPayload) -> None:
        self._post(build_card([alert], "🚨 Cảnh báo ĐỎ – cần gọi thai phụ trong ngày"))

    def send_digest(self, alerts: list[AlertPayload]) -> None:
        if alerts:
            self._post(build_card(alerts, f"Bản gom cảnh báo VÀNG ({len(alerts)} ca) – liên hệ trong 24 giờ"))
