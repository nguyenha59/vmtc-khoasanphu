"""Đọc file Excel lịch hẹn HIS → danh sách dòng chuẩn (HisRow)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import IO, Any

import pandas as pd

from core.config import HisColumns
from core.security import normalize_phone


@dataclass
class HisRow:
    row_no: int  # số dòng trong Excel (tiêu đề là dòng 1)
    pid: str
    full_name: str
    phone: str
    nationality: str
    appt_date: date | None
    start_time: time | None
    end_time: time | None
    service: str
    department: str
    doctor: str = ""
    note: str = ""
    booking_source: str = ""
    booked_at: datetime | None = None
    phone_raw: str = ""
    warnings: list[str] = field(default_factory=list)


def read_raw(file: str | IO[bytes] | Any) -> pd.DataFrame:
    """Đọc Excel (hoặc CSV) giữ nguyên dạng chuỗi."""
    name = getattr(file, "name", str(file))
    if str(name).lower().endswith(".csv"):
        df = pd.read_csv(file, dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(file, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def missing_columns(raw: pd.DataFrame, cfg: HisColumns) -> list[str]:
    """Trả tên cột gốc (trong file HIS) của các trường bắt buộc bị thiếu."""
    reverse = {v: k for k, v in cfg.columns.items()}
    return [reverse.get(f, f) for f in cfg.required if reverse.get(f, f) not in raw.columns]


def _clean(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def parse_date(v: Any, fmt: str) -> date | None:
    s = _clean(v)
    if not s:
        return None
    for f in (fmt, "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def parse_time(v: Any, fmt: str) -> time | None:
    s = _clean(v)
    if not s:
        return None
    for f in (fmt, "%H:%M:%S", "%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, f).time().replace(second=0)
        except ValueError:
            continue
    return None


def parse_datetime(v: Any, date_fmt: str, time_fmt: str) -> datetime | None:
    s = _clean(v)
    if not s:
        return None
    for f in (f"{date_fmt} {time_fmt}", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", date_fmt):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def split_self_booking(note: str, prefixes: tuple[str, ...]) -> tuple[str, bool]:
    """Tách tiền tố 'khách tự đặt' (ví dụ "Autobooking") khỏi ghi chú. Trả (ghi chú còn lại, có tự đặt)."""
    found = False
    for pfx in prefixes:
        pattern = re.compile(rf"(?<!\w){re.escape(pfx)}(?!\w)[\s:;,.\-|]*", re.IGNORECASE)
        note, k = pattern.subn(" ", note)
        found = found or k > 0
    return re.sub(r"\s+", " ", note).strip(" ;,.-|"), found


def standardize(raw: pd.DataFrame, cfg: HisColumns) -> list[HisRow]:
    df = raw.rename(columns=cfg.columns)
    rows: list[HisRow] = []
    for i, r in enumerate(df.to_dict("records")):
        phone_raw = _clean(r.get("phone"))
        note, self_booked = split_self_booking(_clean(r.get("note")), cfg.self_booking_prefixes)
        source = _clean(r.get("booking_source")) or ("Autobooking" if self_booked else "")
        rows.append(
            HisRow(
                row_no=i + 2,
                pid=_clean(r.get("pid")),
                full_name=_clean(r.get("full_name")),
                phone=normalize_phone(phone_raw),
                phone_raw=phone_raw,
                nationality=_clean(r.get("nationality")) or "Việt Nam",
                appt_date=parse_date(r.get("appt_date"), cfg.date_format),
                start_time=parse_time(r.get("start_time"), cfg.time_format),
                end_time=parse_time(r.get("end_time"), cfg.time_format),
                service=_clean(r.get("service")),
                department=_clean(r.get("department")),
                doctor=_clean(r.get("doctor")),
                note=note,
                booking_source=source,
                booked_at=parse_datetime(r.get("booked_at"), cfg.date_format, cfg.time_format),
            )
        )
    return rows
