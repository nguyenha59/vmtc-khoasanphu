"""Hàm nhỏ trên văn bản dùng chung cho các bước đọc tự viết (hàm thuần)."""

from __future__ import annotations

import re
from typing import Any

from core.textutil import deaccent, nfc, no_diacritic_ratio

CLAUSE_SPLIT = re.compile(r"[,.;!?\n]+|\s(?:nhưng|nhung|tuy nhiên|tuy nhien)\s", re.IGNORECASE)


def clauses(text: str) -> list[tuple[int, str]]:
    """Mệnh đề (vị trí bắt đầu, nội dung), tách theo dấu câu và "nhưng / tuy nhiên"."""
    out, pos = [], 0
    for m in CLAUSE_SPLIT.finditer(text):
        out.append((pos, text[pos : m.start()]))
        pos = m.end()
    out.append((pos, text[pos:]))
    return [(s, c) for s, c in out if c.strip()]


def clause_index(text: str, start: int, end: int) -> int | None:
    """Chỉ số mệnh đề chứa trọn [start, end)."""
    return next((i for i, (off, c) in enumerate(clauses(text)) if off <= start and end <= off + len(c)), None)


def has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.IGNORECASE) for w in words)


def has_plain(text: str, phrases: list[str]) -> bool:
    """Khớp cụm từ không phân biệt dấu (cả hai phía bỏ dấu)."""
    return has(deaccent(text).lower(), [deaccent(p).lower() for p in phrases])


def has_words(text: str, words: list[str], plain_ambiguous: list[str] | None = None) -> bool:
    """Khớp từ ĐÚNG DẤU khi văn bản có dấu ("đâu" ≠ "đau", "đỡ" ≠ "đỏ", "hồi" ≠ "hơi").

    Văn bản gần như không dấu → so bản bỏ dấu, nhưng bỏ các từ mà bản không dấu trùng từ khác (`plain_ambiguous`).
    Dùng cho dấu hiệu để AI được HẠ mức – thà không khớp (giữ mức danh mục) còn hơn khớp nhầm.
    """
    if no_diacritic_ratio(text) > 0.7:
        skip = {deaccent(w).lower() for w in plain_ambiguous or []}
        return has_plain(text, [w for w in words if deaccent(w).lower() not in skip])
    return has(nfc(text).lower(), [nfc(w).lower() for w in words])


def tokens_plain(text: str) -> list[str]:
    return re.findall(r"\w+", deaccent(text).lower())


def evidence_in(evidence: str, text: str) -> bool:
    """Trích dẫn trọn vẹn có trong văn bản (chỉ cho khác hoa/thường và khoảng trắng)."""
    words = nfc(evidence or "").strip().split()
    return bool(words) and re.search(r"\s+".join(re.escape(w) for w in words), text, re.IGNORECASE) is not None


def grounded(concern: str, text: str, min_ratio: float = 0.6) -> bool:
    """Mối lo phải bám chữ thật trong văn bản (chống chép ví dụ trong prompt / lời giải thích)."""
    c_tok = [w for w in re.findall(r"\w+", deaccent(concern).lower()) if len(w) >= 2]
    if not c_tok:
        return False
    t_tok = set(re.findall(r"\w+", deaccent(text).lower()))
    return sum(w in t_tok for w in c_tok) / len(c_tok) >= min_ratio


def source_clause(fragment: str, text: str) -> str:
    """Mệnh đề trong văn bản gốc trùng nhiều từ nhất với fragment (evidence / mối lo); '' nếu không trùng."""
    f_tok = set(tokens_plain(fragment))
    best, best_n = "", 0
    for _s, clause in clauses(text):
        n = len(f_tok & set(tokens_plain(clause)))
        if n > best_n:
            best, best_n = clause, n
    return best


def only_at(llm_f: dict[str, Any], lex_f: dict[str, Any], text: str) -> bool:
    """Trích dẫn của AI có trong văn bản và MỌI lần xuất hiện đều đè lên chỗ danh mục đọc (chặt hơn same_mention:
    trích dẫn ngắn lặp lại như "ra máu" ở hai câu khác nhau không được coi là cùng chỗ). Dùng khi AI được HẠ mức."""
    words = nfc(str(llm_f.get("evidence", ""))).split()
    start, end = lex_f.get("evidence_start"), lex_f.get("evidence_end")
    if not words or start is None or end is None:
        return False
    hits = list(re.finditer(r"\s+".join(map(re.escape, words)), text, re.IGNORECASE))
    return bool(hits) and all(m.start() < end and start < m.end() for m in hits)


def same_mention(llm_f: dict[str, Any], lex_f: dict[str, Any], text: str) -> bool:
    """Bằng chứng của AI nằm ĐÈ lên chỗ danh mục đã đọc trong câu gốc?

    Không → AI dựa vào một cụm KHÁC (vd "quần lót có vệt nâu") mà danh mục không có từ → phủ định / vị trí khác của
    danh mục ("không ra máu") không được xóa phát hiện đó. Không xác định được vị trí → coi là cùng chỗ.
    """
    words = nfc(str(llm_f.get("evidence", ""))).split()
    start, end = lex_f.get("evidence_start"), lex_f.get("evidence_end")
    if not words or start is None or end is None:
        return True
    hits = list(re.finditer(r"\s+".join(map(re.escape, words)), text, re.IGNORECASE))
    if not hits:
        return True
    return any(m.start() < end and start < m.end() for m in hits)
