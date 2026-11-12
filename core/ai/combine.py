"""Gộp mức 3 lớp (docs/04 C4). Luật khóa: AI chỉ được NÂNG mức, không bao giờ hạ."""

from __future__ import annotations

from dataclasses import dataclass

from core.levels import Level, max_level


@dataclass(frozen=True)
class Combined:
    level: Level
    rule_level: Level
    trend_level: Level
    ai_level: Level
    ai_escalated: bool  # AI làm tăng mức so với quy tắc + xu hướng
    needs_human: bool  # AI lỗi / không chắc → chuyển người


def combine(
    rule_level: Level | str,
    trend_level: Level | str,
    ai_level: Level | str | None,
    *,
    ai_ok: bool,
    ai_confidence: float | None,
    min_confidence: float,
) -> Combined:
    rule_l, trend_l = Level.parse(rule_level), Level.parse(trend_level)
    ai_l = Level.parse(ai_level)
    needs_human = False
    if not ai_ok or ai_confidence is None or ai_confidence < min_confidence:
        ai_l = max_level(ai_l, Level.YELLOW)  # AI lỗi / sai lược đồ / không chắc → ít nhất vàng
        needs_human = True
    level = max_level(rule_l, trend_l, ai_l)
    assert level >= rule_l, "Vi phạm luật khóa: mức gộp thấp hơn mức quy tắc"
    return Combined(level=level, rule_level=rule_l, trend_level=trend_l, ai_level=ai_l,
                    ai_escalated=ai_l > max_level(rule_l, trend_l), needs_human=needs_human)
