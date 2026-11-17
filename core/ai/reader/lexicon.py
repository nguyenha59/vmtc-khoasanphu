"""Bước 1 – danh mục khái niệm (rules.yaml symptom_concepts) + luật khẳng định trên câu gốc. Không dùng AI.

Trả phát hiện cùng dạng đầu ra AI: concept, present, temporality, experiencer, severity, evidence (+ vị trí).
Phân tích phủ định / người khác / thời gian nằm ở core/ai/clinical_facts.py.
"""

from __future__ import annotations

import re
from typing import Any

from core.ai.clinical_facts import make_fact, using_rules
from core.ai.reader.text import clauses
from core.config import Rules, load_rules
from core.levels import Level, max_level
from core.textutil import deaccent, nfc, no_diacritic_ratio

SEVERITY_ORDER = {"mild": 0, "unknown": 1, "severe": 2}

# Từ chỉ mức độ / lượng / trợ từ / đại từ hay chen giữa cụm triệu chứng: "mặt HƠI sưng", "ra ÍT máu",
# "ra MỘT CHÚT máu", "mặt EM sưng". Tối đa 2 từ chen (MAX_FILLERS). Chỉ làm bắt được NHIỀU hơn, không hạ mức.
FILLERS = ["hơi", "bị", "rất", "khá", "hay", "cũng", "thấy", "ít", "chút", "tí", "một", "nhiều", "có", "em", "chị",
           "tôi", "mình", "hoi", "bi", "rat", "kha", "cung", "thay", "it", "chut", "ti", "mot", "nhieu", "co", "chi",
           "toi", "minh"]
MAX_FILLERS = 2


def concepts(rules: Rules | None = None) -> dict[str, dict[str, Any]]:
    return (rules or load_rules())["symptom_concepts"]


def syn_pattern(syn: str, plain: bool) -> str:
    """Mẫu khớp từ đồng nghĩa, cho phép tối đa MAX_FILLERS từ chen giữa các từ."""
    words = syn.split()
    fillers = sorted({deaccent(f) for f in FILLERS} if plain else set(FILLERS), key=len, reverse=True)
    gap = rf"\s+(?:(?:{'|'.join(map(re.escape, fillers))})\s+){{0,{MAX_FILLERS}}}"
    return r"(?<!\w)" + gap.join(map(re.escape, words)) + r"(?!\w)"


def match_all(text: str, catalog: dict[str, dict[str, Any]]) -> list[tuple[str, int, int]]:
    """Tìm (mã khái niệm, start, end) trong văn bản theo từ đồng nghĩa."""
    found = []
    plain_mode = no_diacritic_ratio(text) > 0.7
    hay = deaccent(text).lower() if plain_mode else text.lower()
    for code, c in catalog.items():
        syns = c.get("synonyms", [])
        if plain_mode:
            syns = [deaccent(s) for s in syns]
        for s in sorted(set(syns), key=len, reverse=True):
            for m in re.finditer(syn_pattern(s.lower(), plain_mode), hay):
                found.append((code, m.start(), m.end()))
    return found


