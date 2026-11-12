"""Báo cáo: tóm tắt hồ sơ theo dõi (PDF), file danh sách Excel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
]


def _font() -> Path | None:
    return next((p for p in FONT_CANDIDATES if p.exists()), None)


def profile_pdf(title: str, lines: list[str], table: list[dict[str, Any]] | None = None) -> bytes | None:
    """PDF tóm tắt; trả None nếu máy không có font Unicode (trang sẽ cho tải HTML)."""
    font = _font()
    if font is None:
        return None
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("U", "", str(font))
    pdf.set_font("U", size=15)
    pdf.multi_cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("U", size=10)
    for ln in lines:
        pdf.multi_cell(0, 6, ln, new_x="LMARGIN", new_y="NEXT")
    if table:
        pdf.ln(3)
        cols = list(table[0])
        width = (pdf.w - 20) / len(cols)
        pdf.set_font("U", size=9)
        for c in cols:
            pdf.cell(width, 7, str(c), border=1)
        pdf.ln()
        for row in table[:40]:
            for c in cols:
                pdf.cell(width, 6, str(row[c])[:28], border=1)
            pdf.ln()
    pdf.ln(4)
    pdf.set_font("U", size=8)
    pdf.multi_cell(0, 5, new_x="LMARGIN", new_y="NEXT", text="Công cụ hỗ trợ – người quyết định cuối là điều dưỡng / bác sĩ. Dữ liệu demo là dữ liệu giả.")
    return bytes(pdf.output())


def profile_html(title: str, lines: list[str], table: list[dict[str, Any]] | None = None) -> bytes:
    body = "".join(f"<p>{ln}</p>" for ln in lines)
    if table:
        body += pd.DataFrame(table).to_html(index=False)
    return (f"<html><meta charset='utf-8'><body style='font-family:Arial'><h2>{title}</h2>{body}"
            "<p><i>Công cụ hỗ trợ – người quyết định cuối là điều dưỡng / bác sĩ.</i></p></body></html>").encode()


def xlsx_bytes(rows: list[dict[str, Any]]) -> bytes:
    from core.safe_files import df_to_xlsx_bytes

    return df_to_xlsx_bytes(pd.DataFrame(rows))
