"""Tạo 2 file WorldSMS nhắc hẹn theo cột P–P6 (docs/04 A6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import pandas as pd

from core.security import is_valid_phone
from core.textutil import deaccent

# Mặc định khi config/his_columns.yaml chưa có mục worldsms. Ý nghĩa P–P6 khoa xác nhận 12/11/2026.
DEFAULT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Số điện thoại", "phone"), ("P", "full_name"), ("P1", "pid"), ("P2", "start_time"),
    ("P3", "day"), ("P4", "month"), ("P5", "year"), ("P6", "arrival_time"),
)
COLUMNS = [h for h, _ in DEFAULT_COLUMNS]
VN = "Việt Nam"
DEFAULT_VN_ALIASES: tuple[str, ...] = ("Việt Nam", "Vietnam", "Viet Nam", "VN", "VNM", "Vietnamese")


def _norm(text: str) -> str:
    return " ".join(deaccent(text or "").lower().split())


def is_vietnamese(nationality: str, aliases: tuple[str, ...] = DEFAULT_VN_ALIASES) -> bool:
    """Quốc tịch Việt Nam (so không dấu, không phân biệt hoa thường); trống → Việt Nam."""
    n = _norm(nationality)
    return not n or n in {_norm(a) for a in aliases}


def format_phone(phone: str, fmt: str = "local") -> str:
    """phone đã chuẩn hóa 0xxxxxxxxx. intl → 84xxxxxxxxx."""
    return "84" + phone[1:] if fmt == "intl" and phone.startswith("0") else phone


class ExportLocked(Exception):
    pass


@dataclass
class ExportRow:
    phone: str
    full_name: str
    pid: str
    appt_date: date
    start_time: time
    arrival_time: time | None
    status: str  # auto | needs_review | confirmed
    nationality: str
    waiting: bool = False  # lịch khám chờ chưa có giờ đến → P6 ghi nhãn (ví dụ "Khám chờ")


def file_names(appt_date: date) -> tuple[str, str]:
    d = appt_date.isoformat()
    return f"nhac_hen_VN_{d}.xlsx", f"nhac_hen_NN_{d}.xlsx"


def build_exports(
    rows: list[ExportRow],
    appt_date: date,
    waiting_label: str = "Khám chờ",
    columns: tuple[tuple[str, str], ...] = DEFAULT_COLUMNS,
    phone_format: str = "local",
    vn_aliases: tuple[str, ...] = DEFAULT_VN_ALIASES,
) -> dict[str, pd.DataFrame]:
    pending = [r for r in rows if r.status == "needs_review" or (r.arrival_time is None and not r.waiting)]
    if pending:
        raise ExportLocked(f"Còn {len(pending)} ca 'Cần xác nhận' – xác nhận hết rồi mới xuất được file.")
    # Số điện thoại lỗi không gửi được qua WorldSMS → không đưa vào file (xem invalid_phone_rows).
    ordered = sorted((r for r in rows if is_valid_phone(r.phone)), key=lambda r: (r.start_time, r.pid))

    cols = columns or DEFAULT_COLUMNS

    def value(r: ExportRow, f: str) -> str:
        return {
            "phone": format_phone(r.phone, phone_format),
            "full_name": r.full_name,
            "pid": r.pid,
            "start_time": r.start_time.strftime("%H:%M"),
            "day": f"{r.appt_date.day:02d}",
            "month": f"{r.appt_date.month:02d}",
            "year": f"{r.appt_date.year}",
            "arrival_time": r.arrival_time.strftime("%H:%M") if r.arrival_time else waiting_label,
        }[f]

    def frame(sel: list[ExportRow]) -> pd.DataFrame:
        data = [{h: value(r, f) for h, f in cols} for r in sel]
        return pd.DataFrame(data, columns=[h for h, _ in cols])

    vn_name, nn_name = file_names(appt_date)
    return {
        vn_name: frame([r for r in ordered if is_vietnamese(r.nationality, vn_aliases)]),
        nn_name: frame([r for r in ordered if not is_vietnamese(r.nationality, vn_aliases)]),
    }


def invalid_phone_rows(rows: list[ExportRow]) -> list[ExportRow]:
    return [r for r in rows if not is_valid_phone(r.phone)]


def to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    from core.safe_files import df_to_xlsx_bytes

    return df_to_xlsx_bytes(df)


def write_exports(exports: dict[str, pd.DataFrame], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in exports.items():
        p = out_dir / name
        p.write_bytes(to_xlsx_bytes(df))
        paths.append(p)
    return paths
