"""Lớp 3 – AI đọc phần tự viết của form (docs/05 mục 3).

LLM (self-consistency 3 lần) + luật khẳng định (phủ định / thời gian / người trải qua) chạy trên câu gốc.
Mâu thuẫn giữa LLM và luật → lấy hướng an toàn hơn và hạ độ tin cậy. Danh mục khái niệm cũng được
dò trực tiếp trên văn bản để không bỏ sót khi LLM bỏ qua (nâng mức). Ngoại lệ duy nhất: `form_cap`
(phương án A) – danh mục chắc chắn người khác / đã hết / bé đạp bình thường → finding đỏ của LLM tối đa vàng.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

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
    before = clause[:start].lower()
    tokens = re.findall(r"\w+", before)
    negated = negation_allowed and _negated(tokens)
    after_and_next = clause[end:] + " " + next_clause
    if _has(clause, CONTINUE):
        temporality = "current"
    elif _has(clause, PAST) or _has(after_and_next, RESOLVED):
        temporality = "past_resolved"
    else:
        temporality = "current"
    experiencer = "other" if _other_subject(before) else "patient"
    return {"present": not negated, "temporality": temporality, "experiencer": experiencer}


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


# Từ chỉ mức độ / trợ từ hay chen giữa cụm triệu chứng: "mặt HƠI sưng", "ra BỊ máu", "nhìn RẤT mờ".
FILLERS = ["hơi", "bị", "rất", "khá", "hay", "cũng", "thấy", "hoi", "bi", "rat", "kha", "cung", "thay"]


def _syn_pattern(syn: str, plain: bool) -> str:
    """Mẫu khớp từ đồng nghĩa, cho phép tối đa một từ chỉ mức độ chen giữa các từ."""
    words = syn.split()
    fillers = sorted({deaccent(f) for f in FILLERS} if plain else set(FILLERS), key=len, reverse=True)
    gap = rf"\s+(?:(?:{'|'.join(map(re.escape, fillers))})\s+)?"
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
    for idx, (_cstart, clause) in enumerate(clauses):
        nxt = clauses[idx + 1][1] if idx + 1 < len(clauses) else ""
        if nxt and _match_all(nxt, concepts):
            nxt = ""
        for code, s, e in _match_all(clause, concepts):
            c = concepts[code]
            a = (assertion(clause, s, e, nxt,
                           c.get("negation_allowed", True))
                 if apply_assertions else {"present": True, "temporality": "current", "experiencer": "patient"})
            sev = "unknown"
            if c.get("severe_words"):
                sev = "severe" if _has(clause, c["severe_words"]) or _has(deaccent(clause), [deaccent(w) for w in c["severe_words"]]) else "mild"
            f = {"concept": code, **a, "severity": sev, "evidence": clause.strip()[:120]}
            prev = best.get(code)
            if prev is None or _safety_key(f) > _safety_key(prev):
                best[code] = f
    return list(best.values())


def _safety_key(f: dict[str, Any]) -> tuple[int, int, int, int]:
    """Thứ tự 'an toàn hơn': có mặt > không; hiện tại > đã qua; bản thân > người khác; nặng > nhẹ."""
    return (int(bool(f["present"])), int(f["experiencer"] == "patient"),
            int(f["temporality"] != "past_resolved"), SEVERITY_ORDER.get(f["severity"], 1))


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
    return lvl


def _effective(f: dict[str, Any]) -> bool:
    return bool(f["present"]) and f["experiencer"] == "patient" and f["temporality"] != "past_resolved"


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
    clause = _source_clause(concern, text)
    return bool(clause) and _has_plain(clause, cap.get("info_context_phrases", []))


def _valid_concern(concern: str, text: str, rules: Rules | None = None) -> bool:
    """Mối lo ngoài danh mục hợp lệ: bám văn bản, không bị phủ định, không phải của người khác, không phải câu bình thường."""
    concern = nfc(concern or "").strip()
    if not concern or not _grounded(concern, text):
        return False
    head = re.findall(r"\w+", concern.lower())[:3]
    if any(tok in NEGATORS for tok in head):
        return False
    if _other_experiencer(concern):
        return False
    cap = (rules or load_rules()).get("form_cap", {}) or {}
    if _benign_concern(concern, text, cap):
        return False
    # Mệnh đề gốc nói về người khác ("chị dâu em bị tiền sản giật" → mối lo "tiền sản giật").
    clause = _source_clause(concern, text)
    return not (cap.get("enabled") and clause and _other_experiencer(clause))


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


# ---------------------------------------------------------------- đọc form
def build_prompt(masked: str, ga_weeks: int | None, no_diacritics: bool, rules: Rules) -> tuple[str, str]:
    template, version = prompt_text("form_reader")
    lines = [f"- {code}: {c['name']} (cách nói: {', '.join(c['synonyms'][:6])})"
             for code, c in rules["symptom_concepts"].items()]
    template = template.replace("{concepts}", "\n".join(lines))
    payload = {"text": masked, "ga_weeks": ga_weeks, "no_diacritics": no_diacritics}
    return render_prompt(template, payload), version


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

    mask = mask_text(text, full_name=patient_name, pid=pid, ner=ner)
    reading = FormReading(level=Level.GREEN, ok=False, confidence=0.0, no_diacritics=no_dia,
                          masked_text=mask.text, mask_ok=mask.ok)
    llm_runs: list[list[dict[str, Any]]] = []
    unmapped: list[str] = []
    if client is not None and mask.ok:
        prompt, version = build_prompt(mask.text, ga_weeks, no_dia, rules)
        reading.prompt_version, reading.model = version, client.name
        votes = run_votes(client, prompt, FORM_SCHEMA, int(rules["llm"]["consistency_runs_form"]),
                          float(rules["llm"]["temperature"]))
        reading.raw = {"outputs": votes.outputs, "errors": votes.errors}
        llm_runs = [o["findings"] for o in votes.outputs]
        unmapped = _vote_unmapped([o.get("unmapped_concerns", []) for o in votes.outputs], text, rules)
        if votes.outputs:
            reading.ok = True
            sets = Counter(frozenset(f["concept"] for f in run if _effective(f)) for run in llm_runs)
            reading.confidence = sets.most_common(1)[0][1] / votes.n_total

    # Mỗi lần chạy LLM: đối chiếu luật khẳng định trên câu gốc (lấy hướng an toàn hơn).
    adjusted_runs: list[dict[str, dict[str, Any]]] = []
    for run in llm_runs:
        per_run: dict[str, dict[str, Any]] = {}
        for f in run:
            f = {**f, "source": "llm"}
            # Luật khẳng định chạy trên câu gốc quanh evidence
            pos = text.lower().find(str(f.get("evidence", "")).lower()[:40]) if f.get("evidence") else -1
            if pos >= 0 and f["concept"] in rules["symptom_concepts"]:
                all_clauses = _clauses(text)
                for ci, (cstart, clause) in enumerate(all_clauses):
                    if cstart <= pos < cstart + len(clause) + 1:
                        span = [m for m in _match_all(clause, {f["concept"]: rules["symptom_concepts"][f["concept"]]})]
                        if span:
                            _c, s, e = span[0]
                            # Mệnh đề kế tiếp (nếu không chứa khái niệm khác), ví dụ "giờ hết rồi" – như lexicon_findings.
                            nxt = all_clauses[ci + 1][1] if ci + 1 < len(all_clauses) else ""
                            if nxt and _match_all(nxt, rules["symptom_concepts"]):
                                nxt = ""
                            a = assertion(clause, s, e, nxt,
                                          rules["symptom_concepts"][f["concept"]].get("negation_allowed", True))
                            safer = {**f, **{k: v for k, v in a.items()}}
                            if _effective(safer) != _effective(f):
                                reading.conflicts.append(f"{f['concept']}: LLM và luật khẳng định khác nhau")
                                if _safety_key(safer) > _safety_key(f):
                                    f = {**safer, "source": "llm+rule"}
                        break
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
        elif _safety_key(f) > _safety_key(prev):
            reading.conflicts.append(f"{f['concept']}: luật khẳng định an toàn hơn LLM")
            merged[f["concept"]] = {**f, "source": "llm+rule"}

    if reading.conflicts and reading.ok:
        reading.confidence = max(0.0, reading.confidence - penalty)

    lex_by_concept = {f["concept"]: f for f in lex}
    findings = []
    for f in merged.values():
        lvl = finding_level(f, ga_weeks, rules)
        reason = cap_reason(f, lex_by_concept.get(f["concept"]), text, rules)
        cap_level = Level.parse(rules.get("form_cap.cap_level", "yellow"))
        if reason and lvl > cap_level:
            reading.conflicts.append(f"{f['concept']}: hạ {lvl.code}→{cap_level.code} ({reason})")
            lvl = cap_level
            f = {**f, "source": f"{f.get('source', 'llm')}+cap"}
        findings.append(ReadFinding(concept=f["concept"], present=bool(f["present"]),
                                    temporality=f["temporality"], experiencer=f["experiencer"],
                                    severity=f.get("severity", "unknown"), evidence=str(f.get("evidence", ""))[:120],
                                    source=f.get("source", "llm"), level=lvl.code))
    level = max_level(*(f.level for f in findings)) if findings else Level.GREEN
    if unmapped:
        level = max_level(level, Level.YELLOW)
    # Bằng chứng của lớp danh mục lấy từ câu GỐC → che trước khi lưu (Assessment.findings không mã hóa).
    for f in findings:
        f.evidence = mask_fragment(f.evidence, full_name=patient_name, pid=pid, persons=mask.persons)
    reading.findings, reading.unmapped, reading.level = findings, unmapped, level
    if client is None:  # chạy không có AI: chỉ danh mục
        reading.ok, reading.confidence = False, 0.0
    return reading
