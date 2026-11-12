"""Tính giờ đến bằng quy tắc + gợi ý AI đã kiểm chứng (docs/04 A3, A4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from core.config import Rules, parse_hhmm
from core.rules.note_patterns import find_explicit_time
from core.textutil import word_pattern


@dataclass
class NoteAI:
    """Kết quả AI đọc ghi chú đã tổng hợp (từ note_extractor)."""

    ok: bool
    confidence: float
    explicit_arrival_time: time | None = None
    minutes_early: int | None = None
    needs_fasting: bool = False
    ga_weeks: int | None = None
    ga_days: int | None = None
    risk_flags: list[str] = field(default_factory=list)
    asks_confirmation: bool = False
    evidence: str = ""


@dataclass
class ArrivalResult:
    arrival_time: time | None
    method: str  # rule | ai
    rule_applied: str
    reasons: list[str] = field(default_factory=list)
    rule_time: time | None = None
    rule_minutes: int | None = None
    explicit_time: time | None = None
    ai_time: time | None = None
    details: list[str] = field(default_factory=list)
    waiting: bool = False  # lịch "khám chờ": không có giờ đến, tin nhắn ghi nhãn


def waiting_label(cfg: dict[str, Any]) -> str | None:
    """Nhãn khám chờ trong tin nhắn, None nếu chưa cấu hình."""
    w = cfg.get("waiting_slot")
    return str(w["label"]) if w else None


def arrival_text(res: ArrivalResult, cfg: dict[str, Any]) -> str:
    """Giờ đến dạng chữ như trong tin nhắn: "HH:MM", nhãn khám chờ, hoặc "" nếu chưa có."""
    if res.arrival_time is not None:
        return res.arrival_time.strftime("%H:%M")
    return (waiting_label(cfg) or "") if res.waiting else ""


def is_waiting_slot(appt_time: time, cfg: dict[str, Any]) -> bool:
    w = cfg.get("waiting_slot")
    if not w:
        return False
    return parse_hhmm(w["from"]) <= appt_time < parse_hhmm(w["to"])


def _shift(t: time, minutes: int) -> time:
    return (datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)).time()


def _diff_minutes(a: time, b: time) -> float:
    d = date(2000, 1, 1)
    return abs((datetime.combine(d, a) - datetime.combine(d, b)).total_seconds()) / 60


def _service_matches(service: str, keyword: str) -> bool:
    if keyword == "*":
        return True
    return bool(word_pattern(keyword).search(service))


def rule_minutes_early(services: list[str], table: list[dict[str, Any]]) -> tuple[int | None, str]:
    """Mức sớm nhất (nhiều phút nhất) trong các dịch vụ của lịch đã gộp."""
    best: int | None = None
    label = ""
    for svc in services:
        for entry in table:
            if any(_service_matches(svc, k) for k in entry["keywords"]):
                m = int(entry["minutes"])
                if best is None or m > best:
                    best, label = m, svc
                break
    return best, label


def is_valid_arrival(arrival: time, appt: time, cfg: dict[str, Any]) -> tuple[bool, str]:
    earliest = parse_hhmm(cfg["earliest_arrival"])
    if arrival > appt:
        return False, f"giờ đến {arrival:%H:%M} sau giờ hẹn {appt:%H:%M}"
    if arrival < earliest:
        return False, f"giờ đến {arrival:%H:%M} sớm hơn {earliest:%H:%M}"
    if _diff_minutes(appt, arrival) > cfg["max_minutes_early"]:
        return False, f"sớm hơn giờ hẹn quá {cfg['max_minutes_early']} phút"
    return True, ""


def compute_arrival(
    appt_time: time, services: list[str], note: str, ai: NoteAI | None, rules: Rules
) -> ArrivalResult:
    cfg = rules["arrival"]
    diff_max = cfg["ai_rule_diff_minutes"]
    details: list[str] = []

    rule_min, rule_svc = rule_minutes_early(services, cfg["early_by_service"])
    rule_time: time | None = None
    if rule_min is not None:
        candidate = _shift(appt_time, -rule_min)
        ok, why = is_valid_arrival(candidate, appt_time, cfg)
        if ok:
            rule_time = candidate
        else:
            details.append(f"Quy tắc dịch vụ bị loại: {why}")
    rule_label = (
        f"Bảng đến sớm theo dịch vụ: {rule_svc} → sớm {rule_min} phút" if rule_min is not None else ""
    )

    ai_time: time | None = None
    ai_kind = ""
    if ai is not None and ai.ok:
        if ai.explicit_arrival_time is not None:
            ai_time, ai_kind = ai.explicit_arrival_time, "giờ đến ghi rõ"
        elif ai.minutes_early:
            ai_time, ai_kind = _shift(appt_time, -int(ai.minutes_early)), f"đến trước {ai.minutes_early} phút"

    res = ArrivalResult(
        arrival_time=None, method="rule", rule_applied="", rule_time=rule_time,
        rule_minutes=rule_min, ai_time=ai_time, details=details,
    )

    # 0. Khám chờ: không tính giờ đến. Ghi chú vẫn có giờ / dặn đến sớm → người kiểm tra.
    rx_time = find_explicit_time(note)
    res.explicit_time = rx_time
    if is_waiting_slot(appt_time, cfg):
        res.waiting, res.rule_applied = True, f"Khám chờ (lịch {cfg['waiting_slot']['from']}–{cfg['waiting_slot']['to']})"
        if rx_time is not None or ai_time is not None:
            res.reasons.append("WAITING_NOTE_TIME")
            shown = rx_time or ai_time
            details.append(f"Lịch khám chờ nhưng ghi chú có giờ đến {shown:%H:%M} – cần người xác nhận")
        return res

    # 1. Giờ bác sĩ ghi rõ (regex)
    if rx_time is not None:
        ok, why = is_valid_arrival(rx_time, appt_time, cfg)
        if ok:
            res.arrival_time, res.rule_applied = rx_time, "Giờ bác sĩ ghi rõ trong ghi chú"
            if ai_time is not None and _diff_minutes(ai_time, rx_time) > diff_max:
                res.reasons.append("AI_RULE_DIFF")
                details.append(f"AI đọc {ai_kind} {ai_time:%H:%M}, khác giờ ghi rõ {rx_time:%H:%M}")
            return res
        details.append(f"Giờ ghi trong ghi chú bị loại: {why}")
        res.arrival_time, res.rule_applied = rule_time, rule_label
        res.reasons.append("NO_RULE")
        return res

    # 3. Gợi ý AI – chỉ áp dụng khi qua kiểm chứng A4
    if ai_time is not None:
        ok, why = is_valid_arrival(ai_time, appt_time, cfg)
        if not ok:
            details.append(f"Gợi ý AI ({ai_kind}) bị loại: {why}")
        else:
            res.arrival_time, res.method = ai_time, "ai"
            res.rule_applied = f"AI đọc ghi chú: {ai_kind}"
            if rule_time is not None and _diff_minutes(ai_time, rule_time) > diff_max:
                res.reasons.append("AI_RULE_DIFF")
                details.append(
                    f"AI ({ai_time:%H:%M}) và quy tắc ({rule_time:%H:%M}) lệch > {diff_max} phút"
                )
            return res

    # 2. Bảng đến sớm theo dịch vụ
    if rule_time is not None:
        res.arrival_time, res.rule_applied = rule_time, rule_label
        return res
    res.reasons.append("NO_RULE")
    res.rule_applied = "Không quy tắc nào áp dụng được"
    return res
