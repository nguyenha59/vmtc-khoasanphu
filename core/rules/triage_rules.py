"""Lớp 1 – quy tắc phân mức từ câu trả lời có cấu trúc (docs/04 C1). Luôn chạy, kể cả khi AI lỗi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import Rules
from core.levels import Level, max_level

YES = {"có", "co", "yes", "true", "1", "x"}

RED_ITEMS = {
    "C1": "Ra máu âm đạo",
    "C2": "Đau bụng nhiều",
    "C3": "Ra nước âm đạo",
    "C6": "Đau đầu / nhìn mờ / đau thượng vị",
    "C8": "Thai máy giảm",
    "DYSPNEA": "Khó thở",
    "CALF": "Đau sưng một bên bắp chân",
}
YELLOW_ITEMS = {
    "C4": "Sốt",
    "C5": "Tiểu buốt",
    "ITCH": "Ngứa lòng bàn tay/chân",
    "C7": "Phù mặt / tay",
    "NAUSEA": "Nghén nặng",
    "MISSED_MED": "Quên thuốc",
}


@dataclass
class Finding:
    code: str
    level: str
    reason: str


@dataclass
class RuleResult:
    level: Level
    findings: list[Finding] = field(default_factory=list)
    # chỉ số hợp lệ để lưu vital_reading: (loại, giá trị, buổi am/pm/None)
    readings: list[tuple[str, float, str | None]] = field(default_factory=list)
    implausible: list[str] = field(default_factory=list)


def is_yes(v: Any) -> bool:
    return str(v).strip().lower() in YES if v is not None else False


def num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def triage(answers: dict[str, Any], ga_weeks: int | None, on_insulin: bool, rules: Rules) -> RuleResult:
    th = rules["thresholds"]
    out = RuleResult(level=Level.GREEN)

    def add(code: str, level: Level, reason: str) -> None:
        out.findings.append(Finding(code, level.code, reason))

    for q, label in RED_ITEMS.items():
        if is_yes(answers.get(q)):
            add(q, Level.RED, label)
    for q, label in YELLOW_ITEMS.items():
        if is_yes(answers.get(q)):
            add(q, Level.YELLOW, label)
    if is_yes(answers.get("C9")):
        term = th.get("contractions_red_before_ga_weeks", 37)
        if ga_weeks is not None and ga_weeks >= term:
            add("C9", Level.parse(th.get("contractions_level_at_term", "yellow")), f"Gò bụng đều ≥ {term} tuần")
        else:
            add("C9", Level.RED, f"Gò bụng đều < {term} tuần")
    if is_yes(answers.get("C4")) and (is_yes(answers.get("C2")) or is_yes(answers.get("C3"))):
        add("FEVER_PLUS", Level.RED, "Sốt kèm đau bụng nhiều / ra nước")
    if is_yes(answers.get("C7")) and is_yes(answers.get("C6")):
        add("EDEMA_PLUS", Level.RED, "Phù mặt/tay kèm đau đầu / nhìn mờ")
    if is_yes(answers.get("HYPO")):
        if on_insulin:
            add("HYPO", Level.RED, "Hạ đường huyết khi dùng insulin")
        else:
            add("HYPO", Level.YELLOW, "Hạ đường huyết")

    # Huyết áp
    imp = th["implausible"]
    for prefix in ("BP_AM", "BP_PM", "BP"):
        sys_v, dia_v = num(answers.get(f"{prefix}_SYS")), num(answers.get(f"{prefix}_DIA"))
        if sys_v is None and dia_v is None:
            continue
        if sys_v is not None and not (imp["sys_min"] <= sys_v <= imp["sys_max"]):
            out.implausible.append(f"{prefix}_SYS={sys_v:g}")
            add(f"{prefix}_IMPLAUSIBLE", Level.YELLOW, f"Huyết áp {sys_v:g} phi lý – nghi nhập sai")
            continue
        s_v, d_v = sys_v or 0, dia_v or 0
        slot = {"BP_AM": "am", "BP_PM": "pm"}.get(prefix)
        if sys_v is not None:
            out.readings.append(("bp_sys", sys_v, slot))
        if dia_v is not None:
            out.readings.append(("bp_dia", dia_v, slot))
        if s_v >= th["bp_red"]["sys"] or d_v >= th["bp_red"]["dia"]:
            add(prefix, Level.RED, f"Huyết áp {s_v:g}/{d_v:g} ≥ {th['bp_red']['sys']}/{th['bp_red']['dia']}")
        elif s_v >= th["bp_yellow"]["sys"] or d_v >= th["bp_yellow"]["dia"]:
            add(prefix, Level.YELLOW, f"Huyết áp {s_v:g}/{d_v:g} ≥ {th['bp_yellow']['sys']}/{th['bp_yellow']['dia']}")

    # Đường huyết
    gy = th["glucose_yellow"]
    for q, key, kind, label in (("GLU_FAST", "fasting", "glu_fast", "lúc đói"),
                                ("GLU_1H", "after_1h", "glu_1h", "sau ăn 1 giờ"),
                                ("GLU_2H", "after_2h", "glu_2h", "sau ăn 2 giờ")):
        v = num(answers.get(q))
        if v is None:
            continue
        if not (imp["glu_min"] <= v <= imp["glu_max"]):
            out.implausible.append(f"{q}={v:g}")
            add(f"{q}_IMPLAUSIBLE", Level.YELLOW, f"Đường huyết {v:g} phi lý – nghi nhập sai")
            continue
        out.readings.append((kind, v, None))
        if v >= gy[key]:
            add(q, Level.YELLOW, f"Đường huyết {label} {v:g} ≥ {gy[key]} mmol/L")

    w = num(answers.get("WEIGHT"))
    if w is not None and 25 <= w <= 200:
        out.readings.append(("weight", w, None))

    # EPDS
    epds = [num(answers.get(f"E{i}")) for i in range(1, 11)]
    if any(v is not None for v in epds):
        e10 = epds[9] or 0
        if e10 > 0:  # khóa: câu 10 khác "không bao giờ" → đỏ
            add("EPDS_10", Level.RED, "EPDS câu 10: có ý nghĩ tự làm hại")
        total = sum(v or 0 for v in epds)
        if total >= th["epds_threshold"]:
            add("EPDS_TOTAL", Level.YELLOW, f"EPDS tổng {total:g} ≥ {th['epds_threshold']}")

    out.level = max_level(*(f.level for f in out.findings)) if out.findings else Level.GREEN
    return out
