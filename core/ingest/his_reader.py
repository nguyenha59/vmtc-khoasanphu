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
    nationality_guessed: bool = False  # file không có cột quốc tịch → đoán theo tên
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


def present_fields(raw: pd.DataFrame, cfg: HisColumns) -> dict[str, str]:
    """Trường chuẩn → tên cột trong file (một trường nhiều tên cột: lấy tên đầu tiên có trong file)."""
    cols = {str(c).strip() for c in raw.columns}
    out: dict[str, str] = {}
    for col, fld in cfg.columns.items():
        if col in cols and fld not in out:
            out[fld] = col
    return out


def missing_columns(raw: pd.DataFrame, cfg: HisColumns) -> list[str]:
    """Tên cột gốc của các trường bắt buộc bị thiếu. Mục "a|b" = cần một trong các trường."""
    present = present_fields(raw, cfg)
    missing: list[str] = []
    for item in cfg.required:
        fields = item.split("|")
        if any(f in present for f in fields):
            continue
        names = [c for c, f in cfg.columns.items() if f == fields[0]] or [fields[0]]
        label = " / ".join(names)
        if label not in missing:
            missing.append(label)
    return missing


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


def _has_clock(v: Any) -> bool:
    return bool(re.search(r"\d{1,2}:\d{2}", _clean(v)))


def parse_datetime(v: Any, date_fmt: str, time_fmt: str) -> datetime | None:
    s = _clean(v)
    if not s:
        return None
    for f in (f"{date_fmt} {time_fmt}", f"{date_fmt} %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
              "%d/%m/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", date_fmt):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def split_self_booking(note: str, prefixes: tuple[str, ...]) -> tuple[str, bool]:
    """Tách tiền tố 'khách tự đặt' (ví dụ "Autobooking") khỏi ghi chú. Trả (ghi chú còn lại, có tự đặt)."""
    found = False
    for pfx in prefixes:
        # File OH: "Autobooking_DRP_..." – dấu "_" cũng là ranh giới.
        pattern = re.compile(rf"(?<![^\W_]){re.escape(pfx)}(?![^\W_])[\s:;,.\-|_]*", re.IGNORECASE)
        note, k = pattern.subn(" ", note)
        found = found or k > 0
    return re.sub(r"\s+", " ", note).strip(" ;,.-|"), found


def nationality_from_name(name: str, cfg: HisColumns) -> str:
    """File không có cột quốc tịch → đoán theo tên (his_columns.yaml). Trả "Việt Nam" hoặc nhãn nước ngoài."""
    from core.textutil import deaccent, has_diacritics

    nn = cfg.name_nationality
    if not nn.get("nationality_from_name") or not name.strip():
        return "Việt Nam"
    foreign = str(nn.get("foreign_label", "Nước ngoài (theo tên)"))
    tokens = re.findall(r"[a-z]+", deaccent(name).lower())
    if not tokens:
        return foreign
    syllable = re.compile(r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnpqrstvx])?[aeiouy]{1,3}(ch|ng|nh|[cmnpt])?$")
    if not all(syllable.match(t) for t in tokens):
        return foreign  # có chữ không phải âm tiết tiếng Việt (Jérôme, Ștefănescu, Tanaka, Johnson…)
    if "đ" in name.lower() or has_diacritics(name):
        return "Việt Nam"  # có dấu + mọi chữ là âm tiết tiếng Việt hợp lệ (vd "Mai Hà")
    surnames = {str(x).lower() for x in nn.get("vietnam_surnames", [])}
    ambiguous = {str(x).lower() for x in nn.get("vietnam_ambiguous_surnames", [])}
    middles = {str(x).lower() for x in nn.get("vietnam_middle_names", [])}
    if any(ch in name.lower() for ch in "fjwz"):
        return foreign  # không có trong chữ Việt
    if tokens[0] in surnames and (tokens[0] not in ambiguous or any(t in middles for t in tokens[1:])):
        return "Việt Nam"
    return foreign


def strip_booking_meta(note: str, patterns: tuple[str, ...]) -> str:
    """Bỏ thông tin đặt lịch (kênh, cộng tác viên, mã đơn) khỏi ghi chú khách tự đặt – chỉ giữ nội dung khám."""
    for pat in patterns:
        note = re.sub(pat, " ", note, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", note.replace("_", " ")).strip(" ;,.-|")


def standardize(raw: pd.DataFrame, cfg: HisColumns) -> list[HisRow]:
    present = present_fields(raw, cfg)
    rows: list[HisRow] = []
    for i, rec in enumerate(raw.to_dict("records")):
        r = {fld: rec.get(col) for fld, col in present.items()}
        # File OH: ngày + giờ hẹn chung một ô ("Appointment Start Date Time").
        appt_dt = parse_datetime(r.get("appt_datetime"), cfg.date_format, cfg.time_format)
        if appt_dt is not None and _has_clock(r.get("appt_datetime")):
            appt_date: date | None = appt_dt.date()
            start: time | None = appt_dt.time().replace(second=0, microsecond=0)
        else:
            appt_date = parse_date(r.get("appt_date"), cfg.date_format)
            start = parse_time(r.get("start_time"), cfg.time_format)
        phone_raw = _clean(r.get("phone"))
        note, self_booked = split_self_booking(_clean(r.get("note")), cfg.self_booking_prefixes)
        if self_booked:
            note = strip_booking_meta(note, cfg.booking_meta_patterns)
        source = _clean(r.get("booking_source")) or ("Autobooking" if self_booked else "")
        rows.append(
            HisRow(
                row_no=i + 2,
                pid=_clean(r.get("pid")),
                full_name=_clean(r.get("full_name")),
                phone=normalize_phone(phone_raw),
                phone_raw=phone_raw,
                nationality=_clean(r.get("nationality")) or nationality_from_name(_clean(r.get("full_name")), cfg),
                nationality_guessed=not _clean(r.get("nationality")) and bool(cfg.name_nationality.get(
                    "nationality_from_name")),
                appt_date=appt_date,
                start_time=start,
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
