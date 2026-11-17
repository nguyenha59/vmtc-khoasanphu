"""Đánh giá 1 câu trả lời form: lớp 1 quy tắc + lớp 2 xu hướng + lớp 3 AI → gộp → tóm tắt."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from core.ai.clinical_facts import blood_pressure_facts
from core.ai.combine import Combined, combine
from core.ai.form_reader import FormReading, read_free_text
from core.ai.llm_client import LlmClient
from core.ai.masking import NerFn
from core.ai.summary import summarize
from core.ai.trends import TrendResult, trend_level
from core.config import FormsConfig, Rules
from core.db import now
from core.levels import Level, max_level
from core.rules.triage_rules import Finding, RuleResult, triage


def _finding_name(f: Any, concepts: dict[str, Any]) -> str:
    name = concepts.get(f.concept, {}).get("name", f.concept)
    if f.concept == "HEADACHE" and f.severity == "severe":
        return f"{name} nặng / không đỡ"
    if "override" in str(getattr(f, "source", "")):
        who = ("người khác bị" if f.experiencer == "other" else "đã hết" if f.temporality == "past_resolved"
               else "không có")
        return f"{name} – AI đọc là {who}, hạ từ đỏ xuống vàng – gọi xác minh"
    if "unverified" in str(getattr(f, "source", "")):
        return f"{name} – AI nghi ngờ, cần gọi xác minh"
    if getattr(f, "assertion_status", "") == "uncertain":
        return f"{name} – thông tin đọc/nghe được, cần gọi xác minh"
    return name


def _effective(f: Any) -> bool:
    return bool(f.present and f.experiencer == "patient" and f.temporality != "past_resolved"
                and getattr(f, "assertion_status", "affirmed") == "affirmed")


def text_combo_findings(reading: FormReading, readings: list[tuple[str, float, str | None]],
                        rules: Rules) -> list[Finding]:
    """Kết hợp triệu chứng trong phần tự viết → Đỏ (config text_combos, HEADACHE.red_with_bp). Chỉ nâng mức."""
    have = {f.concept for f in reading.findings if _effective(f)}
    out: list[Finding] = []
    for c in rules.get("text_combos") or []:
        if set(c.get("all", [])) <= have and (not c.get("any_of") or have & set(c["any_of"])):
            out.append(Finding(c["code"], Level.parse(c.get("level", "red")).code, c["reason"]))
    th = rules["thresholds"]
    sys_v = max((v for k, v, _ in readings if k == "bp_sys"), default=0)
    dia_v = max((v for k, v, _ in readings if k == "bp_dia"), default=0)
    for code, c in (rules.get("symptom_concepts") or {}).items():
        if c.get("red_with_bp") and code in have and (sys_v >= th["bp_yellow"]["sys"] or dia_v >= th["bp_yellow"]["dia"]):
            out.append(Finding(f"TXT_{code}_BP", Level.RED.code,
                               f"{c['name']} kèm huyết áp ≥ {th['bp_yellow']['sys']}/{th['bp_yellow']['dia']}"))
    return out


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
            "ai": [{**vars(f), "name": _finding_name(f, concepts)}
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
    skip_llm: bool = False,
) -> AssessResult:
    at = at or now()
    rule = triage(answers, ga_weeks, on_insulin, rules)
    measurements = blood_pressure_facts(free_text or "")
    for measurement in measurements:
        if not measurement.usable:
            continue
        measured = triage({"BP_SYS": measurement.systolic, "BP_DIA": measurement.diastolic},
                          ga_weeks, on_insulin, rules)
        rule.level = max_level(rule.level, measured.level)
        rule.findings += [Finding("TEXT_" + f.code, f.level, f.reason + " (số đo trong phần tự viết)")
                          for f in measured.findings]
        rule.implausible += measured.implausible
        rule.readings += [r for r in measured.readings if r not in rule.readings]

    hist = {k: list(v) for k, v in history.items()}
    for kind, value, _slot in rule.readings:
        hist.setdefault(kind, []).append((at, value))
    t_level, trends = trend_level(hist, rules)

    skip_llm = skip_llm or rule.level == Level.RED
    active_client = None if skip_llm else client
    reading = read_free_text(free_text, ga_weeks, active_client, rules, patient_name=patient_name, pid=pid, ner=ner)
    reading.raw["measurement_facts"] = [asdict(m) for m in measurements]
    if skip_llm:
        reading.raw["llm_status"] = "skipped_confirmed_red"
    if any(m.experiencer == "patient" and (m.assertion_status == "uncertain" or m.temporality == "unknown")
           for m in measurements):
        reading.conflicts.append("Số đo tự viết chưa rõ thời điểm / đơn vị – chuyển người xem")
        reading.confidence = min(reading.confidence, max(0.0, float(rules["arrival"]["ai_confidence_min"]) - 0.01))
    combos = text_combo_findings(reading, rule.readings, rules)
    if combos:
        rule.findings += combos
        rule.level = max_level(rule.level, *(f.level for f in combos))
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
    reasons += [f"AI: {_finding_name(f, concepts)}" for f in reading.findings
                if f.level != "green"]
    if reading.unmapped:
        reasons.append("AI: mối lo chưa phân loại")
    if skip_llm:
        reasons.append("Bỏ qua AI tự viết để xử lý cảnh báo đỏ ngay; vẫn áp dụng quy tắc và số đo xác định")
    elif combined.needs_human and has_text:
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
        "concepts": [(_finding_name(f, concepts), f.temporality)
                     for f in reading.findings if f.present and f.experiencer == "patient"],
        "level": combined.level.code, "reasons": reasons, "form": form_code,
    }
    text, method = summarize(data, rules, active_client, ner=ner)
    return AssessResult(level=combined.level, rule_level=combined.rule_level, trend_level=combined.trend_level,
                        ai_level=combined.ai_level, rule=rule, trends=trends, reading=reading, combined=combined,
                        summary=text, summary_method=method, reasons=reasons)
