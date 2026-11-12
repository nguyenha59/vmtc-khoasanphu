"""Đọc / ghi file Excel an toàn.

- Ghi: mọi ô ghi dạng CHUỖI, không bao giờ là công thức (chống chèn công thức "=HYPERLINK(...)" từ tên / ghi chú).
- Đọc: kiểm tra file nén .xlsx trước khi giải nén (chống bom nén); XML do openpyxl đọc qua defusedxml (chống bom XML).
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
from openpyxl import Workbook

MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # file HIS thật vài MB; quá mức này coi là bom nén
MAX_RATIO = 200  # tỉ lệ giải nén bất thường
MAX_MEMBERS = 2000


class UnsafeFile(ValueError):
    pass


def check_xlsx_bytes(content: bytes) -> None:
    """Từ chối file nén bất thường trước khi pandas/openpyxl đọc. File không phải zip (CSV) thì bỏ qua."""
    if not content.startswith(b"PK"):
        return
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise UnsafeFile("File Excel hỏng hoặc không đúng định dạng.") from e
    total = sum(i.file_size for i in infos)
    if len(infos) > MAX_MEMBERS or total > MAX_UNCOMPRESSED_BYTES or total > MAX_RATIO * max(len(content), 1):
        raise UnsafeFile("File Excel có cấu trúc bất thường (quá lớn khi giải nén) – từ chối để bảo vệ máy chủ.")


def df_to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    """Ghi DataFrame ra .xlsx: tiêu đề + dữ liệu đều là chuỗi thuần, không công thức."""
    wb = Workbook()
    ws = wb.active
    rows = [[str(c) for c in df.columns]] + [["" if v is None else str(v) for v in r] for r in df.itertuples(index=False)]
    for r_idx, row in enumerate(rows, start=1):
        for c_idx, v in enumerate(row, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=v)
            cell.data_type = "s"  # openpyxl tự coi chuỗi bắt đầu "=" là công thức → ép lại thành chuỗi
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
