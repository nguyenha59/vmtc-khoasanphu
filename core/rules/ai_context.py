"""Tóm tắt lâm sàng ĐÃ PHÂN LOẠI, KHÔNG định danh, gửi kèm cho AI chọn câu (docs/13 mục 5c). Hàm thuần.

Chỉ gửi nhãn nhóm (vd. "Huyết áp gần nhất: cao (≥140/90)"), không gửi số đo gốc, ngày tháng, ghi chú tự do.
Ngưỡng lấy từ `thresholds` / `trends`; từ khóa thuốc / tiền sử lấy từ `personalization.ai_context` (bác sĩ duyệt).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from core.ai.trends import trend_level
from core.config import Rules
from core.levels import Level
from core.rules.personalize import _clauses, _keyword_hit
from core.textutil import deaccent

GLUCOSE = {"glu_fast": ("fasting", "Đường huyết lúc đói"), "glu_1h": ("after_1h", "Đường huyết sau ăn 1 giờ"),
           "glu_2h": ("after_2h", "Đường huyết sau ăn 2 giờ")}


def _cfg(rules: Rules) -> dict[str, Any]:
    return (rules.get("personalization") or {}).get("ai_context") or {}


def context_as_of(today: date, as_of: datetime | None = None) -> datetime:
    """An explicit replay timestamp, or the latest available instant of the requested day."""
    from core.db import now

    return min(as_of or now(), datetime.combine(today, datetime.max.time()))


def _recent(readings: list[tuple[datetime, float]], today: date, days: int,
            as_of: datetime | None = None) -> list[tuple[datetime, float]]:
    since = datetime.combine(today - timedelta(days=days), datetime.min.time())
    until = context_as_of(today, as_of)
    return sorted(r for r in readings if since <= r[0] <= until and math.isfinite(r[1]))


def measurement_labels(readings: dict[str, list[tuple[datetime, float]]], today: date, rules: Rules,
                       *, as_of: datetime | None = None) -> list[str]:
    """Số đo gần nhất trong N ngày → nhãn đã phân loại + xu hướng (EWMA, lớp 2)."""
    days = int(_cfg(rules).get("measurement_days", 14))
    th = rules.get("thresholds") or {}
    out: list[str] = []
    sys_r = _recent(readings.get("bp_sys", []), today, days, as_of)
    dia_r = _recent(readings.get("bp_dia", []), today, days, as_of)
    if sys_r and dia_r:
        s, d = sys_r[-1][1], dia_r[-1][1]
        red, yel = th.get("bp_red", {}), th.get("bp_yellow", {})
        if s >= red.get("sys", 999) or d >= red.get("dia", 999):
            cat = f"rất cao (≥{red['sys']}/{red['dia']})"
        elif s >= yel.get("sys", 999) or d >= yel.get("dia", 999):
            cat = f"cao (≥{yel['sys']}/{yel['dia']})"
        else:
            cat = f"dưới ngưỡng {yel.get('sys')}/{yel.get('dia')}"
        min_n = int((rules.get("trends") or {}).get("min_readings", 3))
        if len(sys_r) < min_n:
            trend = "chưa đủ số đo để xem xu hướng"
        else:
            lvl, _ = trend_level({"bp_sys": sys_r, "bp_dia": dia_r}, rules)
            trend = "xu hướng tăng" if lvl > Level.GREEN else "ổn định"
        out.append(f"Huyết áp gần nhất: {cat}, {trend}")
    glu = th.get("glucose_yellow", {})
    for kind, (key, name) in GLUCOSE.items():
        r = _recent(readings.get(kind, []), today, days, as_of)
        if r and key in glu:
            high = sum(1 for _, v in r if v >= glu[key])
            out.append(f"{name}: {'cao hơn' if r[-1][1] >= glu[key] else 'dưới'} ngưỡng {glu[key]}"
                       + (f" ({high}/{len(r)} lần cao)" if len(r) > 1 else ""))
    return out


def medication_statuses(on_insulin: bool, latest_plan: list[str], rules: Rules) -> dict[str, str]:
    """Keep explicit stopped/planned mentions separate from active medication facts."""
    cfg = _cfg(rules)
    found = {"INSULIN": "active"} if on_insulin else {}
    neg = (rules.get("personalization") or {}).get("negation_words", [])
    for group, kws in (cfg.get("medication_keywords") or {}).items():
        for line in latest_plan:
            for clause in _clauses(str(line)):
                plain = deaccent(clause.lower())
                for keyword in kws:
                    match = re.search(rf"(?<!\w){re.escape(deaccent(keyword.lower()))}(?!\w)", plain)
                    if match is None:
                        continue
                    before = plain[:match.start()]
                    original_before = clause.lower()[:match.start()]
                    if (re.search(r"\b(?:ngung|ngung su dung|tam ngung|dung su dung)\s*$", before)
                            or re.search(r"\b(?:dừng|tạm dừng)\s*$", original_before)):
                        found[group] = "stopped"
                    elif re.search(r"\b(?:can nhac|du kien|xem xet|neu|chi dung khi|se dung)\b", before):
                        found[group] = "planned"
                    elif not _keyword_hit(clause, keyword, neg):
                        found[group] = "stopped"
                    elif re.search(r"\b(?:dung|tiep tuc|bo sung|uong|duy tri)\b", plain[:match.end()]):
                        found[group] = "active"
                    else:
                        found[group] = "unknown"
    return found


def medication_labels(on_insulin: bool, latest_plan: list[str], rules: Rules) -> list[str]:
    names: dict[str, str] = _cfg(rules).get("medication_names", {})
    return [names.get(group, group) for group, status in medication_statuses(on_insulin, latest_plan, rules).items()
            if status == "active"]


def obstetric_labels(history: dict[str, Any] | None, rules: Rules) -> list[str]:
    """Tiền sử sản khoa dạng có / không (chỉ liệt kê mục CÓ)."""
    cfg = _cfg(rules)
    names: dict[str, str] = cfg.get("obstetric_names", {})
    neg = (rules.get("personalization") or {}).get("negation_words", [])
    lines = [str(x) for x in ((history or {}).get("obstetric_history") or [])]
    return [names.get(g, g) for g, kws in (cfg.get("obstetric_keywords") or {}).items()
            if any(_keyword_hit(c, k, neg) for line in lines for c in _clauses(line) for k in kws)]


def visit_gap(last_visit: date | None, next_visit: date | None, today: date) -> dict[str, int | None]:
    return {"days_since_last_visit": (today - last_visit).days if last_visit else None,
            "days_to_next_visit": (next_visit - today).days if next_visit else None}


def clinical_summary(*, readings: dict[str, list[tuple[datetime, float]]], on_insulin: bool, latest_plan: list[str],
                     history: dict[str, Any] | None, last_visit: date | None, next_visit: date | None, today: date,
                     rules: Rules, as_of: datetime | None = None,
                     lab_labels: list[str] | None = None) -> dict[str, Any]:
    """Gộp 4 nhóm theo `personalization.ai_context.fields` (nhóm không bật → không gửi)."""
    fields = set(_cfg(rules).get("fields", []))
    out: dict[str, Any] = {}
    if "measurements" in fields:
        out["measurements"] = measurement_labels(readings, today, rules, as_of=as_of) + (lab_labels or [])
    if "medications" in fields:
        out["medications"] = medication_labels(on_insulin, latest_plan, rules)
        out["medication_statuses"] = [{"group": group, "status": status}
                                      for group, status in medication_statuses(on_insulin, latest_plan, rules).items()]
    if "obstetric_history" in fields:
        out["obstetric_history"] = obstetric_labels(history, rules)
    if "visit_gap" in fields:
        out.update(visit_gap(last_visit, next_visit, today))
    return out
