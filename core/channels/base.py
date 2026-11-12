"""Giao diện kênh gửi tin. Đổi kênh = thêm file mới, không sửa nghiệp vụ."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass
class SmsRow:
    phone: str
    patient_code: str
    params: dict[str, str] = field(default_factory=dict)  # P, P1… – không chứa tên khách


@dataclass
class RowResult:
    patient_code: str
    status: str  # sent | exported | blocked | error
    error: str | None = None


@dataclass
class SendResult:
    channel: str
    template: str
    rows: list[RowResult] = field(default_factory=list)
    file_path: Path | None = None
    file_bytes: bytes | None = None

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.rows if r.status in ("sent", "exported"))

    @property
    def n_blocked(self) -> int:
        return sum(1 for r in self.rows if r.status == "blocked")


class SmsChannel(Protocol):
    name: str

    def send_batch(self, template_code: str, rows: list[SmsRow]) -> SendResult: ...


@dataclass(frozen=True)
class AlertPayload:
    """Cảnh báo KHÔNG định danh: chỉ mã R####, tuần thai, mức, lý do."""

    alert_id: int
    patient_code: str
    ga_text: str
    level: str
    reasons: tuple[str, ...]
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat(timespec="minutes")
        d["reasons"] = list(self.reasons)
        return d


class AlertChannel(Protocol):
    name: str

    def send(self, alert: AlertPayload) -> None: ...

    def send_digest(self, alerts: list[AlertPayload]) -> None: ...
