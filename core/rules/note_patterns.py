"""Regex đọc ghi chú phần quy tắc: giờ đến ghi rõ, tuần thai."""

from __future__ import annotations

import re
from datetime import time

from core.textutil import deaccent, nfc

_EXPLICIT_RE = re.compile(
    r"(?:den|toi|co mat|luc|vao)\s*(?:luc\s*)?(\d{1,2})\s*(?:h|g|gio|:)\s*(\d{2})?(?![\d])",
    re.IGNORECASE,
)
_ANY_TIME_RE = re.compile(r"(?<![\d.,])(\d{1,2})\s*(?:h|g|gio|:)\s*(\d{2})?(?![\d])", re.IGNORECASE)
_GA_WD_RE = re.compile(r"(\d{1,2})\s*(?:w|tuan)\s*(\d)\s*(?:d|ngay)\b", re.IGNORECASE)
_GA_PLUS_RE = re.compile(r"(?<![\d:])(\d{1,2})\s*\+\s*(\d)(?!\d)")
_GA_W_RE = re.compile(r"(\d{1,2})\s*(?:w|tuan)\b", re.IGNORECASE)


def find_explicit_time(note: str) -> time | None:
    """Tìm giờ đến ghi rõ, ví dụ 'đến 7h30', 'có mặt lúc 7:30'."""
    text = deaccent(nfc(note)).lower()
    m = _EXPLICIT_RE.search(text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return time(hh, mm)


def note_clock_times(note: str) -> set[tuple[int, int]]:
    """Mọi giờ dạng 7h30 / 7g / 7:30 / 15 gio trong ghi chú, quy về (giờ % 12, phút) để so sáng/chiều."""
    text = deaccent(nfc(note)).lower()
    out: set[tuple[int, int]] = set()
    for m in _ANY_TIME_RE.finditer(text):
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            out.add((hh % 12, mm))
    return out


def time_in_note(value: str, note: str) -> bool:
    """Giờ 'HH:MM' do AI trả về có thật sự xuất hiện trong ghi chú không (chống bịa / chép giờ hẹn)."""
    try:
        hh, mm = (int(x) for x in value.split(":"))
    except ValueError:
        return False
    return (hh % 12, mm) in note_clock_times(note)


def find_ga(note: str) -> tuple[int, int | None] | None:
    """Tìm tuần thai: '32w', 'tt 24 tuan 3 ngay', 'TT 24+3'. Trả (tuần, ngày|None)."""
    text = deaccent(nfc(note)).lower()
    for rx, has_days in ((_GA_WD_RE, True), (_GA_PLUS_RE, True), (_GA_W_RE, False)):
        m = rx.search(text)
        if m:
            w = int(m.group(1))
            d = int(m.group(2)) if has_days else None
            if 4 <= w <= 42 and (d is None or 0 <= d <= 6):
                return w, d
    return None
