"""Báo cáo: tóm tắt hồ sơ theo dõi (PDF), file danh sách Excel."""

from __future__ import annotations

from html import escape
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


LEVEL_VI = {"red": "ĐỎ", "yellow": "VÀNG", "green": "XANH"}


def _row_texts(table: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(dòng tiêu đề, nội dung đầy đủ) cho mỗi dòng bảng: cột cuối là nội dung dài (vd "Tóm tắt"), các cột còn lại ghép
    thành tiêu đề. Hàm thuần – không cắt chữ."""
    if not table:
        return []
    cols = list(table[0])
    *heads, last = cols if len(cols) > 1 else (cols + [""])
    out = []
    for row in table:
        head = " · ".join(LEVEL_VI.get(str(row.get(c, "")), str(row.get(c, ""))) for c in heads)
        out.append((head, str(row.get(last, "")) if last else ""))
    return out


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
        # Mỗi dòng in thành một đoạn ĐẦY ĐỦ, tự xuống dòng – không cắt tóm tắt lâm sàng vào ô bảng hẹp, không bỏ dòng.
        pdf.ln(3)
        pdf.set_font("U", size=9)
        for head, body in _row_texts(table):
            pdf.multi_cell(0, 5, head, new_x="LMARGIN", new_y="NEXT")
            if body:
                pdf.multi_cell(0, 5, "    " + body, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
    pdf.ln(4)
    pdf.set_font("U", size=8)
    pdf.multi_cell(0, 5, new_x="LMARGIN", new_y="NEXT", text="Công cụ hỗ trợ – người quyết định cuối là điều dưỡng / bác sĩ. Dữ liệu demo là dữ liệu giả.")
    return bytes(pdf.output())


def profile_html(title: str, lines: list[str], table: list[dict[str, Any]] | None = None) -> bytes:
    body = "".join(f"<p>{escape(str(ln))}</p>" for ln in lines)
    if table:
        body += pd.DataFrame(table).to_html(index=False)
    return (f"<html><meta charset='utf-8'><body style='font-family:Arial'><h2>{escape(str(title))}</h2>{body}"
            "<p><i>Công cụ hỗ trợ – người quyết định cuối là điều dưỡng / bác sĩ.</i></p></body></html>").encode()


def xlsx_bytes(rows: list[dict[str, Any]]) -> bytes:
    from core.safe_files import df_to_xlsx_bytes

    return df_to_xlsx_bytes(pd.DataFrame(rows))
