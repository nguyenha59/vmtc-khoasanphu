"""Lớp 3 – AI đọc phần tự viết của form (docs/05 mục 3).

LLM (self-consistency 3 lần) + luật khẳng định (phủ định / thời gian / người trải qua) chạy trên câu gốc.
Độ nhất quán chuẩn hóa các khác biệt biểu diễn không có hiệu lực chỉ khi câu gốc xác nhận.
Mâu thuẫn giữa LLM và luật → lấy hướng an toàn hơn và hạ độ tin cậy. Danh mục khái niệm cũng được
dò trực tiếp trên văn bản để không bỏ sót khi LLM bỏ qua (nâng mức). Ngoại lệ duy nhất: `form_cap`
(phương án A) – danh mục chắc chắn người khác / đã hết / bé đạp bình thường → finding đỏ của LLM tối đa vàng.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core.ai.clinical_facts import assertion_content_start, assertion_context, make_fact
from core.ai.consistency import run_votes
from core.ai.llm_client import LlmClient, render_prompt
from core.ai.masking import NerFn, mask_fragment, mask_text
from core.config import Rules, load_rules, prompt_text
from core.levels import Level, max_level
from core.textutil import deaccent, nfc, no_diacritic_ratio

FORM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "concept": {"type": "string"},
                    "present": {"type": "boolean"},
                    "temporality": {"enum": ["current", "past_resolved", "unknown"]},
                    "experiencer": {"enum": ["patient", "other"]},
                    "severity": {"enum": ["mild", "severe", "unknown"]},
                    "evidence": {"type": "string", "maxLength": 120},
                },
                "required": ["concept", "present", "temporality", "experiencer", "severity", "evidence"],
            },
        },
        "unmapped_concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings", "unmapped_concerns"],
}

CLAUSE_SPLIT = re.compile(r"[,.;!?\n]+|\s(?:nhưng|nhung|tuy nhiên|tuy nhien)\s", re.IGNORECASE)
NEGATORS = {"không", "chưa", "ko", "k", "khong", "chua", "hết", "het", "chẳng", "chang", "chả", "cha"}
CONTINUE = ["đến giờ", "tới giờ", "đến nay", "vẫn", "từ", "den gio", "toi gio", "den nay", "van", "tu"]
PAST = ["hôm qua", "hôm kia", "tuần trước", "hôm trước", "tháng trước", "lúc trước", "hom qua", "hom kia",
        "tuan truoc", "hom truoc", "thang truoc"]
RESOLVED = ["đã hết", "giờ hết", "hết rồi", "khỏi rồi", "đỡ rồi", "da het", "gio het", "het roi",
            "khoi roi", "do roi"]
OTHERS = ["mẹ chồng", "mẹ em", "mẹ tôi", "mẹ mình", "chồng", "chị gái", "em gái", "con lớn", "bé lớn", "bố",
          "con gái", "con trai", "ông nội", "ông ngoại", "bà nội", "bà ngoại", "chị dâu", "em dâu", "anh trai",
          "bạn em", "hàng xóm", "me chong", "me em", "me toi", "chong", "chi gai", "em gai", "con lon", "ban em",
          "con gai", "con trai", "ong noi", "ong ngoai", "ba noi", "ba ngoai", "chi dau", "anh trai", "hang xom"]
# Thai phụ tự xưng: xuất hiện SAU người khác (không tính đại từ sở hữu liền sau, "chồng em") → triệu chứng của thai phụ.
SELF = {"em", "tôi", "mình", "toi", "minh"}
SEVERITY_ORDER = {"mild": 0, "unknown": 1, "severe": 2}


@dataclass
class ReadFinding:
    concept: str
    present: bool
    temporality: str
    experiencer: str
    severity: str
    evidence: str
    source: str = "llm"
    level: str = "green"
    assertion_status: str = "affirmed"
    anatomy: str | None = None
    evidence_start: int | None = None
    evidence_end: int | None = None


@dataclass
class FormReading:
    level: Level
    ok: bool
    confidence: float
    findings: list[ReadFinding] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    no_diacritics: bool = False
    masked_text: str = ""
    mask_ok: bool = True
    conflicts: list[str] = field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- luật khẳng định
def _has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text, re.IGNORECASE) for w in words)


def _clauses(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for m in CLAUSE_SPLIT.finditer(text):
        out.append((pos, text[pos : m.start()]))
        pos = m.end()
    out.append((pos, text[pos:]))
    return [(s, c) for s, c in out if c.strip()]


# Từ được phép chen giữa từ phủ định và triệu chứng: "không BỊ ra máu", "chưa THẤY đạp", "không CÒN ra nước".
NEG_BRIDGE = {"bị", "có", "thấy", "còn", "hề", "bi", "co", "thay", "con", "he"}
# Từ được phép chen giữa người khác và triệu chứng: "chồng em BỊ khó thở", "mẹ CŨNG HAY đau đầu".
OTHER_BRIDGE = {"bị", "cũng", "có", "đang", "hay", "thì", "lại", "vẫn", "hơi", "rất",
                "bi", "cung", "co", "dang", "thi", "lai", "van", "hoi", "rat"}


def _negated(tokens: list[str]) -> bool:
    """Phủ định chỉ khi từ phủ định đứng NGAY trước triệu chứng (cho chen tối đa 2 từ nối).

    Trước đây: có từ phủ định trong 3 từ trước là đủ → "ko ngủ dc ra máu" bị hiểu là "không ra máu".
    """
    i = len(tokens) - 1
    skipped = 0
    while i >= 0 and tokens[i] in NEG_BRIDGE and skipped < 2:
        i -= 1
        skipped += 1
    return i >= 0 and tokens[i] in NEGATORS


def _other_subject(before: str) -> bool:
    """Người khác bị – chỉ khi từ chỉ người khác ĐI LIỀN triệu chứng ("chồng em khó thở", "mẹ chồng bị ra máu").

    Câu bỏ chủ ngữ ("Chồng đưa đi khám vì ra máu", "Bố mẹ lo lắm vì ra máu") là thai phụ bị → không coi là người khác.
    Sai về phía an toàn: nghi ngờ thì coi là thai phụ (luật này còn dùng để HẠ mức – phương án A).
    """
    last_end = -1
    for w in OTHERS:
        for m in re.finditer(rf"(?<!\w){re.escape(w)}(?!\w)", before, re.IGNORECASE):
            last_end = max(last_end, m.end())
    if last_end < 0:
        return False
    tail = re.findall(r"\w+", before[last_end:].lower())
    if tail and tail[0] in SELF:  # đại từ sở hữu: "chồng em", "mẹ tôi"
        tail = tail[1:]
    return len(tail) <= 3 and all(t in OTHER_BRIDGE for t in tail)


def assertion(clause: str, start: int, end: int, next_clause: str = "", negation_allowed: bool = True) -> dict[str, Any]:
    """Phân loại khẳng định cho khái niệm tại [start, end) trong mệnh đề.

    next_clause: mệnh đề kế tiếp (chỉ truyền khi nó không chứa khái niệm nào), ví dụ "giờ hết rồi".
    """
    return assertion_context(clause, start, end, next_clause=next_clause, negation_allowed=negation_allowed)


def _other_experiencer(before: str) -> bool:
    """Người khác bị, trừ khi sau từ chỉ người khác thai phụ lại tự xưng ("chồng em thấy EM thở hụt hơi")."""
    last_end = -1
    for w in OTHERS:
        for m in re.finditer(rf"(?<!\w){re.escape(w)}(?!\w)", before, re.IGNORECASE):
            last_end = max(last_end, m.end())
    if last_end < 0:
        return False
    tail = re.findall(r"\w+", before[last_end:].lower())
    if tail and tail[0] in SELF:  # đại từ sở hữu: "chồng em", "mẹ tôi"
        tail = tail[1:]
    return not any(tok in SELF for tok in tail)


def _concepts(rules: Rules | None = None) -> dict[str, dict[str, Any]]:
    return (rules or load_rules())["symptom_concepts"]


# Từ chỉ mức độ / lượng / trợ từ / đại từ hay chen giữa cụm triệu chứng: "mặt HƠI sưng", "ra ÍT máu",
# "ra MỘT CHÚT máu", "mặt EM sưng". Tối đa 2 từ chen (MAX_FILLERS). Chỉ làm bắt được NHIỀU hơn, không hạ mức.
FILLERS = ["hơi", "bị", "rất", "khá", "hay", "cũng", "thấy", "ít", "chút", "tí", "một", "nhiều", "có", "em", "chị",
           "tôi", "mình", "hoi", "bi", "rat", "kha", "cung", "thay", "it", "chut", "ti", "mot", "nhieu", "co", "chi",
           "toi", "minh"]
MAX_FILLERS = 2


def _syn_pattern(syn: str, plain: bool) -> str:
    """Mẫu khớp từ đồng nghĩa, cho phép tối đa MAX_FILLERS từ chen giữa các từ."""
    words = syn.split()
    fillers = sorted({deaccent(f) for f in FILLERS} if plain else set(FILLERS), key=len, reverse=True)
    gap = rf"\s+(?:(?:{'|'.join(map(re.escape, fillers))})\s+){{0,{MAX_FILLERS}}}"
    return r"(?<!\w)" + gap.join(map(re.escape, words)) + r"(?!\w)"


def _match_all(text: str, concepts: dict[str, dict[str, Any]]) -> list[tuple[str, int, int]]:
    """Tìm (mã khái niệm, start, end) trong văn bản theo từ đồng nghĩa."""
    found = []
    plain_mode = no_diacritic_ratio(text) > 0.7
    hay = deaccent(text).lower() if plain_mode else text.lower()
    for code, c in concepts.items():
        syns = c.get("synonyms", [])
        if plain_mode:
            syns = [deaccent(s) for s in syns]
        for s in sorted(set(syns), key=len, reverse=True):
            for m in re.finditer(_syn_pattern(s.lower(), plain_mode), hay):
                found.append((code, m.start(), m.end()))
    return found


def lexicon_findings(text: str, apply_assertions: bool = True, rules: Rules | None = None) -> list[dict[str, Any]]:
    """Dò khái niệm theo danh mục + luật khẳng định. Trả dạng giống đầu ra LLM."""
    concepts = _concepts(rules)
    text = nfc(text or "")
    clauses = _clauses(text)
    best: dict[str, dict[str, Any]] = {}
    previous_clause_fact = None
    previous_clause_end = 0
    for idx, (cstart, clause) in enumerate(clauses):
        # Tối đa 2 mệnh đề kế tiếp trong CÙNG câu, không chứa khái niệm khác:
        # "ra máu tuần trước, đã đi khám rồi, giờ hết hẳn" – "giờ hết" nằm ở mệnh đề thứ hai.
        following = []
        prev_end = cstart + len(clause)
        for nstart, nclause in clauses[idx + 1: idx + 3]:
            if _match_all(nclause, concepts) or re.search(r"[.;!?\n]", text[prev_end:nstart]):
                break
            following.append(nclause)
            prev_end = nstart + len(nclause)
        nxt = " ".join(following)
        mentions = []
        for code, s, e in sorted(set(_match_all(clause, concepts)), key=lambda m: (m[1], -(m[2] - m[1]))):
            if mentions and s < mentions[-1][2]:
                continue
            mentions.append((code, s, e))
        previous = None
        previous_end = 0
        separator = text[previous_clause_end:cstart]
        inherited = (previous_clause_fact.experiencer if previous_clause_fact is not None
                     and not re.search(r"[.;!?\n]", separator) else None)
        for mi, (code, s, e) in enumerate(mentions):
            c = concepts[code]
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
            if prev is None or _safety_key(f) > _safety_key(prev):
                best[code] = f
        previous_clause_fact = previous
        previous_clause_end = cstart + len(clause)
    return list(best.values())


def _safety_key(f: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Thứ tự 'an toàn hơn': có mặt > không; hiện tại > đã qua; bản thân > người khác; nặng > nhẹ;
    khẳng định > chỉ là thông tin đọc/nghe được ("đọc trên mạng thấy ra máu…, giờ em đang ra máu" → lấy lần khẳng định)."""
    return (int(bool(f["present"])), int(f["experiencer"] == "patient"),
            int(f["temporality"] != "past_resolved"), int(f.get("assertion_status", "affirmed") == "affirmed"),
            SEVERITY_ORDER.get(f["severity"], 1))


