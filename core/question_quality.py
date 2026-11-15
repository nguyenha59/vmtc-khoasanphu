"""Cổng chất lượng câu hỏi (docs/12): câu trả lời phải nhận ra được vấn đề của từng thai phụ.

Hàm thuần: cấu hình form → danh sách lỗi. Tiêu chí (từ mơ hồ, thuật ngữ, câu đảo chiều) nằm trong
config/forms.yaml mục `quality`, không viết cứng. Mã lỗi:

- NO_TRIAGE: lớp quy tắc không phân mức được câu trả lời → trả lời "Có" cũng không ai biết là vấn đề gì.
- REVERSED: trả lời "Có" = tốt (ngược chiều các câu khác) → dễ trả lời nhầm.
- JARGON: thuật ngữ chuyên môn → thai phụ phải tự chẩn đoán.
- VAGUE: từ chỉ mức độ (nhiều, dữ dội…) không có mốc / ví dụ cụ thể → người nhẹ trả lời "Không", bị bỏ sót.
- MULTI (cảnh báo): câu gộp nhiều ý – chấp nhận khi các ý cùng một mức xử trí, người duyệt cần xem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.config import FormsConfig
from core.rules.triage_rules import TRIAGED_NUMBER_PREFIXES, TRIAGED_YESNO

ISSUE_VI = {
    "NO_TRIAGE": "Quy tắc không phân mức được câu trả lời",
    "REVERSED": "Trả lời 'Có' lại là tốt (ngược chiều) – dễ trả lời nhầm",
    "JARGON": "Có thuật ngữ chuyên môn",
    "VAGUE": "Từ chỉ mức độ không có mốc đo / ví dụ",
    "MULTI": "Gộp nhiều ý trong một câu",
}


@dataclass(frozen=True)
class QuestionCheck:
    code: str
    text: str
    issues: tuple[str, ...] = ()
    blocking: tuple[str, ...] = ()
    details: tuple[str, ...] = field(default=())

    @property
    def passed(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "text": self.text, "passed": self.passed, "issues": list(self.issues),
                "blocking": list(self.blocking), "details": list(self.details)}


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", text.lower()) is not None


def check_question(code: str, q: dict[str, Any], quality: dict[str, Any]) -> QuestionCheck:
    text = str(q.get("text", ""))
    low = text.lower()
    issues: list[str] = []
    details: list[str] = []
    qtype = q.get("type")
    if qtype == "yesno" and code not in TRIAGED_YESNO:
        issues.append("NO_TRIAGE")
    elif qtype == "number" and not code.startswith(TRIAGED_NUMBER_PREFIXES):
        issues.append("NO_TRIAGE")
    if any(p.lower() in low for p in quality.get("reversed_patterns", [])):
        issues.append("REVERSED")
    jar = [w for w in quality.get("jargon", []) if _has_word(text, w)]
    if jar:
        issues.append("JARGON")
        details.append("Thuật ngữ: " + ", ".join(jar))
    # Ví dụ / mốc trong ngoặc "(…)" làm rõ mức độ → không tính là mơ hồ.
    vague = [w for w in quality.get("vague_words", []) if _has_word(text, w)]
    if vague and "(" not in text:
        issues.append("VAGUE")
        details.append("Từ mơ hồ: " + ", ".join(vague))
    if qtype == "yesno" and (" hoặc " in low or low.count(",") >= 1):
        issues.append("MULTI")
    block = set(quality.get("block", []))
    return QuestionCheck(code=code, text=text, issues=tuple(issues),
                         blocking=tuple(i for i in issues if i in block), details=tuple(details))


def check_all(forms_cfg: FormsConfig) -> dict[str, QuestionCheck]:
    return {c: check_question(c, q, forms_cfg.quality) for c, q in forms_cfg.questions.items()}


def passing_questions(codes: list[str], forms_cfg: FormsConfig) -> tuple[list[str], list[QuestionCheck]]:
    """Tách danh sách câu thành (đạt, bị loại kèm lý do). Giữ thứ tự gốc."""
    ok: list[str] = []
    rejected: list[QuestionCheck] = []
    for c in codes:
        q = forms_cfg.questions.get(c)
        if q is None:
            rejected.append(QuestionCheck(code=c, text="(không có trong forms.yaml)", issues=("NO_TRIAGE",),
                                          blocking=("NO_TRIAGE",)))
            continue
        chk = check_question(c, q, forms_cfg.quality)
        (ok.append(c) if chk.passed else rejected.append(chk))
    return ok, rejected
