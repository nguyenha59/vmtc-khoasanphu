"""Tạo 2 file WorldSMS nhắc hẹn theo cột P–P6 (docs/04 A6)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

import pandas as pd

from core.security import is_valid_phone

COLUMNS = ["Số điện thoại", "P", "P1", "P2", "P3", "P4", "P5", "P6"]  # [CẦN XÁC NHẬN tên cột thật]
VN = "Việt Nam"


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


def build_exports(rows: list[ExportRow], appt_date: date, waiting_label: str = "Khám chờ") -> dict[str, pd.DataFrame]:
    pending = [r for r in rows if r.status == "needs_review" or (r.arrival_time is None and not r.waiting)]
    if pending:
        raise ExportLocked(f"Còn {len(pending)} ca 'Cần xác nhận' – xác nhận hết rồi mới xuất được file.")
    # Số điện thoại lỗi không gửi được qua WorldSMS → không đưa vào file (xem invalid_phone_rows).
    ordered = sorted((r for r in rows if is_valid_phone(r.phone)), key=lambda r: (r.start_time, r.pid))

    def frame(sel: list[ExportRow]) -> pd.DataFrame:
        data = [
            {
                "Số điện thoại": r.phone,
                "P": r.full_name,
                "P1": r.pid,
                "P2": r.start_time.strftime("%H:%M"),
                "P3": f"{r.appt_date.day:02d}",
                "P4": f"{r.appt_date.month:02d}",
                "P5": f"{r.appt_date.year}",
                "P6": r.arrival_time.strftime("%H:%M") if r.arrival_time else waiting_label,
            }
            for r in sel
        ]
        return pd.DataFrame(data, columns=COLUMNS)

    vn_name, nn_name = file_names(appt_date)
    return {
        vn_name: frame([r for r in ordered if r.nationality == VN]),
        nn_name: frame([r for r in ordered if r.nationality != VN]),
    }


def invalid_phone_rows(rows: list[ExportRow]) -> list[ExportRow]:
    return [r for r in rows if not is_valid_phone(r.phone)]


def to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.astype(str).to_excel(buf, index=False)
    return buf.getvalue()


def write_exports(exports: dict[str, pd.DataFrame], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in exports.items():
        p = out_dir / name
        p.write_bytes(to_xlsx_bytes(df))
        paths.append(p)
    return paths