def _same_mention(llm_f: dict[str, Any], lex_f: dict[str, Any], text: str) -> bool:
    """Bằng chứng của LLM nằm ĐÈ lên chỗ danh mục đã đọc trong câu gốc?

    Không → LLM dựa vào một cụm KHÁC (vd "quần lót có vệt nâu") mà danh mục không có từ → phủ định / vị trí khác của
    danh mục ("không ra máu") không được xóa phát hiện đó. Không xác định được vị trí → coi là cùng chỗ (như trước).
    """
    words = nfc(str(llm_f.get("evidence", ""))).split()
    start, end = lex_f.get("evidence_start"), lex_f.get("evidence_end")
    if not words or start is None or end is None:
        return True
    hits = list(re.finditer(r"\s+".join(map(re.escape, words)), text, re.IGNORECASE))
    if not hits:
        return True
    return any(m.start() < end and start < m.end() for m in hits)


def finding_level(f: dict[str, Any], ga_weeks: int | None, rules: Rules) -> Level:
    concepts = _concepts(rules)
    c = concepts.get(f["concept"])
    if not f["present"] or f["experiencer"] == "other":
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


def _effective(f: dict[str, Any]) -> bool:
    return (bool(f["present"]) and f["experiencer"] == "patient" and f["temporality"] != "past_resolved"
            and f.get("assertion_status", "affirmed") == "affirmed")


