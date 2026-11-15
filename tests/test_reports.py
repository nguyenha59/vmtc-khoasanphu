"""Báo cáo tóm tắt hồ sơ: PDF nhiều dòng + bảng không được lỗi (fpdf2 multi_cell để con trỏ ở lề phải)."""

from __future__ import annotations

from core.reports import profile_html, profile_pdf


def test_profile_pdf_many_lines_and_table():
    lines = ["Khoảng theo dõi: 01/10/2026 → hôm nay", "Lần hẹn tới: 20/11/2026", "3 câu trả lời form; 1 đỏ, 1 vàng.",
             "Huyết áp tâm thu trung bình 128, cao nhất 151 mmHg.", "Đánh giá gần nhất: " + "đau đầu nhiều, nhìn mờ " * 8]
    table = [{"Thời điểm": "10/11 08:00", "Form": "F6", "Mức": "red", "Tóm tắt": "x" * 200}] * 5
    pdf = profile_pdf("Tóm tắt theo dõi R0046 – tuần thai 32+1", lines, table)
    if pdf is None:  # máy không có font Unicode → trang cho tải HTML
        assert profile_html("t", lines, table).startswith(b"<html")
        return
    assert pdf.startswith(b"%PDF")


def test_summary_postpartum_wording():
    from core.ai.summary import template_summary

    assert template_summary({"ga": "Hậu sản 52 ngày", "level": "green"}).startswith("Hậu sản 52 ngày.")
    assert template_summary({"ga": "32+1", "level": "green"}).startswith("Tuần thai 32+1.")


def test_summary_shows_pack_names_not_codes():
    from core.ai.summary import template_summary

    s = template_summary({"ga": "30+1", "risk_groups": ["M-THA", "P-SINHNON"], "level": "yellow"})
    assert "P-SINHNON" not in s and "sinh non" in s.lower()
