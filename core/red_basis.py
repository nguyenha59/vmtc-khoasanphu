"""Căn cứ của một lý do cảnh báo: quy tắc / khái niệm → nguồn y khoa (config red_basis, knowledge/sources/y-khoa.md mục 1).

Mọi cảnh báo ĐỎ phải giải thích được: dữ liệu nào (câu trả lời / câu tự viết) → quy tắc nào → nguồn nào.
Hàm thuần, chỉ đọc cấu hình.
"""

from __future__ import annotations

import re
from typing import Any

from core.config import Rules

_PREFIXES = ("TEXT_", "BP_AM_", "BP_PM_")


def _source_codes_from_meaning(meaning: str) -> list[str]:
    m = re.search(r"\(((?:S\d)(?:,\s*S\d)*)\)\s*$", meaning or "")
    return [x.strip() for x in m.group(1).split(",")] if m else []


def basis_codes(kind: str, code: str, rules: Rules) -> list[str]:
    """kind: rule (mã câu / luật lớp 1) | concept (khái niệm phần tự viết) | combo (text_combos)."""
    cfg = rules.get("red_basis") or {}
    if kind == "concept":
        override = (cfg.get("concepts") or {}).get(code)
        if override:
            return list(override)
        return _source_codes_from_meaning((rules.get("symptom_concepts") or {}).get(code, {}).get("meaning", ""))
    for c in rules.get("text_combos") or []:
        if c.get("code") == code:
            return list(c.get("basis") or [])
    bare = code
    for p in _PREFIXES:
        if bare.startswith(p):
            bare = bare[len(p):]
    rule_map = cfg.get("rules") or {}
    if code.startswith("TXT_") and code.endswith("_BP"):  # triệu chứng tự viết kèm HA cao (red_with_bp)
        return list(rule_map.get("TXT_BP") or [])
    if bare.endswith("_IMPLAUSIBLE"):
        return list(rule_map.get("IMPLAUSIBLE") or [])
    return list(rule_map.get(code) or rule_map.get(bare) or [])


def basis_text(codes: list[str], rules: Rules) -> str:
    names = (rules.get("red_basis") or {}).get("sources") or {}
    return " · ".join(f"{names.get(c, c)} ({c})" if re.fullmatch(r"S\d", c) and c in names else names.get(c, c)
                      for c in codes)


def explain(kind: str, code: str, rules: Rules) -> dict[str, Any]:
    codes = basis_codes(kind, code, rules)
    return {"basis_codes": codes, "basis": basis_text(codes, rules)}