def _grounded(concern: str, text: str, min_ratio: float = 0.6) -> bool:
    """Mối lo phải bám chữ thật trong văn bản (chống chép ví dụ trong prompt / lời giải thích)."""
    c_tok = [w for w in re.findall(r"\w+", deaccent(concern).lower()) if len(w) >= 2]
    if not c_tok:
        return False
    t_tok = set(re.findall(r"\w+", deaccent(text).lower()))
    return sum(w in t_tok for w in c_tok) / len(c_tok) >= min_ratio


def _has_plain(text: str, phrases: list[str]) -> bool:
    """Khớp cụm từ không phân biệt dấu (cả hai phía bỏ dấu)."""
    return _has(deaccent(text).lower(), [deaccent(p).lower() for p in phrases])


def _tokens_plain(text: str) -> list[str]:
    return re.findall(r"\w+", deaccent(text).lower())


def _source_clause(fragment: str, text: str) -> str:
    """Mệnh đề trong văn bản gốc trùng nhiều từ nhất với fragment (evidence / mối lo); '' nếu không trùng."""
    f_tok = set(_tokens_plain(fragment))
    best, best_n = "", 0
    for _s, clause in _clauses(text):
        n = len(f_tok & set(_tokens_plain(clause)))
        if n > best_n:
            best, best_n = clause, n
    return best


