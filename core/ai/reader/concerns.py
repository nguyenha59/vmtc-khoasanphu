"""Bước 3 – mối lo ngoài danh mục (`unmapped_concerns` của AI) và dấu hiệu tự kể chưa phân loại.

Mối lo chỉ được giữ khi bám chữ thật trong văn bản, không bị phủ định, không phải của người khác / đã hết,
không phải câu bình thường hay thông tin đọc được, và đa số lần chạy AI cùng nêu. Có mối lo → tối thiểu vàng.
"""

from __future__ import annotations

import re
from typing import Any

from core.ai.clinical_facts import assertion_content_start, make_fact
from core.ai.reader.lexicon import concepts, lexicon_findings, match_all
from core.ai.reader.text import clauses, evidence_in, grounded, has_plain, tokens_plain
from core.config import Rules, load_rules
from core.textutil import deaccent, nfc


def benign_concern(concern: str, text: str, cap: dict[str, Any]) -> bool:
    """Mối lo chỉ là câu bình thường ("bé đạp đều", "giờ khỏi rồi") hoặc là thông tin đọc được, không phải triệu chứng."""
    if not cap.get("enabled"):
        return False
    tokens = tokens_plain(concern)
    phrases = [p for p in cap.get("benign_concern_phrases", []) if has_plain(concern, [p])]
    if phrases:
        covered = {t for p in phrases for t in tokens_plain(p)}
        if sum(1 for t in tokens if t not in covered) <= int(cap.get("max_leftover_tokens", 1)):
            return True
    fragment = set(tokens_plain(concern))
    scored = [(len(fragment & set(tokens_plain(clause))), clause) for _offset, clause in clauses(text)]
    best = max((score for score, _clause in scored), default=0)
    matches = [clause for score, clause in scored if score == best and score > 0]
    return bool(matches) and all(has_plain(clause, cap.get("info_context_phrases", [])) for clause in matches)


def concern_source_facts(concern: str, text: str, rules: Rules) -> list:
    """Xét mọi lần nhắc nguyên văn, không để lời của người thân che lời của khách."""
    words = nfc(concern).split()
    if not words:
        return []
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    parts = clauses(text)
    facts = []
    for index, (_offset, clause) in enumerate(parts):
        nxt = parts[index + 1][1] if index + 1 < len(parts) else ""
        if nxt and match_all(nxt, concepts(rules)):
            nxt = ""
        for match in pattern.finditer(clause):
            content_start = match.start() + assertion_content_start(match.group())
            facts.append(make_fact("UNMAPPED", clause, content_start, match.end(), next_clause=nxt))
    return facts


def covered_brushing_concern(concern: str, text: str, rules: Rules) -> bool:
    """Chỉ bỏ phần lặp lại một mô tả chân răng khi đánh răng đã được loại theo vị trí.

    Không dùng phủ định từ khóa toàn câu; từ chỉ nặng, kéo dài, vị trí khác hoặc triệu chứng
    mới đều nằm ngoài tập từ cho phép và vẫn được giữ để người xem.
    """
    if not rules.get("form_cap.enabled", False):
        return False
    plain = deaccent(concern).lower()
    if (not evidence_in(concern, text) or not re.search(r"\b(?:danh|chai) rang\b", plain)
            or not re.search(r"\b(?:chan rang|nuou|loi rang)\b", plain)):
        return False
    allowed = {"em", "toi", "minh", "bi", "co", "chi", "hoi", "it", "mot", "chut", "ti", "khi", "luc",
               "chay", "ri", "dinh", "thay", "mau", "chan", "rang", "nuou", "loi", "danh", "chai"}
    if not set(tokens_plain(concern)) <= allowed:
        return False
    facts = lexicon_findings(concern, rules=rules)
    return bool(facts) and all(f["concept"] == "BLEEDING" and f.get("assertion_status") == "other_site"
                              and f.get("anatomy") == "non_genital" for f in facts)


def valid_concern(concern: str, text: str, rules: Rules | None = None) -> bool:
    """Mối lo ngoài danh mục hợp lệ: bám văn bản, không bị phủ định, không phải của người khác, không phải câu bình thường."""
    concern = nfc(concern or "").strip()
    if not concern or not grounded(concern, text):
        return False
    # Không thực hiện được một chức năng là một mối lo, khác với phủ định có triệu chứng.
    if re.search(r"\b(?:khong|ko|chua)(?:\s+\w+){0,5}\s+(?:duoc|noi)\b", deaccent(concern).lower()):
        return True
    facts = concern_source_facts(concern, text, rules or load_rules())
    if facts and all(not fact.present or fact.experiencer == "other" or fact.temporality == "past_resolved"
                     for fact in facts):
        return False
    cap = (rules or load_rules()).get("form_cap", {}) or {}
    if benign_concern(concern, text, cap):
        return False
    if covered_brushing_concern(concern, text, rules or load_rules()):
        return False
    # Khi không có trích dẫn nguyên văn để xác minh phạm vi, giữ mối lo thay vì suy đoán phủ định/người khác.
    return True


def vote_unmapped(runs: list[list[str]], text: str, rules: Rules | None = None) -> list[str]:
    """Mối lo ngoài danh mục: lọc từng lần chạy, rồi chỉ giữ khi > 1/2 số lần chạy có mối lo hợp lệ."""
    rules = rules or load_rules()
    valid_runs = [[u for u in run if valid_concern(u, text, rules)] for run in runs]
    if sum(1 for r in valid_runs if r) * 2 <= len(runs):
        return []
    out: list[str] = []
    for r in valid_runs:
        out += [u for u in r if u not in out]
    return out


def unresolved_text_concerns(text: str, rules: Rules) -> list[str]:
    """Một dấu hiệu tự kể chưa đủ vị trí/ý nghĩa không được xác nhận Xanh chỉ vì LLM trả rỗng."""
    cues = re.compile(r"(?<!\w)(?:mệt|khó chịu|bất thường|không ổn|lạ trong người|tê bì|đau)(?!\w)", re.IGNORECASE)
    plain_cues = re.compile(r"(?<!\w)(?:met|kho chiu|bat thuong|khong on|la trong nguoi|te bi|dau)(?!\w)")
    out = []
    for _offset, clause in clauses(text):
        known = match_all(clause, concepts(rules))
        # Bản bỏ dấu 1-1 sau NFC; không ghép vị trí từ chuỗi có độ dài khác.
        hay = deaccent(clause).lower()
        matches = list(plain_cues.finditer(hay)) if len(hay) == len(clause) else list(cues.finditer(clause))
        for match in matches:
            if any(start <= match.start() and match.end() <= end for _code, start, end in known):
                continue
            fact = make_fact("UNRESOLVED", clause, match.start(), match.end())
            if fact.present and fact.experiencer == "patient" and fact.temporality != "past_resolved":
                out.append(clause.strip()[:120])
    return list(dict.fromkeys(out))
