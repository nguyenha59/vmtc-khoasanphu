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
    flags: list[str] = field(default_factory=list)  # nhóm nguy cơ (vd. PREECLAMPSIA → M-THA 3 lần/tuần)


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
    merged_into: str | None = None  # gộp vào form chính cùng ngày (1 đường dẫn / thai phụ / ngày)


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


WEEKDAY_VI = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def module_weekdays(module: str, mcfg: dict[str, Any], on_insulin: bool, flags: list[str]) -> list[int] | None:
    """Ngày trong tuần gửi module (0 = Thứ 2). None = cấu hình cũ every_days."""
    if "weekdays" not in mcfg:
        return None
    days = list(mcfg["weekdays"])
    if on_insulin and mcfg.get("weekdays_if_insulin"):
        days = list(mcfg["weekdays_if_insulin"])
    for flag, alt in (mcfg.get("weekdays_if_flag") or {}).items():
        if flag in flags:
            days = list(alt)
    return days


def schedule_text(module: str, mcfg: dict[str, Any], on_insulin: bool = False, flags: list[str] | None = None) -> str:
    days = module_weekdays(module, mcfg, on_insulin, flags or [])
    if days is not None:
        return f"{len(days)} lần/tuần ({', '.join(WEEKDAY_VI[d] for d in days)})"
    if "every_days" in mcfg:
        return "hằng ngày" if mcfg["every_days"] == 1 else f"mỗi {mcfg['every_days']} ngày"
    return "ghép vào form theo mốc"


def _last_sent(st: PatientState, form_code: str) -> date | None:
    dates = [d for c, d in st.sent if c == form_code]
    return max(dates) if dates else None


# Thứ tự chọn form chính khi nhiều form đến hạn cùng ngày: form định kỳ / sau sinh (có câu trả lời được ghi nhận
# là "đã trả lời form này") → bộ theo tình trạng → module bệnh lý.
_PRIMARY_ORDER = {"routine": 0, "postpartum": 0, "pack": 1, "module": 2}


def merge_same_day(decisions: list[FormDecision]) -> list[FormDecision]:
    """Mỗi thai phụ mỗi ngày CHỈ một đường dẫn: các form đến hạn cùng ngày gộp vào một form chính.
    Câu hỏi của form gộp = hợp các form, mỗi câu một lần (core.rules.personalize.compose_merged)."""
    due = [d for d in decisions if d.send]
    if len(due) <= 1:
        return decisions
    primary = min(due, key=lambda d: (_PRIMARY_ORDER.get(_kind(d), 9), due.index(d)))
    for d in due:
        if d is not primary:
            d.send, d.merged_into = False, primary.form_code
            d.skip_reason = f"Gộp vào {primary.form_code} (mỗi ngày 1 đường dẫn, không hỏi trùng)"
    return decisions


def _kind(d: FormDecision) -> str:
    return "pack" if (d.form_code or "").startswith("P-") else d.kind


def plan_for_patient(st: PatientState, today: date, rules: Rules) -> list[FormDecision]:
    decisions = _plan_for_patient(st, today, rules)
    # 1 đường dẫn / thai phụ / ngày giữ cả QUA NHIỀU LẦN lập lịch: hôm nay đã xuất một đường dẫn (vd F5) thì form đến
    # hạn thêm sau đó (vd module vừa xác nhận) chờ lần gửi tới, không tạo đường dẫn thứ hai.
    sent_today = sorted({c for c, d in st.sent if d == today and c})
    if sent_today:
        for d in decisions:
            if d.send:
                d.send = False
                d.skip_reason = (f"Hôm nay đã gửi đường dẫn {', '.join(sent_today)} – mỗi thai phụ 1 đường dẫn/ngày, "
                                 "form này gửi ở lần tới")
    return merge_same_day(decisions)


