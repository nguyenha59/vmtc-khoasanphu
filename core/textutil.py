"""Tiện ích xử lý văn bản tiếng Việt."""

from __future__ import annotations

import re
import unicodedata

_VN_SPECIAL = str.maketrans({"đ": "d", "Đ": "D"})


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def deaccent(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Gọi xác nhận' → 'Goi xac nhan'."""
    t = unicodedata.normalize("NFD", (text or "").translate(_VN_SPECIAL))
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def has_diacritics(text: str) -> bool:
    return deaccent(text) != (text or "")


def no_diacritic_ratio(text: str) -> float:
    """Tỷ lệ từ (chữ cái) không có dấu trong văn bản."""
    words = re.findall(r"[^\W\d_]+", nfc(text))
    if not words:
        return 0.0
    plain = sum(1 for w in words if deaccent(w) == w)
    return plain / len(words)


def word_pattern(keyword: str) -> re.Pattern[str]:
    """Regex khớp nguyên từ, không phân biệt hoa thường."""
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", re.IGNORECASE)


def contains_keyword(text: str, keyword: str) -> bool:
    """Khớp từ khóa; nếu văn bản hoàn toàn không dấu thì so cả dạng không dấu."""
    text = nfc(text)
    if not keyword.strip("?").strip():  # từ khóa ký hiệu như "?"
        return keyword in text
    if word_pattern(nfc(keyword)).search(text):
        return True
    if not has_diacritics(text):
        return bool(word_pattern(deaccent(keyword)).search(text))
    return False