def _benign_concern(concern: str, text: str, cap: dict[str, Any]) -> bool:
    """Mối lo chỉ là câu bình thường ("bé đạp đều", "giờ khỏi rồi") hoặc là thông tin đọc được, không phải triệu chứng."""
    if not cap.get("enabled"):
        return False
    tokens = _tokens_plain(concern)
    phrases = [p for p in cap.get("benign_concern_phrases", []) if _has_plain(concern, [p])]
    if phrases:
        covered = {t for p in phrases for t in _tokens_plain(p)}
        if sum(1 for t in tokens if t not in covered) <= int(cap.get("max_leftover_tokens", 1)):
            return True
    fragment = set(_tokens_plain(concern))
    clauses = [(len(fragment & set(_tokens_plain(clause))), clause) for _offset, clause in _clauses(text)]
    best = max((score for score, _clause in clauses), default=0)
    matches = [clause for score, clause in clauses if score == best and score > 0]
    return bool(matches) and all(_has_plain(clause, cap.get("info_context_phrases", [])) for clause in matches)


def _concern_source_facts(concern: str, text: str, rules: Rules) -> list:
    """Xét mọi lần nhắc nguyên văn, không để lời của người thân che lời của khách."""
    words = nfc(concern).split()
    if not words:
        return []
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    clauses = _clauses(text)
    facts = []
    for index, (_offset, clause) in enumerate(clauses):
        nxt = clauses[index + 1][1] if index + 1 < len(clauses) else ""
        if nxt and _match_all(nxt, _concepts(rules)):
            nxt = ""
        for match in pattern.finditer(clause):
            content_start = match.start() + assertion_content_start(match.group())
            facts.append(make_fact("UNMAPPED", clause, content_start, match.end(), next_clause=nxt))
    return facts


def _valid_concern(concern: str, text: str, rules: Rules | None = None) -> bool:
    """Mối lo ngoài danh mục hợp lệ: bám văn bản, không bị phủ định, không phải của người khác, không phải câu bình thường."""
    concern = nfc(concern or "").strip()
    if not concern or not _grounded(concern, text):
        return False
    # Không thực hiện được một chức năng là một mối lo, khác với phủ định có triệu chứng.
    if re.search(r"\b(?:khong|ko|chua)(?:\s+\w+){0,5}\s+(?:duoc|noi)\b", deaccent(concern).lower()):
        return True
    facts = _concern_source_facts(concern, text, rules or load_rules())
    if facts and all(not fact.present or fact.experiencer == "other" or fact.temporality == "past_resolved"
                     for fact in facts):
        return False
    cap = (rules or load_rules()).get("form_cap", {}) or {}
    if _benign_concern(concern, text, cap):
        return False
    if _covered_brushing_concern(concern, text, rules or load_rules()):
        return False
    # Khi không có trích dẫn nguyên văn để xác minh phạm vi, giữ mối lo thay vì suy đoán phủ định/người khác.
    return True


def _vote_unmapped(runs: list[list[str]], text: str, rules: Rules | None = None) -> list[str]:
    """Mối lo ngoài danh mục: lọc từng lần chạy, rồi chỉ giữ khi > 1/2 số lần chạy có mối lo hợp lệ."""
    rules = rules or load_rules()
    valid_runs = [[u for u in run if _valid_concern(u, text, rules)] for run in runs]
    if sum(1 for r in valid_runs if r) * 2 <= len(runs):
        return []
    out: list[str] = []
    for r in valid_runs:
        out += [u for u in r if u not in out]
    return out


