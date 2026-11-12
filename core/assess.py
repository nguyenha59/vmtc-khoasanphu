"""Đánh giá 1 câu trả lời form: lớp 1 quy tắc + lớp 2 xu hướng + lớp 3 AI → gộp → tóm tắt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.ai.combine import Combined, combine
from core.ai.form_reader import FormReading, read_free_text
from core.ai.llm_client import LlmClient
from core.ai.masking import NerFn
from core.ai.summary import summarize
from core.ai.trends import TrendResult, trend_level
from core.config import FormsConfig, Rules
from core.db import now
from core.levels import Level
from core.rules.triage_rules import RuleResult, triage


@dataclass
class AssessResult:
    level: Level
    rule_level: Level
    trend_level: Level
    ai_level: Level
    rule: RuleResult
    trends: list[TrendResult]
    reading: FormReading
    combined: Combined
    summary: str
    summary_method: str
    reasons: list[str] = field(default_factory=list)

    def findings_json(self, rules: Rules) -> dict[str, Any]:
        concepts = rules["symptom_concepts"]
        return {
            "rule": [vars(f) for f in self.rule.findings],
            "implausible": self.rule.implausible,
            "trend": [{"kind": t.kind, "level": t.level.code, "reason": t.reason, "baseline": t.baseline,
                       "upper": t.upper} for t in self.trends],
            "ai": [{**vars(f), "name": concepts.get(f.concept, {}).get("name", f.concept)}
                   for f in self.reading.findings],
            "unmapped": self.reading.unmapped,
            "conflicts": self.reading.conflicts,
            "ai_ok": self.reading.ok,
            "mask_ok": self.reading.mask_ok,
            "no_diacritics": self.reading.no_diacritics,
            "needs_human": self.combined.needs_human,
            "ai_escalated": self.combined.ai_escalated,
            "reasons": self.reasons,
        }


def assess_answers(
    form_code: str,
    answers: dict[str, Any],
    free_text: str,
    ga_weeks: int | None,
    rules: Rules,
    forms_cfg: FormsConfig,
    *,
    client: LlmClient | None,
    history: dict[str, list[tuple[datetime, float]]],
    on_insulin: bool = False,
    risk_groups: list[str] | None = None,
    ga_label: str | None = None,
    patient_name: str | None = None,
    pid: str | None = None,
    ner: NerFn | None | bool = True,
    at: datetime | None = None,
) -> AssessResult:
    at = at or now()
    rule = triage(answers, ga_weeks, on_insulin, rules)

    hist = {k: list(v) for k, v in history.items()}
    for kind, value, _slot in rule.readings:
        hist.setdefault(kind, []).append((at, value))
    t_level, trends = trend_level(hist, rules)

    reading = read_free_text(free_text, ga_weeks, client, rules, patient_name=patient_name, pid=pid, ner=ner)
    has_text = bool((free_text or "").strip())
    combined = combine(
        rule.level, t_level, reading.level,
        ai_ok=reading.ok or not has_text,
        ai_confidence=reading.confidence if has_text else 1.0,
        min_confidence=rules["arrival"]["ai_confidence_min"],
    )

    reasons = [f.reason for f in rule.findings if f.level != "green"]
    reasons += [f"Xu hướng: {t.reason}" for t in trends if t.level > Level.GREEN]
    concepts = rules["symptom_concepts"]
    reasons += [f"AI: {concepts.get(f.concept, {}).get('name', f.concept)}" for f in reading.findings
                if f.level != "green"]
    if reading.unmapped:
        reasons.append("AI: mối lo chưa phân loại")
    if combined.needs_human and has_text:
        reasons.append("AI không chắc / lỗi – chuyển người xem")

    bp = next((v for k, v, _ in reversed(rule.readings) if k == "bp_sys"), None)
    bpd = next((v for k, v, _ in reversed(rule.readings) if k == "bp_dia"), None)
    glu = next((v for k, v, _ in rule.readings if k == "glu_fast"), None)
    vitals: dict[str, tuple[str, str]] = {}
    sys_trend = next((t for t in trends if t.kind == "bp_sys"), None)
    if bp is not None:
        vitals["bp"] = (f"{bp:g}/{bpd:g}" if bpd is not None else f"{bp:g}",
                        "xu hướng tăng" if sys_trend and sys_trend.level > Level.GREEN else "")
    if glu is not None:
        vitals["glu"] = (f"{glu:g}", "")
    data = {
        "ga": ga_label, "risk_groups": risk_groups or [], "vitals": vitals,
        "concepts": [(concepts.get(f.concept, {}).get("name", f.concept), f.temporality)
                     for f in reading.findings if f.present and f.experiencer == "patient"],
        "level": combined.level.code, "reasons": reasons, "form": form_code,
    }
    text, method = summarize(data, rules, client)
    return AssessResult(level=combined.level, rule_level=combined.rule_level, trend_level=combined.trend_level,
                        ai_level=combined.ai_level, rule=rule, trends=trends, reading=reading, combined=combined,
                        summary=text, summary_method=method, reasons=reasons)
