"""Bước 4 – đối chiếu danh mục ↔ AI theo BẢNG, rồi tính mức từng phát hiện.

Đối chiếu: với mỗi khái niệm danh mục thấy trong câu, duyệt `RECONCILE` theo thứ tự, luật đầu tiên khớp quyết định
lấy bản nào (danh mục / AI) và ghi mâu thuẫn nào. Mâu thuẫn làm độ tin cậy < ngưỡng → chuyển người xem.

Tính mức (`level_finding`): mức theo bảng khái niệm, rồi các điều chỉnh có cấu hình:
  - mức nhẹ ghi rõ trong câu ("hơi đau đầu") thắng "nặng" của AI;
  - `ai_override` – AI hạ khi đọc chắc chắn là người khác / đã hết / phủ định (Đỏ → tối đa Vàng);
  - `form_cap` (phương án A) – AI ra Đỏ nhưng danh mục chắc chắn người khác / đã hết / bé đạp bình thường → Vàng;
  - `form_cap.ai_only_red_cap` – Đỏ chỉ AI thấy, danh mục không có từ khóa → Vàng, gọi xác minh.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.ai.clinical_facts import resolution_asserted
from core.ai.reader.lexicon import concepts, effective, finding_level, match_all, safety_key
from core.ai.reader.text import (
    clause_index,
    clauses,
    evidence_in,
    has_plain,
    has_words,
    only_at,
    same_mention,
    source_clause,
)
from core.config import Rules
from core.levels import Level, max_level
from core.textutil import deaccent, nfc, no_diacritic_ratio

# Danh mục đã đọc ra phạm vi khẳng định cụ thể → ưu tiên danh mục (trừ khi AI dựa vào cụm khác).
SCOPED_STATUSES = {"negated", "conditional", "other_site", "anatomy_only", "uncertain"}


# ---------------------------------------------------------------- bảng đối chiếu
@dataclass(frozen=True)
class Pair:
    """Một khái niệm: bản danh mục, bản AI đã gộp đa số (None nếu AI không nêu), bản AI được phép hạ (nếu có)."""

    concept: str
    lex: dict[str, Any]
    ai: dict[str, Any] | None
    override: dict[str, Any] | None
    llm_ran: bool
    text: str
    locked: bool

    @property
    def scoped(self) -> bool:
        return self.lex.get("assertion_status") in SCOPED_STATUSES and not self.locked


@dataclass(frozen=True)
class Rule:
    name: str
    when: Callable[[Pair], bool]
    take: str  # override | lexicon | lexicon_rule | ai
    conflict: Callable[[Pair], str | None]


def _none(_p: Pair) -> None:
    return None


RECONCILE: tuple[Rule, ...] = (
    # AI đọc chắc chắn không có hiệu lực, đủ phiếu, câu có dấu hiệu (rules.yaml ai_override).
    Rule("ai_override", lambda p: p.override is not None, "override", _none),
    # AI không nêu khái niệm → lấy danh mục (chỉ nâng).
    Rule("ai_missed", lambda p: p.ai is None, "lexicon",
         lambda p: f"{p.concept}: LLM bỏ sót, luật phát hiện" if effective(p.lex) and p.llm_ran else None),
    # Danh mục phủ định / điều kiện / chỗ khác… nhưng AI thấy ở cụm KHÁC → giữ AI (chỉ nâng).
    Rule("scoped_but_ai_elsewhere",
         lambda p: p.scoped and p.ai is not None and effective(p.ai) and not same_mention(p.ai, p.lex, p.text), "ai",
         lambda p: f"{p.concept}: LLM thấy ở cụm khác cụm danh mục phủ định – giữ phát hiện"),
    # Danh mục đọc ra phạm vi khẳng định → lấy danh mục.
    Rule("scoped", lambda p: p.scoped, "lexicon_rule",
         lambda p: f"{p.concept}: LLM và phạm vi khẳng định khác nhau" if p.ai is not None and effective(p.ai) else None),
    # Danh mục an toàn hơn AI (có mặt / bản thân / hiện tại / khẳng định / nặng) → lấy danh mục.
    Rule("lexicon_safer", lambda p: p.ai is not None and safety_key(p.lex) > safety_key(p.ai), "lexicon_rule",
         lambda p: f"{p.concept}: luật khẳng định an toàn hơn LLM"),
    Rule("ai_kept", lambda p: True, "ai", _none),
)


def reconcile(pair: Pair) -> tuple[str, dict[str, Any] | None, str | None]:
    """(tên luật, bản được chọn – None = giữ bản AI, mâu thuẫn hoặc None)."""
    for rule in RECONCILE:
        if rule.when(pair):
            chosen = {"override": pair.override["finding"] if pair.override else None, "lexicon": pair.lex,
                      "lexicon_rule": {**pair.lex, "source": "llm+rule"}, "ai": None}[rule.take]
            return rule.name, chosen, rule.conflict(pair)
    raise AssertionError("RECONCILE phải có luật cuối luôn khớp")


# ---------------------------------------------------------------- AI hạ mức (ai_override)
def inactive_reason(f: dict[str, Any]) -> str | None:
    """Vì sao một bản đọc không có hiệu lực: người khác / đã hết / phủ định (None nếu có hiệu lực hoặc lý do khác)."""
    if effective(f):
        return None
    if f["experiencer"] == "other":
        return "other_person"
    if f["temporality"] == "past_resolved":
        return "past_resolved"
    if not f["present"]:
        return "negated"
    return None


def override_supported(reason: str, lex_f: dict[str, Any], text: str, cfg: dict[str, Any]) -> bool:
    """Câu gốc có dấu hiệu cho cách đọc của AI không (rules.yaml ai_override.support). Không xác định được → không."""
    start, end = lex_f.get("evidence_start"), lex_f.get("evidence_end")
    if start is None or end is None:
        return False
    idx = clause_index(text, start, end)
    if idx is None:
        return False
    parts = clauses(text)
    offset, clause = parts[idx]
    sup = cfg.get("support") or {}
    ambiguous = sup.get("plain_ambiguous", [])
    if reason == "negated":
        before = clause[:start - offset]
        if re.search(r"\b(?:khong|chua|ko|k)\s+het\b", deaccent(before).lower()):
            return False  # "chưa hết khó thở" is persistent, not absent.
        # A negator describing an activity ("không ăn được vì khó thở")
        # cannot support denying the symptom at the end of the clause.
        for word in sup.get("negated", []):
            if not has_words(before, [word], ambiguous):
                continue
            for match in re.finditer(rf"(?<!\w){re.escape(deaccent(word).lower())}(?!\w)",
                                     deaccent(before).lower()):
                tail = re.findall(r"\w+", deaccent(before[match.end():]).lower())
                bridges = {"co", "bi", "he", "thay", "con", "bao", "gio", "dau", "hieu",
                           "trieu", "chung", "tinh", "trang"}
                if len(tail) <= 8 and all(token in bridges for token in tail):
                    return True
                if (tail and tail[0] in {"thay", "co"} and tail[-1] in {"va", "hay", "hoac"}
                        and len(tail) <= 10 and not set(tail) & {"duoc", "noi", "vi", "nen", "nhung",
                                                               "dang", "van", "em", "toi", "minh"}):
                    return True  # "chả thấy chấm sáng hay nhìn đôi" shares the same denial.
        return False
    if reason == "past_resolved":
        active_scope = deaccent(clause).lower()
        if re.search(r"\b(?:van|dang)\b|\b(?:den|toi)\s+(?:bay gio|gio|nay)\b", active_scope):
            return False
        scope = clause[end - offset:]
        if idx + 1 < len(parts):  # mệnh đề kế tiếp CÙNG câu (không qua . ; ! ? xuống dòng)
            nstart = parts[idx + 1][0]
            if not re.search(r"[.;!?\n]", text[offset + len(clause):nstart]):
                scope += " " + parts[idx + 1][1]
        # "Hôm qua" / "đã đi khám rồi" does not establish recovery. Keep the
        # existing remote-history override, but require actual recovery wording
        # for recent events. In particular, "hết thuốc" is not symptom resolution.
        remote = [w for w in sup.get("past_resolved", [])
                  if deaccent(w).lower() in {"hoi", "truoc day", "thang truoc", "tuan truoc"}]
        return resolution_asserted(scope) or has_words(clause[:start - offset], remote, ambiguous)
    if reason == "other_person":
        return _person_subject_before(clause[: start - offset], sup)
    return False


SELF_WORDS = {"em", "tôi", "mình", "e", "tớ"}


def _person_subject_before(before: str, sup: dict[str, Any]) -> bool:
    """Ngay trước triệu chứng là một từ chỉ người (đi lùi qua từ nối / danh xưng / đại từ sở hữu liền sau người).

    Văn bản có dấu so ĐÚNG DẤU ("có" ≠ "cô"); không dấu thì bỏ các từ trùng (`experiencer.ambiguous_plain`-kiểu: cô, má,
    chị, dì, ba, con) – thà không đủ dấu hiệu (giữ mức danh mục) còn hơn hạ nhầm.
    """
    plain = no_diacritic_ratio(before) > 0.7 if before.strip() else False
    norm = (lambda w: deaccent(w).lower()) if plain else (lambda w: nfc(w).lower())
    toks = re.findall(r"\w+", norm(before))
    person = {norm(p.split()[-1]) for p in sup.get("person_words", [])}
    if plain:
        person -= {deaccent(w) for w in ("cô", "má", "chị", "dì", "ba", "con", "cu")}
    cont = {norm(w) for w in sup.get("person_continuation", [])}
    selfs = {norm(w) for w in SELF_WORDS}
    bridges = {norm(w) for w in sup.get("not_subject_words", [])} - selfs - person
    i = len(toks) - 1
    while i >= 0 and toks[i] in bridges:
        i -= 1
    while i >= 0:
        t = toks[i]
        if t in person:
            return True
        if t in selfs:  # đại từ chỉ được là sở hữu: ngay sau người / danh xưng ("cháu em", "bạn của tôi")
            if i >= 1 and (toks[i - 1] in person or toks[i - 1] in cont):
                i -= 1
                continue
            return False
        if t in cont:
            i -= 1
            continue
        return False
    return False


def ai_override(lex_f: dict[str, Any], runs: list[dict[str, dict[str, Any]]], n_total: int, *, ai_ok: bool,
                confidence: float, text: str, ga_weeks: int | None, rules: Rules) -> dict[str, Any] | None:
    """AI hạ một phát hiện của danh mục (rules.yaml `ai_override`, người dùng chốt 15/11/2026). Hàm thuần.

    Chỉ khi: danh mục thấy triệu chứng CÓ hiệu lực ở thai phụ; AI chạy được, đủ tỷ lệ phiếu (`min_vote_share`) cùng đọc
    ra không có hiệu lực vì một lý do trong `reasons`, ở ĐÚNG chỗ danh mục đọc và trích dẫn có thật; không phiếu nào
    thấy có hiệu lực; độ nhất quán chung ≥ `min_confidence`; khái niệm không khóa / không nằm trong `exclude_concepts`;
    văn bản không có dấu hiệu thai phụ cũng bị (`form_cap.keep_red_markers`); câu gốc có dấu hiệu (`support`).
    Đỏ → tối đa `floor_if_red` (không bao giờ Xanh). Trả {"finding", "record"} hoặc None.
    """
    cfg = rules.get("ai_override") or {}
    concept = rules["symptom_concepts"].get(lex_f["concept"]) or {}
    if (not cfg.get("enabled") or not ai_ok or n_total <= 0 or not effective(lex_f) or concept.get("locked")
            or lex_f["concept"] in cfg.get("exclude_concepts", [])
            or confidence < float(cfg.get("min_confidence", 1.0))
            or has_plain(text, (rules.get("form_cap") or {}).get("keep_red_markers", []))):
        return None
    before = finding_level(lex_f, ga_weeks, rules)
    if before == Level.GREEN:
        return None
    versions = [r[lex_f["concept"]] for r in runs if lex_f["concept"] in r]
    if any(effective(v) for v in versions):
        return None
    allowed = set(cfg.get("reasons", []))
    agree = [v for v in versions if inactive_reason(v) in allowed and evidence_in(v.get("evidence", ""), text)
             and only_at(v, lex_f, text)]
    if not agree or len(agree) < len(versions) or len(agree) / n_total < float(cfg.get("min_vote_share", 1.0)):
        return None
    chosen = max(agree, key=safety_key)  # bản an toàn nhất trong các phiếu đồng ý
    reason = str(inactive_reason(chosen))
    if not override_supported(reason, lex_f, text, cfg):
        return None
    # Đỏ không bao giờ về Xanh – kẹp cả khi cấu hình đặt sai (floor_if_red là khóa sửa được qua màn Cấu hình).
    floor = max_level(cfg.get("floor_if_red", "yellow"), Level.YELLOW) if before == Level.RED else None
    after = max_level(finding_level(chosen, ga_weeks, rules), floor) if floor else finding_level(chosen, ga_weeks, rules)
    if after >= before:
        return None
    finding = {**lex_f, **{k: chosen[k] for k in ("present", "temporality", "experiencer")},
               "source": "llm+override", "override_reason": reason, "override_floor": floor.code if floor else None}
    record = {"concept": lex_f["concept"], "from": before.code, "to": after.code, "reason": reason,
              "votes": f"{len(agree)}/{n_total}", "confidence": round(confidence, 2)}
    return {"finding": finding, "record": record}


# ---------------------------------------------------------------- giới hạn mức (form_cap)
def cap_reason(f: dict[str, Any], lex_f: dict[str, Any] | None, text: str, rules: Rules) -> str | None:
    """Lý do hạ một finding LLM (phương án A, config form_cap); None nếu không được hạ.

    Chỉ hạ khi lớp danh mục chắc chắn: cùng khái niệm được dò thấy trong văn bản và MỌI lần xuất hiện đều là
    người khác / đã hết; hoặc câu evidence chỉ tả bé đạp bình thường và danh mục không thấy khái niệm đó.
    Không hạ khái niệm locked, không hạ khi văn bản có dấu hiệu thai phụ cũng bị / kể lại ("em cũng", "bảo em").
    """
    cap = rules.get("form_cap", {}) or {}
    concept = rules["symptom_concepts"].get(f["concept"])
    if not cap.get("enabled") or concept is None or concept.get("locked") or not effective(f):
        return None
    if "llm" not in str(f.get("source", "llm")):
        return None
    if has_plain(text, cap.get("keep_red_markers", [])):
        return None
    if lex_f is not None:
        if lex_f["present"] and not effective(lex_f):
            return "danh mục: người khác bị" if lex_f["experiencer"] == "other" else "danh mục: đã hết"
        return None
    if f["concept"] in cap.get("normal_fm_concepts", []):
        clause = source_clause(str(f.get("evidence", "")), text)
        if (clause and has_plain(clause, cap.get("normal_fm_phrases", []))
                and not has_plain(clause, cap.get("normal_fm_breakers", []))):
            return "bé đạp bình thường"
    return None


def ai_only(f: dict[str, Any], lex_f: dict[str, Any] | None, rules: Rules) -> bool:
    """Đỏ chỉ dựa vào AI: lớp danh mục không thấy khái niệm này trong câu (config form_cap.ai_only_red_cap)."""
    cap = rules.get("form_cap", {}) or {}
    concept = rules["symptom_concepts"].get(f["concept"]) or {}
    if not cap.get("enabled") or not cap.get("ai_only_red_cap") or concept.get("locked"):
        return False
    return "llm" in str(f.get("source", "")) and lex_f is None


def explicit_mild(lex: dict | None, text: str, rules: Rules) -> bool:
    """Từ mức nhẹ phải gắn với đúng lần nhắc, không lấy từ hoạt động / triệu chứng khác."""
    if lex is None or lex.get("severity") != "mild":
        return False
    start, end = lex.get("evidence_start"), lex.get("evidence_end")
    if start is None or end is None:
        return False
    for offset, clause in clauses(text):
        if offset <= start and end <= offset + len(clause):
            local_start, local_end = start - offset, end - offset
            before = re.split(r"\b(?:va|roi|nhung|vi|nen|kem)\b", deaccent(clause[:local_start]).lower())[-1]
            next_start = min((s for _concept, s, _e in match_all(clause, concepts(rules)) if s >= local_end),
                             default=len(clause))
            after = re.split(r"\b(?:va|roi|nhung|vi|nen|kem)\b", deaccent(clause[local_end:next_start]).lower())[0]
            return bool(re.search(r"\b(?:hoi|nhe|thoang qua)\b", " ".join(before.split()[-2:]) + " " + after))
    return False


RedCheck = Callable[[dict[str, Any]], tuple[bool, dict[str, Any]]]


def ai_red_keep(f: dict[str, Any], *, unanimous: set[str], embedder: Any, rules: Rules) -> tuple[bool, dict[str, Any]]:
    """Giữ ĐỎ cho ca chỉ AI thấy (rules.yaml form_cap.ai_only_red_keep) khi hai bộ đọc độc lập cùng xác nhận.

    Đủ HẾT: công tắc bật; khái niệm trong danh sách; mọi lần chạy AI cùng thấy (require_unanimous); bộ đọc theo nghĩa
    xác nhận đúng khái niệm trên trích dẫn (require_semantic). Thiếu nhân chứng / lỗi embedding → không giữ (hạ vàng).
    Trả (giữ?, bản ghi để lưu raw.ai_red_checks).
    """
    from core.ai.llm_client import LlmError
    from core.ai.reader.semantic import witness

    cfg = (rules.get("form_cap") or {}).get("ai_only_red_keep") or {}
    rec: dict[str, Any] = {"concept": f["concept"], "unanimous": f["concept"] in unanimous}
    if not cfg.get("enabled") or f["concept"] not in cfg.get("concepts", []):
        return False, rec | {"kept": False, "why": "không áp dụng"}
    if cfg.get("require_unanimous", True) and not rec["unanimous"]:
        return False, rec | {"kept": False, "why": "AI không thống nhất"}
    if cfg.get("require_semantic", True):
        if embedder is None:
            return False, rec | {"kept": False, "why": "không có bộ đọc theo nghĩa"}
        try:
            w = witness(f["concept"], str(f.get("evidence", "")), embedder, rules)
        except LlmError as e:
            return False, rec | {"kept": False, "why": f"embedding lỗi: {str(e)[:80]}"}
        rec["semantic"] = w.record()
        if not w.confirmed:
            return False, rec | {"kept": False, "why": "nghĩa không xác nhận"}
    return True, rec | {"kept": True}


@dataclass
class Leveled:
    finding: dict[str, Any]
    level: Level
    conflicts: list[str]
    severity_adjudication: dict[str, Any] | None
    red_check: dict[str, Any] | None = None


def level_finding(f: dict[str, Any], lex_f: dict[str, Any] | None, text: str, ga_weeks: int | None,
                  rules: Rules, red_check: RedCheck | None = None) -> Leveled:
    """Mức một phát hiện sau đối chiếu, kèm điều chỉnh có cấu hình (xem đầu module)."""
    conflicts: list[str] = []
    adjudication = None
    source_mild = (f["concept"] == "HEADACHE" and (lex_f or {}).get("severity") == "mild"
                   and same_mention(f, lex_f, text))
    source_mild = source_mild or (explicit_mild(lex_f, text, rules) and same_mention(f, lex_f, text))
    if source_mild:
        if f.get("severity") == "severe":
            adjudication = {"concept": f["concept"], "model": "severe", "source": "mild"}
        f = {**f, "severity": "mild"}
    lvl = finding_level(f, ga_weeks, rules)
    if f.get("override_floor"):  # AI hạ Đỏ: tối đa xuống floor_if_red, không bao giờ về Xanh
        lvl = max_level(lvl, f["override_floor"])
    if f["concept"] == "HEADACHE" and f.get("severity") == "mild" and lvl == Level.RED:
        conflicts.append("HEADACHE: văn bản mô tả mức nhẹ nhưng cấu hình xếp đỏ – cần xác minh mức độ")
    reason = cap_reason(f, lex_f, text, rules)
    cap_level = Level.parse(rules.get("form_cap.cap_level", "yellow"))
    check = None
    if reason is None and lvl > cap_level and ai_only(f, lex_f, rules):
        kept, check = red_check(f) if red_check is not None else (False, None)
        if kept:
            conflicts.append(f"{f['concept']}: chỉ AI thấy, bộ đọc theo nghĩa xác nhận – giữ {lvl.code}, gọi xác minh")
            f = {**f, "source": f"{f.get('source', 'llm')}+confirmed"}
        else:
            conflicts.append(f"{f['concept']}: chỉ AI thấy, quy tắc không có từ khóa – hạ "
                             f"{lvl.code}→{cap_level.code}, gọi xác minh")
            lvl = cap_level
            f = {**f, "source": f"{f.get('source', 'llm')}+unverified"}
    if reason and lvl > cap_level:
        conflicts.append(f"{f['concept']}: hạ {lvl.code}→{cap_level.code} ({reason})")
        lvl = cap_level
        f = {**f, "source": f"{f.get('source', 'llm')}+cap"}
    return Leveled(f, lvl, conflicts, adjudication, check)