def safety_key(f: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Thứ tự 'an toàn hơn': có mặt > không; bản thân > người khác; hiện tại > đã qua; khẳng định > thông tin
    đọc/nghe được ("đọc trên mạng thấy ra máu…, giờ em đang ra máu" → lấy lần khẳng định); nặng > nhẹ."""
    return (int(bool(f["present"])), int(f["experiencer"] == "patient"),
            int(f["temporality"] != "past_resolved"), int(f.get("assertion_status", "affirmed") == "affirmed"),
            SEVERITY_ORDER.get(f["severity"], 1))


def effective(f: dict[str, Any]) -> bool:
    """Triệu chứng của thai phụ, đang có, được khẳng định."""
    return (bool(f["present"]) and f["experiencer"] == "patient" and f["temporality"] != "past_resolved"
            and f.get("assertion_status", "affirmed") == "affirmed")


def lexicon_findings(text: str, apply_assertions: bool = True, rules: Rules | None = None) -> list[dict[str, Any]]:
    """Dò khái niệm theo danh mục + luật khẳng định. Trả dạng giống đầu ra LLM.
    Mọi cấu hình (danh mục, người thân `experiencer`) lấy từ `rules` nơi gọi truyền vào."""
    with using_rules(rules):
        return _lexicon_findings(text, apply_assertions, rules)


def _lexicon_findings(text: str, apply_assertions: bool, rules: Rules | None) -> list[dict[str, Any]]:
    catalog = concepts(rules)
    text = nfc(text or "")
    parts = clauses(text)
    best: dict[str, dict[str, Any]] = {}
    previous_clause_fact = None
    previous_clause_end = 0
    for idx, (cstart, clause) in enumerate(parts):
        # Tối đa 2 mệnh đề kế tiếp trong CÙNG câu, không chứa khái niệm khác:
        # "ra máu tuần trước, đã đi khám rồi, giờ hết hẳn" – "giờ hết" nằm ở mệnh đề thứ hai.
        following = []
        prev_end = cstart + len(clause)
        for nstart, nclause in parts[idx + 1: idx + 3]:
            if match_all(nclause, catalog) or re.search(r"[.;!?\n]", text[prev_end:nstart]):
                break
            following.append(nclause)
            prev_end = nstart + len(nclause)
        nxt = " ".join(following)
        mentions: list[tuple[str, int, int]] = []
        for code, s, e in sorted(set(match_all(clause, catalog)), key=lambda m: (m[1], -(m[2] - m[1]))):
            if mentions and s < mentions[-1][2]:
                continue
            mentions.append((code, s, e))
        previous = None
        previous_end = 0
        separator = text[previous_clause_end:cstart]
        inherited = (previous_clause_fact.experiencer if previous_clause_fact is not None
                     and not re.search(r"[.;!?\n]", separator) else None)
        for mi, (code, s, e) in enumerate(mentions):
            c = catalog[code]
            fact = make_fact(code, clause, s, e, offset=cstart, next_clause=nxt,
                             negation_allowed=c.get("negation_allowed", True),
                             severe_words=c.get("severe_words"), previous=previous,
                             previous_end=previous_end, inherited_subject=inherited,
                             next_start=mentions[mi + 1][1] if mi + 1 < len(mentions) else None)
            previous, previous_end = fact, e
            f = fact.finding()
            if not apply_assertions:
                f.update(present=True, temporality="current", experiencer="patient", assertion_status="affirmed")
            prev = best.get(code)
            if prev is None or safety_key(f) > safety_key(prev):
                best[code] = f
        previous_clause_fact = previous
        previous_clause_end = cstart + len(clause)
    return list(best.values())


def finding_level(f: dict[str, Any], ga_weeks: int | None, rules: Rules) -> Level:
    """Mức của một phát hiện theo bảng khái niệm (docs/05 mục 3 "Ra mức")."""
    c = concepts(rules).get(f["concept"])
    if f["experiencer"] == "other":
        return Level.GREEN
    if not f["present"]:
        # "Không có, đã qua" của dấu hiệu nguy hiểm (past_counts_yellow: ra máu, thai ít đạp, co giật, ngất, tự hại)
        # = từng có nay hết → vẫn Vàng để hỏi lại tiền sử, như khi ghi "đã hết" (docs/05 mục 3 "Ra mức").
        # Phủ định của bộ đọc nguồn chỉ biểu thị vắng mặt; bước gộp vẫn chuyển người
        # xem nếu chưa rõ tiền sử, thay vì biến phủ định thành khẳng định từng có.
        if (f["temporality"] == "past_resolved" and f.get("assertion_status") != "negated"
                and c is not None and c.get("past_counts_yellow")):
            return Level.YELLOW
        return Level.GREEN
    if c is None:  # khái niệm ngoài danh mục → coi như mối lo chưa phân loại
        return Level.YELLOW
    if f["temporality"] == "past_resolved":
        return Level.YELLOW if c.get("past_counts_yellow") else Level.GREEN
    lvl = Level.parse(c["level"])
    if c.get("level_if_severe") and f.get("severity") == "severe":
        lvl = max_level(lvl, c["level_if_severe"])
    if c.get("red_only_before_term") and ga_weeks is not None:
        th = rules["thresholds"]
        if ga_weeks >= th.get("contractions_red_before_ga_weeks", 37):
            lvl = Level.parse(th.get("contractions_level_at_term", "yellow"))
    if f.get("assertion_status") == "uncertain":  # thông tin đọc / nghe được – Vàng để gọi xác minh, không Xanh
        lvl = Level.YELLOW
    return lvl
