"""Lọc khoa và gộp lịch cùng PID cùng ngày (docs/04 A2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from core.ingest.his_reader import HisRow
from core.textutil import contains_keyword


@dataclass(frozen=True)
class Slot:
    """Một lịch gốc trong nhóm đã gộp: giờ hẹn, dịch vụ, ghi chú của dòng đó."""

    start_time: time
    service: str
    note: str


@dataclass
class MergedAppt:
    pid: str
    appt_date: date
    start_time: time
    end_time: time | None
    services: list[str]
    note: str
    full_name: str
    phone: str
    phone_raw: str
    nationality: str
    department: str
    doctor: str
    booking_source: str
    booked_at: datetime | None
    merged_from: list[int] = field(default_factory=list)  # số dòng Excel gốc
    conflict: bool = False
    conflict_detail: str = ""
    warnings: list[str] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)

    @property
    def is_merged(self) -> bool:
        return len(self.merged_from) > 1


def filter_departments(rows: list[HisRow], included: list[str]) -> tuple[list[HisRow], list[HisRow]]:
    """File OH không có cột khoa (đã là lịch của khoa) → dòng khoa trống được giữ."""
    keep = [r for r in rows if r.department in included or not r.department]
    dropped = [r for r in rows if r.department and r.department not in included]
    return keep, dropped


def _minutes_between(d: date, a: time, b: time) -> float:
    return (datetime.combine(d, b) - datetime.combine(d, a)).total_seconds() / 60


def needs_fasting(service: str, fasting_keywords: list[str]) -> bool:
    return any(contains_keyword(service, k) for k in fasting_keywords)


def merge_appointments(
    rows: list[HisRow], conflict_minutes: int, fasting_keywords: list[str]
) -> list[MergedAppt]:
    """Gom theo (pid, ngày hẹn): giữ giờ sớm nhất, hợp dịch vụ, gộp ghi chú."""
    groups: dict[tuple[str, date | None], list[HisRow]] = {}
    for r in rows:
        if r.start_time is None or r.appt_date is None:
            continue
        groups.setdefault((r.pid, r.appt_date), []).append(r)

    merged: list[MergedAppt] = []
    for (_pid, d), grp in groups.items():
        assert d is not None
        grp = sorted(grp, key=lambda r: (r.start_time, r.row_no))
        first = grp[0]
        assert first.start_time is not None
        services: list[str] = []
        for r in grp:
            if r.service and r.service not in services:
                services.append(r.service)
        notes = [r.note for r in grp if r.note]
        conflict, detail = False, ""
        if len(grp) > 1:
            last = grp[-1]
            assert last.start_time is not None
            span = _minutes_between(d, first.start_time, last.start_time)
            if span > conflict_minutes:
                conflict, detail = True, f"Hai lịch cách nhau {span:.0f} phút (> {conflict_minutes})"
            for r in grp[1:]:
                if r.start_time and r.start_time > first.start_time and needs_fasting(r.service, fasting_keywords):
                    conflict = True
                    detail = (detail + "; " if detail else "") + f"Dịch vụ cần nhịn ăn ở lịch muộn: {r.service}"
        ends = [r.end_time for r in grp if r.end_time]
        merged.append(
            MergedAppt(
                pid=first.pid,
                appt_date=d,
                start_time=first.start_time,
                end_time=max(ends) if ends else None,
                services=services,
                note=" | ".join(dict.fromkeys(notes)),
                full_name=first.full_name,
                phone=first.phone,
                phone_raw=first.phone_raw,
                nationality=first.nationality,
                department=first.department,
                doctor=first.doctor,
                booking_source=first.booking_source,
                booked_at=first.booked_at,
                merged_from=[r.row_no for r in grp],
                conflict=conflict,
                conflict_detail=detail,
                warnings=sorted({w for r in grp for w in r.warnings}),
                slots=[Slot(r.start_time, r.service, r.note) for r in grp if r.start_time is not None],
            )
        )
    merged.sort(key=lambda m: (m.start_time, m.pid))
    return merged