def _plan_for_patient(st: PatientState, today: date, rules: Rules) -> list[FormDecision]:
    cfg = rules["forms"]
    if st.consent != "granted":
        return []  # chưa / đã rút đồng thuận: không xuất hiện trong lịch gửi form
    base = {"patient_id": st.patient_id, "code": st.code, "last_visit": st.last_visit, "next_appt": st.next_appt}
    out: list[FormDecision] = []

    def blocked(form_code: str, *, once: bool = False) -> str | None:
        if form_code in st.answered:
            return "Đã có câu trả lời của form này"
        last = _last_sent(st, form_code)
        if last is not None and once:
            return f"Đã gửi form mốc này ngày {last:%d/%m} – mỗi mốc gửi 1 lần (+ nhắc 1 lần)"
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
    since_visit = (today - st.last_visit).days if st.last_visit else None
    near_visit: str | None = None  # docs/13: sắp khám / vừa khám → không gửi (mọi loại form trước sinh)
    if gap is not None and gap <= cfg["skip_if_visit_within_days"]:
        near_visit = f"Có lịch hẹn trong {cfg['skip_if_visit_within_days']} ngày tới ({st.next_appt:%d/%m})"
    elif since_visit is not None and since_visit <= int(cfg.get("skip_if_visited_within_days", 0)):
        near_visit = f"Vừa khám ngày {st.last_visit:%d/%m} (≤ {cfg.get('skip_if_visited_within_days')} ngày)"

    # Module bệnh lý: ngày cố định trong tuần; không xét khoảng trống, không xét tuần thai ≥ 36
    packs_cfg = cfg.get("packs") or {}
    active_packs: list[str] = []
    for module in st.modules:
        mcfg = cfg["modules"].get(module)
        if not mcfg:
            continue
        # Bộ câu hỏi theo tình trạng chỉ có nghĩa trong cửa sổ tuần thai của nó (vd. sinh non 22–36+6 tuần).
        if "ga_from" in mcfg and not (tuple(mcfg["ga_from"]) <= ga <= tuple(mcfg["ga_to"])):
            continue
        if module.startswith("P-") and not packs_cfg.get("standalone", True):
            active_packs.append(module)  # không gửi riêng – ghép vào form mốc (personalize.compose_questions)
            continue
        days = module_weekdays(module, mcfg, st.on_insulin, st.flags)
        if days is not None:
            if today.weekday() not in days or _last_sent(st, module) == today:
                continue
        else:
            every = mcfg.get("every_days_if_insulin", mcfg.get("every_days")) if st.on_insulin else mcfg.get("every_days")
            if every is None:  # không có lịch gửi riêng (vd bộ P- chỉ có cửa sổ tuần thai) → ghép vào form mốc
                if module.startswith("P-"):
                    active_packs.append(module)
                continue
            last = _last_sent(st, module)
            if module.startswith("P-"):
                routine = [d for c, d in st.sent if c in cfg["windows"]]
                last = max([x for x in (last, *routine) if x is not None], default=None)
            if last is not None and (today - last).days < every:
                continue
        label = schedule_text(module, mcfg, st.on_insulin, st.flags)
        kind_vi = "Bộ câu theo tình trạng" if module.startswith("P-") else "Module"
        out.append(FormDecision(**base, form_code=module, kind="module", send=near_visit is None,
                                reason=f"{kind_vi} {module}: {label}" + (" (dùng insulin)" if st.on_insulin and module == "M-DTD" else ""),
                                skip_reason=near_visit))

    # Bộ theo tình trạng – 1 form giữa khoảng khi 2 lần khám cách nhau quá xa (docs/13, người dùng 13/11/2026)
    mid = int(packs_cfg.get("mid_gap_days", 0) or 0)
    if active_packs and mid and st.last_visit and st.next_appt and near_visit is None:
        span = (st.next_appt - st.last_visit).days
        sent_since = [c for c, d in st.sent if d > st.last_visit]
        if span > mid and (today - st.last_visit).days >= span // 2 and not sent_since:
            for pack in active_packs:
                out.append(FormDecision(**base, form_code=pack, kind="module", send=True,
                                        reason=f"Bộ câu theo tình trạng {pack}: 2 lần khám cách nhau {span} ngày "
                                               f"(> {mid}) – 1 lần giữa khoảng"))

    # Form định kỳ theo cửa sổ tuần thai
    window = next((c for c, w in cfg["windows"].items() if _in_window(ga, w)), None)
    if window is None:
        return out
    skip: str | None = None
    if ga[0] >= cfg["stop_routine_from_ga_weeks"]:
        skip = f"Tuần thai ≥ {cfg['stop_routine_from_ga_weeks']} – dừng form định kỳ"
    elif near_visit:
        skip = near_visit
    elif gap is not None and gap <= cfg["min_gap_days"]:
        skip = f"Khoảng trống đến lần hẹn tới chỉ {gap} ngày (≤ {cfg['min_gap_days']})"
    else:
        skip = blocked(window, once=not cfg.get("resend_routine", True))
    gap_txt = f"khoảng trống {gap} ngày" if gap is not None else "chưa có lịch hẹn tới"
    out.append(FormDecision(**base, form_code=window, kind="routine", send=skip is None,
                            reason=f"Tuần thai {ga_text(ga)} trong cửa sổ {window}; {gap_txt}", skip_reason=skip))
    return out


def plan_forms(states: list[PatientState], today: date, rules: Rules) -> list[FormDecision]:
    out: list[FormDecision] = []
    for st in states:
        out += plan_for_patient(st, today, rules)
    return out


def suggested_modules(risk_flags: list[str], rules: Rules | None = None) -> list[str]:
    """Đề xuất module / bộ câu hỏi từ risk_flags (AI đọc ghi chú) – điều dưỡng phải xác nhận.
    Ánh xạ nằm trong rules.yaml `personalization` (luật 2: không viết cứng)."""
    if rules is None:
        from core.config import load_rules

        rules = load_rules()
    cfg = rules.get("personalization") or {}
    out: set[str] = set()
    for f in risk_flags:
        flag = cfg.get("note_flags", {}).get(f)
        out.update(cfg.get("flag_targets", {}).get(flag, []) if flag else [])
    return sorted(out)
