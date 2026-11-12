"""Lịch gửi form (docs/04 B). Hàm thuần: trạng thái thai phụ + ngày → quyết định."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.config import Rules


@dataclass
class PatientState:
    patient_id: int
    code: str
    consent: str  # none | granted | withdrawn
    edd: date | None = None
    delivery_date: date | None = None
    last_visit: date | None = None
    next_appt: date | None = None
    modules: list[str] = field(default_factory=list)  # M-THA, M-DTD đã xác nhận
    on_insulin: bool = False
    sent: list[tuple[str, date]] = field(default_factory=list)  # (mã form, ngày gửi)
    answered: set[str] = field(default_factory=set)


@dataclass
class FormDecision:
    patient_id: int
    code: str
    form_code: str | None
    kind: str  # routine | module | postpartum
    send: bool
    reason: str
    skip_reason: str | None = None
    ga: tuple[int, int] | None = None
    gap_days: int | None = None
    last_visit: date | None = None
    next_appt: date | None = None


def ga_at(edd: date | None, d: date) -> tuple[int, int] | None:
    if edd is None:
        return None
    total = 280 - (edd - d).days
    if total < 0 or total > 44 * 7:
        return None
    return total // 7, total % 7


def ga_text(ga: tuple[int, int] | None) -> str:
    return f"{ga[0]}+{ga[1]}" if ga else "—"


def _in_window(ga: tuple[int, int], w: dict[str, Any]) -> bool:
    lo, hi = tuple(w["from"]), tuple(w["to"])
    return lo <= ga <= hi


def _last_sent(st: PatientState, form_code: str) -> date | None:
    dates = [d for c, d in st.sent if c == form_code]
    return max(dates) if dates else None


def plan_for_patient(st: PatientState, today: date, rules: Rules) -> list[FormDecision]:
    cfg = rules["forms"]
    if st.consent != "granted":
        return []  # chưa / đã rút đồng thuận: không xuất hiện trong lịch gửi form
    base = {"patient_id": st.patient_id, "code": st.code, "last_visit": st.last_visit, "next_appt": st.next_appt}
    out: list[FormDecision] = []

    def blocked(form_code: str) -> str | None:
        if form_code in st.answered:
            return "Đã có câu trả lời của form này"
        last = _last_sent(st, form_code)
        if last is not None and (today - last).days < cfg["resend_block_days"]:
            return f"Đã gửi form này ngày {last:%d/%m} (trong {cfg['resend_block_days']} ngày)"
        return None

    # Sau sinh: gửi từ ngày mốc, cho gửi bù thêm postpartum_grace_days ngày (hôm đó quên xuất SMS không bị mất form).
    if st.delivery_date is not None:
        days = (today - st.delivery_date).days
        grace = int(cfg["postpartum_grace_days"])
        for code, day in cfg["postpartum_days"].items():
            if day <= days <= day + grace:
                why = blocked(code)
                late = f" – gửi bù (hôm nay ngày {days})" if days > day else ""
                out.append(FormDecision(**base, form_code=code, kind="postpartum", send=why is None,
                                        reason=f"Sau sinh ngày {day}{late}", skip_reason=why))
        return out

    ga = ga_at(st.edd, today)
    if ga is None:
        return out
    gap = (st.next_appt - today).days if st.next_appt else None
    base.update(ga=ga, gap_days=gap)

    # Module bệnh lý: không xét khoảng trống, không xét tuần thai ≥ 36
    for module in st.modules:
        mcfg = cfg["modules"].get(module)
        if not mcfg:
            continue
        every = mcfg.get("every_days_if_insulin", mcfg["every_days"]) if st.on_insulin else mcfg["every_days"]
        last = _last_sent(st, module)
        if last is None or (today - last).days >= every:
            label = "hằng ngày" if every == 1 else f"mỗi {every} ngày"
            out.append(FormDecision(**base, form_code=module, kind="module", send=True,
                                    reason=f"Module {module}: {label}" + (" (dùng insulin)" if st.on_insulin and module == "M-DTD" else "")))

    # Form định kỳ theo cửa sổ tuần thai
    window = next((c for c, w in cfg["windows"].items() if _in_window(ga, w)), None)
    if window is None:
        return out
    skip: str | None = None
    if ga[0] >= cfg["stop_routine_from_ga_weeks"]:
        skip = f"Tuần thai ≥ {cfg['stop_routine_from_ga_weeks']} – dừng form định kỳ"
    elif gap is not None and gap <= cfg["skip_if_visit_within_days"]:
        skip = f"Có lịch hẹn trong {cfg['skip_if_visit_within_days']} ngày tới ({st.next_appt:%d/%m})"
    elif gap is not None and gap <= cfg["min_gap_days"]:
        skip = f"Khoảng trống đến lần hẹn tới chỉ {gap} ngày (≤ {cfg['min_gap_days']})"
    else:
        skip = blocked(window)
    gap_txt = f"khoảng trống {gap} ngày" if gap is not None else "chưa có lịch hẹn tới"
    out.append(FormDecision(**base, form_code=window, kind="routine", send=skip is None,
                            reason=f"Tuần thai {ga_text(ga)} trong cửa sổ {window}; {gap_txt}", skip_reason=skip))
    return out


def plan_forms(states: list[PatientState], today: date, rules: Rules) -> list[FormDecision]:
    out: list[FormDecision] = []
    for st in states:
        out += plan_for_patient(st, today, rules)
    return out


def suggested_modules(risk_flags: list[str]) -> list[str]:
    """Đề xuất module từ risk_flags – điều dưỡng phải xác nhận."""
    m = {"HTN": "M-THA", "PREECLAMPSIA": "M-THA", "GDM": "M-DTD"}
    return sorted({m[f] for f in risk_flags if f in m})
