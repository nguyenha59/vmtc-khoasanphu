"""Kênh cảnh báo giả: ghi vào data/outbox/alerts.jsonl."""

from __future__ import annotations

import json
from pathlib import Path

from core.channels.base import AlertPayload
from core.config import work_dir


class MockAlertChannel:
    name = "mock"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or work_dir("outbox") / "alerts.jsonl"
        self.sent: list[AlertPayload] = []

    def _write(self, kind: str, items: list[AlertPayload]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "alerts": [a.to_dict() for a in items]}, ensure_ascii=False) + "\n")

    def send(self, alert: AlertPayload) -> None:
        self.sent.append(alert)
        self._write("immediate", [alert])

    def send_digest(self, alerts: list[AlertPayload]) -> None:
        self.sent += alerts
        self._write("digest", alerts)
