"""Ghi Excel không công thức (chống chèn công thức) + kiểm tra file nén tải lên."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest
from openpyxl import load_workbook

from core.safe_files import UnsafeFile, check_xlsx_bytes, df_to_xlsx_bytes


def test_formula_written_as_text():
    df = pd.DataFrame([{"P": '=HYPERLINK("http://x","bấm")', "P1": "+1+1", "P2": "@SUM(A1)", "SĐT": "0912345678"}])
    ws = load_workbook(io.BytesIO(df_to_xlsx_bytes(df))).active
    for cell in ws[2]:
        assert cell.data_type == "s", cell.value
    assert ws["A2"].value.startswith("=HYPERLINK") and ws["D2"].value == "0912345678"


def test_worldsms_export_uses_safe_writer():
    from core.rules.export_worldsms import to_xlsx_bytes

    ws = load_workbook(io.BytesIO(to_xlsx_bytes(pd.DataFrame([{"P": "=1+1"}])))).active
    assert ws["A2"].data_type == "s"


def test_zip_bomb_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.xml", b"0" * (50 * 1024 * 1024))  # nén còn vài chục KB, tỉ lệ > 200
    with pytest.raises(UnsafeFile):
        check_xlsx_bytes(buf.getvalue())


def test_normal_xlsx_and_csv_pass():
    check_xlsx_bytes(df_to_xlsx_bytes(pd.DataFrame([{"a": "1"}])))
    check_xlsx_bytes(b"pid,name\n1,A\n")
