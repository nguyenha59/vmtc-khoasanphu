"""Lớp 3 – đọc phần tự viết của form (docs/05 mục 3). Ghép các bước trong core/ai/reader/.

  1. danh mục + luật khẳng định trên câu gốc            (reader/lexicon.py)
  2. che dữ liệu → AI đọc 3 lần → lọc phiếu, gộp đa số   (reader/votes.py)
  3. mối lo ngoài danh mục                               (reader/concerns.py)
  4. bảng đối chiếu danh mục ↔ AI, tính mức có giới hạn  (reader/reconcile.py)
  5. cờ chuyển người xem, che bằng chứng trước khi lưu   (bên dưới)

Mặc định danh mục chỉ NÂNG mức của AI. Ngoại lệ có cấu hình: `ai_override` (AI hạ khi đọc chắc chắn, Đỏ tối đa Vàng),
`form_cap` (AI ra Đỏ nhưng danh mục chắc chắn không có hiệu lực → Vàng; Đỏ chỉ AI thấy → Vàng, gọi xác minh).
Hành vi khóa bởi tests/test_form_reader_behavior.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.ai.clinical_facts import using_rules
from core.ai.consistency import run_votes
from core.ai.injection import REVIEW_NOTE as INJECTION_NOTE
from core.ai.injection import injection_signals
from core.ai.llm_client import LlmClient, render_prompt
from core.ai.masking import NerFn, mask_fragment, mask_text
from core.ai.reader.concerns import covered_brushing_concern as _covered_brushing_concern  # noqa: F401
from core.ai.reader.concerns import unresolved_text_concerns
from core.ai.reader.concerns import valid_concern as _valid_concern  # noqa: F401
from core.ai.reader.concerns import vote_unmapped as _vote_unmapped  # noqa: F401
from core.ai.reader.lexicon import effective, finding_level, lexicon_findings  # noqa: F401
from core.ai.reader.reconcile import (  # noqa: F401
    Pair,
    ai_override,
    ai_red_keep,
    cap_reason,
    level_finding,
    reconcile,
)
from core.ai.reader.votes import VoteResult, collect
from core.ai.reader.votes import majority_merge as _majority_merge
from core.config import Rules, prompt_text
from core.levels import Level, max_level
from core.textutil import nfc, no_diacritic_ratio

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


def build_prompt(masked: str, ga_weeks: int | None, no_diacritics: bool, rules: Rules) -> tuple[str, str]:
    template, version = prompt_text("form_reader")
    # Ý nghĩa (meaning) giúp AI xếp đúng khái niệm cho cách nói chưa có trong danh mục ("mỏ ác", "thở không ra hơi").
    lines = [f"- {code}: {c['name']}" + (f" – {re.sub(r' [(]S[0-9, S]+[)]$', '', c['meaning'])}" if c.get("meaning") else "")
             + f" (ví dụ cách nói: {', '.join(c['synonyms'][:6])})"
             for code, c in rules["symptom_concepts"].items()]
    template = template.replace("{concepts}", "\n".join(lines))
    payload = {"text": masked, "ga_weeks": ga_weeks, "no_diacritics": no_diacritics}
    return render_prompt(template, payload), version


def _review_cap(rules: Rules) -> float:
    """Độ tin cậy tối đa khi có mâu thuẫn: ngay dưới ngưỡng → luôn chuyển người xem."""
    return max(0.0, float(rules["arrival"]["ai_confidence_min"]) - 0.01)


def _ask_ai(reading: FormReading, client: LlmClient, masked: str, text: str, ga_weeks: int | None,
            rules: Rules) -> VoteResult:
    """Bước 2 + 3: AI đọc văn bản đã che `consistency_runs_form` lần; lọc phiếu, mối lo, độ nhất quán."""
    prompt, version = build_prompt(masked, ga_weeks, reading.no_diacritics, rules)
    reading.prompt_version, reading.model = version, client.name
    votes = run_votes(client, prompt, FORM_SCHEMA, int(rules["llm"]["consistency_runs_form"]),
                      float(rules["llm"]["temperature"]))
    reading.raw = {"outputs": votes.outputs, "errors": votes.errors, "models": votes.models}
    # Lưu model THẬT đã trả kết quả (hybrid: openai hoặc ollama), không lưu tên cấu hình "hybrid:…|…".
    if votes.models:
        reading.model = "+".join(dict.fromkeys(votes.models))[:64]
    masked_lex = {item["concept"]: item for item in lexicon_findings(masked, rules=rules)}
    res = collect(votes.outputs, votes.n_total, masked=masked, text=text, masked_lex=masked_lex,
                  ga_weeks=ga_weeks, rules=rules)
    if res.repairs:
        reading.raw["evidence_repairs"] = res.repairs
    if res.covered_concerns:
        reading.raw["covered_concerns"] = res.covered_concerns
    if res.ok:
        reading.ok, reading.confidence = True, res.confidence
        reading.raw["confidence_basis"] = "source_supported_clinical_facts"
    if res.rejected:
        reading.raw["rejected_findings"] = res.rejected
        reading.conflicts.append("LLM: mã khái niệm hoặc bằng chứng không hợp lệ – chuyển người xem")
    return res


def _reconcile_all(reading: FormReading, lex: list[dict[str, Any]], res: VoteResult, text: str,
                   ga_weeks: int | None, rules: Rules) -> dict[str, dict[str, Any]]:
    """Bước 4a: gộp đa số các lần chạy AI, rồi đối chiếu từng khái niệm danh mục theo bảng RECONCILE."""
    merged = _majority_merge(res.runs)
    # Câu giống lệnh điều khiển máy (prompt injection) → không cho AI hạ mức ở form này (core/ai/injection.py).
    injected = bool(reading.raw.get("prompt_injection"))
    for f in lex:
        ai = merged.get(f["concept"])
        override = (ai_override(f, res.runs, res.n_total, ai_ok=reading.ok, confidence=reading.confidence,
                                text=text, ga_weeks=ga_weeks, rules=rules)
                    if ai is not None and not injected else None)
        pair = Pair(concept=f["concept"], lex=f, ai=ai, override=override, llm_ran=bool(res.runs), text=text,
                    locked=bool(rules["symptom_concepts"][f["concept"]].get("locked")))
        _rule, chosen, conflict = reconcile(pair)
        if override is not None:
            reading.raw.setdefault("ai_overrides", []).append(override["record"])
            if override["finding"].get("override_floor"):  # Đỏ bị AI hạ → luôn chuyển người xem, gọi xác minh
                rec = override["record"]
                reading.conflicts.append(f"{rec['concept']}: AI đọc là {rec['reason']}, hạ {rec['from']}→{rec['to']}"
                                         " – gọi xác minh")
        if conflict:
            reading.conflicts.append(conflict)
        if chosen is not None:
            merged[f["concept"]] = chosen
    if reading.conflicts and reading.ok:
        penalty = float(rules["llm"].get("assertion_conflict_penalty", 0.2))
        # Mâu thuẫn ngữ nghĩa luôn là điều kiện người xem, kể cả khi độ nhất quán vừa chạm ngưỡng.
        reading.confidence = min(max(0.0, reading.confidence - penalty), _review_cap(rules))
    return merged


def _red_checker(res: VoteResult, rules: Rules, embedder: Any) -> Any:
    """Hàm kiểm tra giữ Đỏ cho ca chỉ AI thấy – chỉ khi form_cap.ai_only_red_keep bật (tắt → hành vi cũ, không gọi gì)."""
    if not ((rules.get("form_cap") or {}).get("ai_only_red_keep") or {}).get("enabled"):
        return None
    unanimous = {c for c in {c for r in res.runs for c in r}
                 if len(res.runs) == res.n_total and all(c in r and effective(r[c]) for r in res.runs)}
    state: dict[str, Any] = {"embedder": embedder}

    def check(f: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        if state["embedder"] is None and "tried" not in state:  # tạo khi cần lần đầu (ít ca chỉ AI thấy Đỏ)
            from core.ai.reader.semantic import make_embedder_safe

            state["embedder"], state["tried"] = make_embedder_safe(rules)[0], True
        return ai_red_keep(f, unanimous=unanimous, embedder=state["embedder"], rules=rules)

    return check


def _mask_evidence(findings: list[ReadFinding], mask: Any, patient_name: str | None, pid: str | None) -> None:
    """Bằng chứng của danh mục lấy từ câu GỐC → che trước khi lưu (Assessment.findings không mã hóa).
    Vị trí trỏ vào văn bản ĐÃ che."""
    for f in findings:
        f.evidence = (mask_fragment(f.evidence, full_name=patient_name, pid=pid, persons=mask.persons)
                      if mask.ok else "")
        words = f.evidence.split()
        anchored = (re.search(r"\s+".join(re.escape(w) for w in words), mask.text, re.IGNORECASE)
                    if words and mask.ok else None)
        if anchored:
            f.evidence_start, f.evidence_end = anchored.span()
            f.evidence = mask.text[f.evidence_start:f.evidence_end]
        else:
            f.evidence, f.evidence_start, f.evidence_end = "", None, None


def read_free_text(
    text: str,
    ga_weeks: int | None,
    client: LlmClient | None,
    rules: Rules,
    *,
    patient_name: str | None = None,
    pid: str | None = None,
    ner: NerFn | None | bool = True,
    embedder: Any = None,
) -> FormReading:
    """`embedder`: bộ đọc theo nghĩa cho form_cap.ai_only_red_keep (None = tạo theo rules.yaml `semantic` khi cần).
    Mọi bước dùng đúng bộ `rules` truyền vào (kể cả danh sách người thân)."""
    with using_rules(rules):
        return _read_free_text(text, ga_weeks, client, rules, patient_name=patient_name, pid=pid, ner=ner,
                               embedder=embedder)


def _read_free_text(text: str, ga_weeks: int | None, client: LlmClient | None, rules: Rules, *,
                    patient_name: str | None, pid: str | None, ner: NerFn | None | bool, embedder: Any) -> FormReading:
    text = nfc(text or "").strip()
    if not text:
        return FormReading(level=Level.GREEN, ok=True, confidence=1.0)

    # 1. danh mục trên câu gốc
    lex = lexicon_findings(text, rules=rules)
    for f in lex:
        f["source"] = "lexicon"
    lex_by_concept = {f["concept"]: f for f in lex}

    # 2 + 3. che dữ liệu → AI (chỉ khi che sạch)
    mask = mask_text(text, full_name=patient_name, pid=pid, ner=ner)
    reading = FormReading(level=Level.GREEN, ok=False, confidence=0.0, no_diacritics=no_diacritic_ratio(text) > 0.7,
                          masked_text=mask.text if mask.ok else "", mask_ok=mask.ok)
    if not mask.ok:
        reading.raw["llm_status"] = "mask_failed"
    res = _ask_ai(reading, client, mask.text, text, ga_weeks, rules) if client is not None and mask.ok else VoteResult()
    injection = injection_signals(text, rules)  # sau _ask_ai (hàm đó đặt lại reading.raw)
    if injection:
        reading.raw["prompt_injection"] = injection
        reading.conflicts.append(INJECTION_NOTE)

    # 4. đối chiếu + mức từng phát hiện
    merged = _reconcile_all(reading, lex, res, text, ga_weeks, rules)
    red_check = _red_checker(res, rules, embedder) if reading.ok else None
    findings = []
    for f in merged.values():
        leveled = level_finding(f, lex_by_concept.get(f["concept"]), text, ga_weeks, rules, red_check)
        if leveled.severity_adjudication:
            reading.raw.setdefault("severity_adjudications", []).append(leveled.severity_adjudication)
        if leveled.red_check:
            reading.raw.setdefault("ai_red_checks", []).append(leveled.red_check)
        reading.conflicts += leveled.conflicts
        f = leveled.finding
        findings.append(ReadFinding(concept=f["concept"], present=bool(f["present"]),
                                    temporality=f["temporality"], experiencer=f["experiencer"],
                                    severity=f.get("severity", "unknown"), evidence=str(f.get("evidence", ""))[:120],
                                    source=f.get("source", "llm"), level=leveled.level.code,
                                    assertion_status=f.get("assertion_status", "affirmed"),
                                    anatomy=f.get("anatomy"), evidence_start=f.get("evidence_start"),
                                    evidence_end=f.get("evidence_end")))
    level = max_level(*(f.level for f in findings)) if findings else Level.GREEN

    # 5. cờ chuyển người xem
    if any(f.assertion_status == "uncertain" and f.experiencer == "patient" for f in findings):
        reading.conflicts.append("Chưa xác nhận là triệu chứng hiện tại – cần gọi xác minh")
    if any(not f["present"] and f["temporality"] == "past_resolved" and f["experiencer"] == "patient"
           and rules["symptom_concepts"][f["concept"]].get("past_counts_yellow") for f in lex):
        reading.conflicts.append("Chưa rõ tiền sử của dấu hiệu đã hết – cần gọi xác minh")
    unmapped = res.unmapped
    if level == Level.GREEN and not unmapped:
        unresolved = unresolved_text_concerns(text, rules)
        if unresolved:
            reading.raw["unresolved_text_concerns"] = [
                mask_fragment(item, full_name=patient_name, pid=pid, persons=mask.persons) for item in unresolved
            ] if mask.ok else []
            reading.conflicts.append("Mô tả có dấu hiệu chưa phân loại rõ – cần hỏi lại")
    if reading.conflicts and reading.ok:
        reading.confidence = min(reading.confidence, _review_cap(rules))
    if unmapped:
        level = max_level(level, Level.YELLOW)
        reading.conflicts.append("Mối lo chưa có phân loại xác nhận – cần người xem")
        if reading.ok:
            reading.confidence = min(reading.confidence, _review_cap(rules))

    _mask_evidence(findings, mask, patient_name, pid)
    reading.findings, reading.unmapped, reading.level = findings, unmapped, level
    if client is None:  # chạy không có AI: chỉ danh mục
        reading.ok, reading.confidence = False, 0.0
    return reading