def _majority_merge(runs: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Gộp các lần chạy: mỗi khái niệm theo đa số (có hiệu lực / không).

    Khái niệm có hiệu lực ở > n/2 lần chạy → lấy bản an toàn nhất trong các bản có hiệu lực.
    Ngược lại → lấy bản an toàn nhất trong các bản KHÔNG có hiệu lực (nếu có), để vẫn ghi nhận
    khái niệm đã được nhắc tới (phủ định / người khác / đã qua).
    """
    n = len(runs)
    out: dict[str, dict[str, Any]] = {}
    for concept in {c for r in runs for c in r}:
        versions = [r[concept] for r in runs if concept in r]
        eff = [f for f in versions if _effective(f)]
        if len(eff) * 2 > n:
            out[concept] = max(eff, key=_safety_key)
        else:
            non_eff = [f for f in versions if not _effective(f)]
            if non_eff:
                out[concept] = max(non_eff, key=_safety_key)
            # chỉ có thiểu số "có hiệu lực" và không ai phủ định → không ghi; độ tin cậy đã giảm
    return out


def cap_reason(f: dict[str, Any], lex_f: dict[str, Any] | None, text: str, rules: Rules) -> str | None:
    """Lý do hạ một finding LLM (phương án A, config form_cap); None nếu không được hạ.

    Chỉ hạ khi lớp danh mục chắc chắn: cùng khái niệm được dò thấy trong văn bản và MỌI lần xuất hiện đều là
    người khác / đã hết; hoặc câu evidence chỉ tả bé đạp bình thường và danh mục không thấy khái niệm đó.
    Không hạ khái niệm locked, không hạ khi văn bản có dấu hiệu thai phụ cũng bị / kể lại ("em cũng", "bảo em").
    """
    cap = rules.get("form_cap", {}) or {}
    concept = rules["symptom_concepts"].get(f["concept"])
    if not cap.get("enabled") or concept is None or concept.get("locked") or not _effective(f):
        return None
    if "llm" not in str(f.get("source", "llm")):
        return None
    if _has_plain(text, cap.get("keep_red_markers", [])):
        return None
    if lex_f is not None:
        if lex_f["present"] and not _effective(lex_f):
            return "danh mục: người khác bị" if lex_f["experiencer"] == "other" else "danh mục: đã hết"
        return None
    if f["concept"] in cap.get("normal_fm_concepts", []):
        clause = _source_clause(str(f.get("evidence", "")), text)
        if (clause and _has_plain(clause, cap.get("normal_fm_phrases", []))
                and not _has_plain(clause, cap.get("normal_fm_breakers", []))):
            return "bé đạp bình thường"
    return None


def _ai_only(f: dict[str, Any], lex_f: dict[str, Any] | None, rules: Rules) -> bool:
    """Đỏ chỉ dựa vào AI: lớp danh mục không thấy khái niệm này trong câu (config form_cap.ai_only_red_cap)."""
    cap = rules.get("form_cap", {}) or {}
    concept = rules["symptom_concepts"].get(f["concept"]) or {}
    if not cap.get("enabled") or not cap.get("ai_only_red_cap") or concept.get("locked"):
        return False
    return "llm" in str(f.get("source", "")) and lex_f is None


# ---------------------------------------------------------------- đọc form
def build_prompt(masked: str, ga_weeks: int | None, no_diacritics: bool, rules: Rules) -> tuple[str, str]:
    template, version = prompt_text("form_reader")
    # Ý nghĩa (meaning) giúp AI xếp đúng khái niệm cho cách nói chưa có trong danh mục ("mỏ ác", "thở không ra hơi").
    lines = [f"- {code}: {c['name']}" + (f" – {re.sub(r' [(]S[0-9, S]+[)]$', '', c['meaning'])}" if c.get("meaning") else "")
             + f" (ví dụ cách nói: {', '.join(c['synonyms'][:6])})"
             for code, c in rules["symptom_concepts"].items()]
    template = template.replace("{concepts}", "\n".join(lines))
    payload = {"text": masked, "ga_weeks": ga_weeks, "no_diacritics": no_diacritics}
    return render_prompt(template, payload), version


def _evidence_in(evidence: str, text: str) -> bool:
    """A complete quotation, allowing only casing and whitespace differences."""
    words = nfc(evidence or "").strip().split()
    return bool(words) and re.search(r"\s+".join(re.escape(w) for w in words), text, re.IGNORECASE) is not None


def _covered_brushing_concern(concern: str, text: str, rules: Rules) -> bool:
    """Chỉ bỏ phần lặp lại một mô tả chân răng khi đánh răng đã được loại theo vị trí.

    Không dùng phủ định từ khóa toàn câu; từ chỉ nặng, kéo dài, vị trí khác hoặc triệu chứng
    mới đều nằm ngoài tập từ cho phép và vẫn được giữ để người xem.
    """
    if not rules.get("form_cap.enabled", False):
        return False
    plain = deaccent(concern).lower()
    if (not _evidence_in(concern, text) or not re.search(r"\b(?:danh|chai) rang\b", plain)
            or not re.search(r"\b(?:chan rang|nuou|loi rang)\b", plain)):
        return False
    allowed = {"em", "toi", "minh", "bi", "co", "chi", "hoi", "it", "mot", "chut", "ti", "khi", "luc",
               "chay", "ri", "dinh", "thay", "mau", "chan", "rang", "nuou", "loi", "danh", "chai"}
    if not set(_tokens_plain(concern)) <= allowed:
        return False
    facts = lexicon_findings(concern, rules=rules)
    return bool(facts) and all(f["concept"] == "BLEEDING" and f.get("assertion_status") == "other_site"
                              and f.get("anatomy") == "non_genital" for f in facts)


def _repair_shared_negation(f: dict, text: str, lex: dict | None, rules: Rules) -> dict | None:
    """Khôi phục nguyên mệnh đề nguồn, chỉ khi AI lặp từ phủ định dùng chung.

    Không sửa một khẳng định dương tính, không suy diễn trên các câu khác nhau và không
    chấp nhận bằng chứng bịa. Mọi phiếu gốc vẫn được lưu nguyên vẹn trong raw.outputs.
    """
    if (lex is None or f["present"] or lex.get("assertion_status") != "negated"
            or rules["symptom_concepts"][f["concept"]].get("locked")):
        return None
    start, end = lex.get("evidence_start"), lex.get("evidence_end")
    if start is None or end is None:
        return None
    mention = text[start:end]
    quoted = " ".join(nfc(f.get("evidence", "")).lower().split())
    mention_words = " ".join(mention.lower().split())
    if not quoted.endswith(" " + mention_words):
        return None
    prefix = quoted[: -len(mention_words)].split()
    if (not prefix or prefix[0] not in {"không", "khong", "ko", "k", "chưa", "chua"}
            or len(prefix) > 3 or any(word not in NEG_BRIDGE for word in prefix[1:])):
        return None
    for offset, clause in _clauses(text):
        if offset <= start and end <= offset + len(clause):
            before = deaccent(clause[: start - offset]).lower()
            if len(clause.strip()) <= 120 and re.search(r"\b(?:va|hay|hoac)\b", before):
                return {**f, "evidence": clause.strip()}
    return None


def _confirmed_inactive(f: dict, lex: dict | None, text: str, ga_weeks: int | None, rules: Rules) -> bool:
    """Khác cách ghi một sự kiện không có hiệu lực chỉ tương đương khi nguồn xác nhận."""
    return bool(lex is not None and not rules["symptom_concepts"][f["concept"]].get("locked")
                and not _effective(f) and not _effective(lex)
                and finding_level(lex, ga_weeks, rules) == Level.GREEN
                and _evidence_in(f.get("evidence", ""), text) and _same_mention(f, lex, text))


def _explicit_mild(lex: dict | None, text: str, rules: Rules) -> bool:
    """Từ mức nhẹ phải gắn với đúng lần nhắc, không lấy từ hoạt động / triệu chứng khác."""
    if lex is None or lex.get("severity") != "mild":
        return False
    start, end = lex.get("evidence_start"), lex.get("evidence_end")
    if start is None or end is None:
        return False
    for offset, clause in _clauses(text):
        if offset <= start and end <= offset + len(clause):
            local_start, local_end = start - offset, end - offset
            before = re.split(r"\b(?:va|roi|nhung|vi|nen|kem)\b", deaccent(clause[:local_start]).lower())[-1]
            next_start = min((s for _concept, s, _e in _match_all(clause, _concepts(rules)) if s >= local_end), default=len(clause))
            after = re.split(r"\b(?:va|roi|nhung|vi|nen|kem)\b", deaccent(clause[local_end:next_start]).lower())[0]
            return bool(re.search(r"\b(?:hoi|nhe|thoang qua)\b", " ".join(before.split()[-2:]) + " " + after))
    return False


def _unresolved_text_concerns(text: str, rules: Rules) -> list[str]:
    """Một dấu hiệu tự kể chưa đủ vị trí/ý nghĩa không được xác nhận Xanh chỉ vì LLM trả rỗng."""
    cues = re.compile(r"(?<!\w)(?:mệt|khó chịu|bất thường|không ổn|lạ trong người|tê bì|đau)(?!\w)", re.IGNORECASE)
    plain_cues = re.compile(r"(?<!\w)(?:met|kho chiu|bat thuong|khong on|la trong nguoi|te bi|dau)(?!\w)")
    out = []
    for _offset, clause in _clauses(text):
        known = _match_all(clause, _concepts(rules))
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


def _vote_signature(findings: list[dict], concerns: list[str], *, lex: dict[str, dict], text: str,
                    ga_weeks: int | None, rules: Rules) -> tuple:
    facts = tuple(sorted((f["concept"], f["present"], f["temporality"], f["experiencer"], f["severity"])
                         for f in findings if not _confirmed_inactive(f, lex.get(f["concept"]), text, ga_weeks, rules)))
    concerns_key = tuple(sorted({" ".join(_tokens_plain(c)) for c in concerns}))
    return facts, concerns_key


def read_free_text(
    text: str,
    ga_weeks: int | None,
    client: LlmClient | None,
    rules: Rules,
    *,
    patient_name: str | None = None,
    pid: str | None = None,
    ner: NerFn | None | bool = True,
) -> FormReading:
    text = nfc(text or "").strip()
    if not text:
        return FormReading(level=Level.GREEN, ok=True, confidence=1.0)
    penalty = float(rules["llm"].get("assertion_conflict_penalty", 0.2))
    no_dia = no_diacritic_ratio(text) > 0.7
    lex = lexicon_findings(text, rules=rules)
    for f in lex:
        f["source"] = "lexicon"
    lex_by_concept = {f["concept"]: f for f in lex}

    mask = mask_text(text, full_name=patient_name, pid=pid, ner=ner)
    reading = FormReading(level=Level.GREEN, ok=False, confidence=0.0, no_diacritics=no_dia,
                          masked_text=mask.text if mask.ok else "", mask_ok=mask.ok)
    if not mask.ok:
        reading.raw["llm_status"] = "mask_failed"
    llm_runs: list[list[dict[str, Any]]] = []
    unmapped: list[str] = []
    rejected = 0
    if client is not None and mask.ok:
        prompt, version = build_prompt(mask.text, ga_weeks, no_dia, rules)
        reading.prompt_version, reading.model = version, client.name
        votes = run_votes(client, prompt, FORM_SCHEMA, int(rules["llm"]["consistency_runs_form"]),
                          float(rules["llm"]["temperature"]))
        reading.raw = {"outputs": votes.outputs, "errors": votes.errors, "models": votes.models}
        masked_lex = {item["concept"]: item for item in lexicon_findings(mask.text, rules=rules)}
        # Lưu model THẬT đã trả kết quả (hybrid: openai hoặc ollama), không lưu tên cấu hình "hybrid:…|…".
        if votes.models:
            reading.model = "+".join(dict.fromkeys(votes.models))[:64]
        valid_concerns = []
        repairs = []
        covered_concerns = []
        for run_index, output in enumerate(votes.outputs, 1):
            grounded = []
            for f in output["findings"]:
                if f["concept"] not in rules["symptom_concepts"]:
                    rejected += 1
                    continue
                if not _evidence_in(f.get("evidence", ""), mask.text):
                    repaired = _repair_shared_negation(f, mask.text, masked_lex.get(f["concept"]), rules)
                    if repaired is None:
                        rejected += 1
                        continue
                    repairs.append({"run": run_index, "concept": f["concept"],
                                    "original": f["evidence"], "replacement": repaired["evidence"]})
                    f = repaired
                grounded.append(f)
            llm_runs.append(grounded)
            valid_concerns.append([u for u in output.get("unmapped_concerns", []) if _valid_concern(u, text, rules)])
            covered_concerns += [{"run": run_index, "text": u, "reason": "covered_brushing_bleeding"}
                                 for u in output.get("unmapped_concerns", [])
                                 if _covered_brushing_concern(u, text, rules)]
        if repairs:
            reading.raw["evidence_repairs"] = repairs
        if covered_concerns:
            reading.raw["covered_concerns"] = covered_concerns
        unmapped = _vote_unmapped(valid_concerns, text, rules)
        if votes.outputs:
            reading.ok = True
            sets = Counter(_vote_signature(run, concerns, lex=masked_lex, text=mask.text,
                                           ga_weeks=ga_weeks, rules=rules)
                           for run, concerns in zip(llm_runs, valid_concerns, strict=True))
            reading.confidence = sets.most_common(1)[0][1] / votes.n_total
            reading.raw["confidence_basis"] = "source_supported_clinical_facts"
        if rejected:
            reading.raw["rejected_findings"] = rejected
            reading.conflicts.append("LLM: mã khái niệm hoặc bằng chứng không hợp lệ – chuyển người xem")

    # Mỗi lần chạy LLM: đối chiếu luật khẳng định trên câu gốc (lấy hướng an toàn hơn).
    adjusted_runs: list[dict[str, dict[str, Any]]] = []
    for run in llm_runs:
        per_run: dict[str, dict[str, Any]] = {}
        for f in run:
            f = {**f, "source": "llm"}
            prev = per_run.get(f["concept"])
            if prev is None or _safety_key(f) > _safety_key(prev):
                per_run[f["concept"]] = f
        adjusted_runs.append(per_run)

    # Bỏ phiếu đa số giữa các lần chạy: khái niệm chỉ "có hiệu lực" khi > 1/2 số lần chạy đồng ý.
    # Lần chạy thiểu số không bị bỏ qua: nó làm các tập kết quả khác nhau → độ tin cậy < ngưỡng
    # → combine đặt ai_level tối thiểu VÀNG (docs/04 C4). Danh mục dò trực tiếp bên dưới vẫn chỉ nâng.
    merged = _majority_merge(adjusted_runs)

    # Đối chiếu với danh mục dò trực tiếp: chỉ nâng, không hạ
    for f in lex:
        prev = merged.get(f["concept"])
        if prev is None:
            if _effective(f) and llm_runs:
                reading.conflicts.append(f"{f['concept']}: LLM bỏ sót, luật phát hiện")
            merged[f["concept"]] = f
        elif (f.get("assertion_status") in {"negated", "conditional", "other_site", "anatomy_only", "uncertain"}
              and not rules["symptom_concepts"][f["concept"]].get("locked")):
            if _effective(prev) and not _same_mention(prev, f, text):
                # LLM dựa vào cụm khác mà danh mục không đọc được → giữ phát hiện của LLM (chỉ nâng, không hạ).
                reading.conflicts.append(f"{f['concept']}: LLM thấy ở cụm khác cụm danh mục phủ định – giữ phát hiện")
                continue
            if _effective(prev):
                reading.conflicts.append(f"{f['concept']}: LLM và phạm vi khẳng định khác nhau")
            merged[f["concept"]] = {**f, "source": "llm+rule"}
        elif _safety_key(f) > _safety_key(prev):
            reading.conflicts.append(f"{f['concept']}: luật khẳng định an toàn hơn LLM")
            merged[f["concept"]] = {**f, "source": "llm+rule"}

    if reading.conflicts and reading.ok:
        reading.confidence = max(0.0, reading.confidence - penalty)
        # A semantic conflict is a review condition, including exactly-at-threshold
        # agreement. Do not let a fixed subtraction accidentally certify it.
        reading.confidence = min(reading.confidence, max(0.0, float(rules["arrival"]["ai_confidence_min"]) - 0.01))

    findings = []
    for f in merged.values():
        source_f = lex_by_concept.get(f["concept"])
        source_mild = (f["concept"] == "HEADACHE" and (source_f or {}).get("severity") == "mild"
                       and _same_mention(f, source_f, text))
        source_mild = source_mild or (_explicit_mild(source_f, text, rules) and _same_mention(f, source_f, text))
        if source_mild:
            if f.get("severity") == "severe":
                reading.raw.setdefault("severity_adjudications", []).append(
                    {"concept": f["concept"], "model": "severe", "source": "mild"})
            f = {**f, "severity": "mild"}
        lvl = finding_level(f, ga_weeks, rules)
        if f["concept"] == "HEADACHE" and f.get("severity") == "mild" and lvl == Level.RED:
            reading.conflicts.append("HEADACHE: văn bản mô tả mức nhẹ nhưng cấu hình xếp đỏ – cần xác minh mức độ")
        reason = cap_reason(f, lex_by_concept.get(f["concept"]), text, rules)
        cap_level = Level.parse(rules.get("form_cap.cap_level", "yellow"))
        if reason is None and lvl > cap_level and _ai_only(f, lex_by_concept.get(f["concept"]), rules):
            reading.conflicts.append(f"{f['concept']}: chỉ AI thấy, quy tắc không có từ khóa – hạ "
                                     f"{lvl.code}→{cap_level.code}, gọi xác minh")
            lvl = cap_level
            f = {**f, "source": f"{f.get('source', 'llm')}+unverified"}
        if reason and lvl > cap_level:
            reading.conflicts.append(f"{f['concept']}: hạ {lvl.code}→{cap_level.code} ({reason})")
            lvl = cap_level
            f = {**f, "source": f"{f.get('source', 'llm')}+cap"}
        findings.append(ReadFinding(concept=f["concept"], present=bool(f["present"]),
                                    temporality=f["temporality"], experiencer=f["experiencer"],
                                    severity=f.get("severity", "unknown"), evidence=str(f.get("evidence", ""))[:120],
                                    source=f.get("source", "llm"), level=lvl.code,
                                    assertion_status=f.get("assertion_status", "affirmed"),
                                    anatomy=f.get("anatomy"), evidence_start=f.get("evidence_start"),
                                    evidence_end=f.get("evidence_end")))
    level = max_level(*(f.level for f in findings)) if findings else Level.GREEN
    if any(f.assertion_status == "uncertain" and f.experiencer == "patient" for f in findings):
        reading.conflicts.append("Chưa xác nhận là triệu chứng hiện tại – cần gọi xác minh")
    if any(not f["present"] and f["temporality"] == "past_resolved" and f["experiencer"] == "patient"
           and rules["symptom_concepts"][f["concept"]].get("past_counts_yellow") for f in lex):
        reading.conflicts.append("Chưa rõ tiền sử của dấu hiệu đã hết – cần gọi xác minh")
    if level == Level.GREEN and not unmapped:
        unresolved = _unresolved_text_concerns(text, rules)
        if unresolved:
            reading.raw["unresolved_text_concerns"] = [
                mask_fragment(item, full_name=patient_name, pid=pid, persons=mask.persons) for item in unresolved
            ] if mask.ok else []
            reading.conflicts.append("Mô tả có dấu hiệu chưa phân loại rõ – cần hỏi lại")
    if reading.conflicts and reading.ok:
        reading.confidence = min(reading.confidence, max(0.0, float(rules["arrival"]["ai_confidence_min"]) - 0.01))
    if unmapped:
        level = max_level(level, Level.YELLOW)
        reading.conflicts.append("Mối lo chưa có phân loại xác nhận – cần người xem")
        if reading.ok:
            reading.confidence = min(reading.confidence, max(0.0, float(rules["arrival"]["ai_confidence_min"]) - 0.01))
    # Bằng chứng của lớp danh mục lấy từ câu GỐC → che trước khi lưu (Assessment.findings không mã hóa).
    for f in findings:
        f.evidence = (mask_fragment(f.evidence, full_name=patient_name, pid=pid, persons=mask.persons)
                      if mask.ok else "")
        # Positions refer to the persisted masked quotation, not an unmasked mention.
        words = f.evidence.split()
        anchored = (re.search(r"\s+".join(re.escape(w) for w in words), mask.text, re.IGNORECASE)
                    if words and mask.ok else None)
        if anchored:
            f.evidence_start, f.evidence_end = anchored.span()
            f.evidence = mask.text[f.evidence_start:f.evidence_end]
        else:
            f.evidence, f.evidence_start, f.evidence_end = "", None, None
    reading.findings, reading.unmapped, reading.level = findings, unmapped, level
    if client is None:  # chạy không có AI: chỉ danh mục
        reading.ok, reading.confidence = False, 0.0
    return reading
