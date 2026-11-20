"""Luồng xử lý file lịch hẹn: kiểm tra → gộp → che → AI → giờ đến → review (hàm thuần)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core.ai.llm_client import LlmClient, can_send_external, local_only_client
from core.ai.masking import MaskResult, NerFn, mask_text
from core.ai.note_extractor import Extraction, extract
from core.config import HisColumns, Rules, load_visit_services
from core.ingest.his_reader import HisRow, standardize
from core.ingest.validators import ValidationReport, check_columns, check_kept_rows, validate_rows
from core.rules.arrival import ArrivalResult, compute_arrival
from core.rules.merge import MergedAppt, Slot, filter_departments, merge_appointments
from core.rules.note_patterns import find_ga, read_fully_by_rules
from core.rules.review import ReviewDecision, decide
from core.rules.sensitive import find_sensitive
from core.rules.visit_infer import (
    ServicePlan,
    infer_services,
    plan_minutes,
    source_titles,
    sourced_floor,
)
from core.security import is_valid_phone


@dataclass
class PlannedAppt:
    merged: MergedAppt
    arrival: ArrivalResult
    decision: ReviewDecision
    mask: MaskResult | None = None
    extraction: Extraction | None = None
    # Vì sao có / không gọi AI – hiện đúng một lý do cho người duyệt (xem AI_STATUS_LABELS ở API).
    ai_status: str = "no_note"
    # Loại khám + dịch vụ suy ra từ ghi chú (file OH không có cột Dịch vụ); rỗng khi file có cột Dịch vụ.
    service_plans: list[ServicePlan] = field(default_factory=list)
    basis: list[str] = field(default_factory=list)

    @property
    def service_names(self) -> list[str]:
        """Dịch vụ dự kiến (tính giờ) – KHÔNG gồm nhóm có điều kiện."""
        return list(dict.fromkeys(s.name for p in self.service_plans for s in p.timed))

    @property
    def maybe_names(self) -> list[str]:
        """Có thể cần (nếu chưa làm) – không tính giờ / nhịn ăn."""
        return list(dict.fromkeys(s.name for p in self.service_plans for s in p.maybe))


@dataclass
class ProcessResult:
    report: ValidationReport
    rows: list[HisRow] = field(default_factory=list)
    dropped: list[HisRow] = field(default_factory=list)
    planned: list[PlannedAppt] = field(default_factory=list)
    rules_version: str = ""

    @property
    def ok(self) -> bool:
        return self.report.ok

    @property
    def auto_rate(self) -> float:
        if not self.planned:
            return 0.0
        return sum(1 for p in self.planned if p.decision.status == "auto") / len(self.planned)


def plan_appointment(
    m: MergedAppt,
    rules: Rules,
    client: LlmClient | None,
    doctors: list[str],
    ner: NerFn | None | bool = True,
) -> PlannedAppt:
    mask: MaskResult | None = None
    extraction: Extraction | None = None
    sensitive: list[str] = []
    external = False
    ai_status = "no_note"
    if m.note:
        mask = mask_text(m.note, full_name=m.full_name, pid=m.pid, doctor_names=doctors, ner=ner)
        # File OH: AI đọc MỌI ghi chú để suy ra loại khám + dịch vụ (người dùng chốt 17/11/2026).
        #   Lịch có cột Dịch vụ (dữ liệu dạng cũ) → giữ như trước: ghi chú quy tắc đọc hết thì không gọi AI.
        read_all = bool(rules["arrival"].get("ai_read_all_notes", False)) and not m.services
        clear = (read_all and rules["arrival"].get("ai_skip_when_rules_clear", True)
                 and rules_clear(m.note, rules["arrival"].get("ai_skip_vocabulary", [])))
        skip_ai = clear or (not read_all
                   and read_fully_by_rules(m.note, rules["arrival"].get("ai_skip_vocabulary", [])))
        sensitive = find_sensitive("\n".join([m.note, *m.services]),
                                   (rules.get("privacy") or {}).get("sensitive_topics", []))
        # Ghi chú nhạy cảm không gửi ra API bên ngoài, kể cả khi đã che (chỉ mô hình cục bộ / mock).
        external = can_send_external(client)
        task_client = local_only_client(client) if sensitive else client
        if client is None:
            ai_status = "off"
        elif not mask.ok:
            ai_status = "mask_failed"
        elif skip_ai:
            ai_status = "rules_read"
        elif task_client is None:
            ai_status = "sensitive"
        else:
            # Dịch vụ HIS cũng là văn bản tự do và có thể chứa định danh.
            service_masks = [mask_text(service, full_name=m.full_name, pid=m.pid,
                                       doctor_names=doctors, ner=ner) for service in m.services]
            if not all(service.ok for service in service_masks):
                mask.ok = False
                mask.leftovers.append("SERVICE_PII")
                ai_status = "mask_failed"
            else:
                extraction = extract(mask.text, [service.text for service in service_masks],
                                     m.start_time, task_client, rules)
                ai_status = "ok" if extraction.ai is not None else "failed"
    ai = extraction.ai if extraction else None
    plans, slot_minutes, cap = plan_services(m, ai, rules)
    arrival = compute_arrival(m.start_time, m.services, m.note, ai, rules, slots=m.slots,
                              slot_minutes=slot_minutes or None, max_early=cap)
    catalog = load_visit_services(copy_data=False) if plans else {}
    basis: list[str] = []
    for sp in plans:
        for t in source_titles(sp, catalog):
            if t not in basis:
                basis.append(t)
        arrival.details.append(f"Loại khám: {sp.visit_name}. Dịch vụ: " + ", ".join(x.name for x in sp.timed))
        if sp.maybe:
            arrival.details.append("Có thể cần (nếu chưa làm – hỏi khách khi gọi): "
                                   + ", ".join(x.name for x in sp.maybe))
    if any(sp.fasting for sp in plans):
        arrival.details.append("Có dịch vụ cần nhịn ăn – nhớ dặn khách nhịn ăn trước khi đến")
    if m.note and sensitive:
        arrival.details.append(
            f"Ghi chú có nội dung nhạy cảm ({', '.join(sensitive)}) – không trao đổi với người nhà, "
            "không đưa vào tin nhắn" + ("; không gửi ra API bên ngoài" if external else "")
        )
    decision = decide(
        arrival=arrival,
        note=m.note,
        phone_ok=is_valid_phone(m.phone),
        conflict=m.conflict,
        ai=ai,
        ai_attempted=bool(extraction and extraction.attempted),
        ai_failed=bool(extraction and extraction.attempted and not extraction.votes and extraction.errors
                       and all("Giờ đến" not in e for e in extraction.errors)),
        # Chỉ coi là PII_LEFT khi định gửi AI mà che chưa sạch.
        mask_ok=not (client is not None and mask is not None and not mask.ok),
        rules=rules,
    )
    review_service_rules(m, plans, arrival, decision, rules)
    alt_raw = alt_arrival(arrival, plans)
    apply_opening_time(arrival, decision, rules, bool(plans), m.start_time)
    arrival.alt_time = clamp_alt(alt_raw, arrival, rules, m.start_time)
    check_clinic_day(m, arrival, decision, rules, bool(plans))
    guessed = list(dict.fromkeys(t for sp in plans for t in sp.guessed))
    if guessed and not arrival.waiting:
        arrival.details.append("Ghi chú có viết tắt khoa chưa xác nhận nghĩa: " + ", ".join(guessed))
        if rules["arrival"].get("review_guessed_abbr", True):
            if "ABBR_GUESS" not in decision.reasons:
                decision.reasons.append("ABBR_GUESS")
            decision.status = "needs_review"
    unclear = scope_unclear(m.note, rules) if m.note else None
    if unclear:
        arrival.details.append(f"Ghi chú có '{unclear}' – kiểm tra lịch này có thuộc khoa Sản phụ khoa không")
        if "SCOPE_CHECK" not in decision.reasons:
            decision.reasons.append("SCOPE_CHECK")
        decision.status = "needs_review"
    if "NO_PID" in m.warnings:
        arrival.details.append(f"Khách tự đặt (Autobooking) chưa có mã bệnh nhân – đang dùng mã tạm {m.pid}; "
                               "tìm / tạo hồ sơ trên OH rồi xác nhận")
        if "NO_PID" not in decision.reasons:
            decision.reasons.append("NO_PID")
        decision.status = "needs_review"
    return PlannedAppt(merged=m, arrival=arrival, decision=decision, mask=mask, extraction=extraction,
                       ai_status=ai_status, service_plans=plans, basis=basis)


def plan_services(
    m: MergedAppt, ai: object | None, rules: Rules
) -> tuple[list[ServicePlan], dict[Slot, tuple[int | None, str]], int | None]:
    """Lịch không có cột Dịch vụ (file OH) → suy loại khám + dịch vụ từ ghi chú từng lịch.
    AI (đọc ghi chú đã gộp) chỉ gắn vào lịch sớm nhất: chỉ làm giờ đến sớm hơn, không muộn hơn."""
    targets = [x for x in m.slots if not x.service]
    if not targets:
        return [], {}, None
    catalog = load_visit_services(copy_data=False)
    default_max = int(rules["arrival"]["max_minutes_early"])
    first = min(targets, key=lambda x: x.start_time)
    ai_ga = (getattr(ai, "ga_weeks", None), getattr(ai, "ga_days", None))
    plans: list[ServicePlan] = []
    minutes: dict[Slot, tuple[int | None, str]] = {}
    cap: int | None = None
    for slot in targets:
        ga = find_ga(slot.note) or (find_ga(m.note) if len(targets) == 1 else None)
        if ga is None and slot is first and ai_ga[0] is not None:
            ga = (int(ai_ga[0]), ai_ga[1])
        plan = infer_services(
            slot.note, catalog, ga=ga,
            ai_visit_type=getattr(ai, "visit_type", None) if slot is first else None,
            ai_services=list(getattr(ai, "services", []) or []) if slot is first else None,
        )
        _mins, scap, _label = plan_minutes(plan, catalog, default_max)
        plan.floor_minutes, plan.floor_sources = sourced_floor(plan, catalog)
        plan.over_cap = plan.floor_minutes > scap  # CLAUDE.md luật 2: vượt giới hạn → Cần xác nhận
        cap = max(scap if cap is None else max(cap, scap), plan.floor_minutes)
        # Code tự tính (người dùng chốt 17/11/2026): tiếp đón + tổng thời gian tối thiểu có nguồn của dịch vụ làm trước.
        minutes[slot] = (plan.floor_minutes, f"{plan.visit_name}: " + " + ".join(
            f"{name} {mm}'" for name, mm in plan.floor_parts))
        plans.append(plan)
    return plans, minutes, cap


def _minutes(t: object) -> int:
    return t.hour * 60 + t.minute  # type: ignore[attr-defined]


def _flag(decision: ReviewDecision, code: str) -> None:
    if code not in decision.reasons:
        decision.reasons.append(code)
    decision.status = "needs_review"


def review_service_rules(
    m: MergedAppt, plans: list[ServicePlan], arrival: ArrivalResult, decision: ReviewDecision, rules: Rules
) -> None:
    """Các luật Cần xác nhận của file OH (người dùng chốt 17/11/2026)."""
    if not plans:
        return
    # Nghi chuyển dạ / dấu hiệu cấp cứu – kể cả lịch khám chờ
    if any(sp.urgent for sp in plans):
        advice = next((sp.urgent_advice for sp in plans if sp.urgent and sp.urgent_advice), "")
        arrival.details.append(advice or str(rules["arrival"].get("urgent_advice", "Nghi cấp cứu sản – gọi khách ngay")))
        _flag(decision, "URGENT")
    if any(sp.fasting_check for sp in plans):
        arrival.details.append("Ghi chú chỉ ghi chuẩn bị hồ sơ thủ thuật – hỏi lại hôm nay có làm (nhịn ăn) không")
        _flag(decision, "FASTING_CHECK")
    if arrival.waiting:
        return
    first = plans[0]
    catalog = load_visit_services(copy_data=False)
    doctor = arrival.doctor_given
    # Giờ bác sĩ ghi ít hơn thời gian tối thiểu ≥ ngưỡng → giữ giờ bác sĩ, cần xác nhận; ít hơn → theo giờ bác sĩ.
    if doctor and arrival.arrival_time is not None:
        given = _minutes(m.start_time) - _minutes(arrival.arrival_time)
        short = first.floor_minutes - given
        if short >= int(rules["arrival"].get("doctor_time_short_review", 15)):
            arrival.details.append(f"Bác sĩ ghi đến trước {given} phút, ít hơn thời gian tối thiểu "
                                   f"{first.floor_minutes} phút ({' + '.join(f'{n} {x}' for n, x in first.floor_parts)})")
            _flag(decision, "DOCTOR_SHORT")
    if any(sp.over_cap for sp in plans) and not doctor:
        arrival.details.append(f"Tổng thời gian tối thiểu {first.floor_minutes} phút vượt giới hạn an toàn của các "
                               "dịch vụ – kiểm lại có làm hết trong một buổi không")
        _flag(decision, "OVER_CAP")
    # Xét nghiệm máu / nước tiểu: chưa rõ khách có phải chờ kết quả trước khi gặp bác sĩ (người dùng 18/11/2026)
    review_codes = set((catalog.get("visit_flow") or {}).get("review_services", []))
    labs = [x.name for x in first.timed if x.code in review_codes]
    if labs and not doctor:
        arrival.details.append("Có " + ", ".join(labs).lower() + " – xác nhận khách có phải chờ kết quả trước khi "
                               "gặp bác sĩ không (nếu có thì cho đến sớm hơn)")
        _flag(decision, "LAB_CONFIRM")
    # Mốc tuần thai đánh dấu "cần xác nhận" (vd test đường huyết – nhịn ăn) mà được tính giờ
    if any(x.confirm for x in first.timed):
        _flag(decision, "SERVICE_CONFIRM")
    # Dịch vụ tính giờ mà chỉ suy ra (ghi chú không ghi) và làm giờ đến sớm hơn → cần xác nhận
    inferred = [x for x in first.inferred_timed if catalog["services"][x.code].get("min_minutes")]
    if inferred and not doctor:
        arrival.details.append("Dịch vụ suy ra (ghi chú không ghi): " + ", ".join(x.name for x in inferred))
        if rules["arrival"].get("review_inferred_services", False):
            _flag(decision, "INFERRED_SERVICES")
    # Khám thai mà không có tuần thai
    if first.visit_type in tuple((catalog.get("visit_flow") or {}).get("anc_visit_types", [])) \
            and first.ga_weeks is None:
        arrival.details.append("Ghi chú khám thai không có tuần thai – hỏi lại tuần thai khi gọi")
        if rules["arrival"].get("review_missing_ga", False):
            _flag(decision, "NO_GA")


_CODE_RE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")


def plain_reason(text: str) -> str:
    """Bỏ mã nguồn (VMC_..., TA_...) khỏi câu giải thích của AI – màn hình chỉ hiện câu tiếng Việt."""
    t = _CODE_RE.sub("", text or "")
    t = re.sub(r"[\[(][\s,;]*[\])]", "", t)  # ngoặc rỗng sau khi bỏ mã
    t = re.sub(r"\s+([,;.)\]])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ;,-–")


def alt_arrival(arrival: ArrivalResult, plans: list[ServicePlan]) -> object:
    """Bản 2 giờ đến: ca tính theo dịch vụ (không phải giờ bác sĩ, không khám chờ) mà có dịch vụ tính giờ → bỏ thời
    gian tiếp đón (giờ đến muộn hơn bản 1 đúng bằng thời gian tiếp đón). Ca chỉ khám → giữ như bản 1."""
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timedelta

    if not plans or arrival.arrival_time is None or arrival.doctor_given or arrival.waiting:
        return arrival.arrival_time
    parts = plans[0].floor_parts
    if not any(mm > 0 for _name, mm in parts[1:]):
        return arrival.arrival_time
    reception = parts[0][1] if parts else 0
    return (_dt.combine(_date(2000, 1, 1), arrival.arrival_time) + timedelta(minutes=reception)).time()


def clamp_alt(alt: object, arrival: ArrivalResult, rules: Rules, appt: object) -> object:
    """Bản 2 cũng không sớm hơn giờ nhận khách và không muộn hơn giờ hẹn."""
    from core.config import parse_hhmm

    if alt is None:
        return arrival.arrival_time
    if arrival.doctor_given or arrival.waiting:
        return arrival.arrival_time
    earliest = rules["arrival"].get("clinic_earliest_arrival")
    if earliest and alt < parse_hhmm(str(earliest)):  # type: ignore[operator]
        alt = parse_hhmm(str(earliest))
    if appt is not None and alt > appt:  # type: ignore[operator]
        alt = appt
    return alt


def check_clinic_day(m: MergedAppt, arrival: ArrivalResult, decision: ReviewDecision, rules: Rules, oh: bool) -> None:
    """File OH: lịch vào lúc phòng khám nghỉ (arrival.clinic_hours – Vinmec Times City) → Cần xác nhận."""
    from core.config import parse_hhmm

    hours = rules["arrival"].get("clinic_hours") or {}
    if not oh or not hours:
        return
    wd = m.appt_date.weekday()  # 5 = thứ 7, 6 = chủ nhật
    closed = (wd == 6 and str(hours.get("sunday", "")) == "closed") or (
        wd == 5 and bool(hours.get("saturday_close")) and m.start_time >= parse_hhmm(str(hours["saturday_close"])))
    if closed:
        arrival.details.append("Lịch vào lúc phòng khám nghỉ (chiều thứ 7 / chủ nhật) – kiểm tra lại lịch")
        if "CLINIC_CLOSED" not in decision.reasons:
            decision.reasons.append("CLINIC_CLOSED")
        decision.status = "needs_review"


def apply_opening_time(arrival: ArrivalResult, decision: ReviewDecision, rules: Rules, oh: bool,
                       appt: object = None) -> None:
    """File OH: giờ đến không sớm hơn giờ phòng khám nhận khách (arrival.clinic_earliest_arrival – Vinmec Times City)."""
    from core.config import parse_hhmm

    earliest = rules["arrival"].get("clinic_earliest_arrival")
    if not oh or not earliest or arrival.arrival_time is None or arrival.waiting:
        return
    limit = parse_hhmm(str(earliest))
    if arrival.arrival_time >= limit:
        return
    doctor = arrival.doctor_given
    if appt is not None and limit > appt:
        limit = appt  # lịch hẹn trước giờ nhận khách → không đẩy giờ đến muộn hơn giờ hẹn
    gap = (limit.hour * 60 + limit.minute) - (arrival.arrival_time.hour * 60 + arrival.arrival_time.minute)
    if not doctor and gap <= int(rules["arrival"].get("clinic_open_review_gap", 0)):
        arrival.details.append(f"Giờ đến tính ra {arrival.arrival_time:%H:%M} → đổi thành {limit:%H:%M} "
                               "(giờ phòng khám bắt đầu nhận khách)")
        arrival.arrival_time = limit
        return
    if doctor:
        arrival.details.append(f"Bác sĩ dặn đến {arrival.arrival_time:%H:%M}, sớm hơn giờ phòng khám nhận khách "
                               f"({limit:%H:%M}) – kiểm lại với khách")
    else:
        arrival.details.append(f"Giờ đến tính ra {arrival.arrival_time:%H:%M} sớm hơn giờ phòng khám nhận khách – "
                               f"đã đổi thành {limit:%H:%M}; cân nhắc đổi giờ hẹn")
        arrival.arrival_time = limit
    if "OPEN_TIME" not in decision.reasons:
        decision.reasons.append("OPEN_TIME")
    decision.status = "needs_review"


def rules_clear(note: str, vocabulary: list[str] | None = None) -> bool:
    """Quy tắc đã đọc rõ ghi chú → AI không làm đổi kết quả, không gọi AI (người dùng 17/11/2026: tối ưu tốc độ):
    - loại khám cụ thể (không phải khám thai / phụ khoa chung chung – AI chỉ được đổi các loại chung), hoặc
    - ghi chú ghi dịch vụ, hoặc có giờ bác sĩ dặn (AI không được thêm / đổi gì), hoặc
    - ghi chú chỉ gồm từ quen + tuần thai / giờ (arrival.ai_skip_vocabulary)."""
    from core.rules.note_patterns import find_explicit_time, find_minutes_early, find_service_time
    from core.rules.visit_infer import _has, _plain, keyword_services, rule_visit_type

    cat = load_visit_services(copy_data=False)
    if any(_has(_plain(note), k) for k in (cat.get("visit_flow") or {}).get("ai_required_keywords", [])):
        return False
    vt = rule_visit_type(note, cat)
    generic = (cat.get("visit_flow") or {}).get("generic_visit_types", ["ANC", "GYN", "OTHER"])
    if vt is not None and vt not in generic:
        return True
    explicit = any(c != "KHAM" for c in keyword_services(note, cat))
    doctor_time = bool(find_explicit_time(note) or find_service_time(note) or find_minutes_early(note))
    if (vt is not None or find_ga(note)) and (explicit or doctor_time):
        return True
    return bool(vocabulary) and read_fully_by_rules(note, vocabulary)


def plan_all(
    merged: list[MergedAppt], rules: Rules, client: LlmClient | None, doctors: list[str], ner: NerFn | None | bool
) -> list[PlannedAppt]:
    """Xử lý các lịch; khi gọi được OpenAI thì chạy song song nhiều lịch (chờ mạng), kết quả và thứ tự như chạy lần
    lượt. Ollama / mock / không AI → lần lượt (forms.ai_workers, biến AI_PARALLEL_CALLS)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from core.ai.masking import default_ner
    from core.forms import ai_workers

    workers = ai_workers(client, len(merged)) if client is not None else 1
    if workers <= 1:
        return [plan_appointment(m, rules, client, doctors, ner) for m in merged]
    task_ner: NerFn | None | bool = ner
    if ner is True and (fn := default_ner()) is not None:
        lock = threading.Lock()  # mô hình NER không chắc an toàn đa luồng → mỗi lúc một ghi chú

        def task_ner(text: str) -> object:  # type: ignore[no-redef]
            with lock:
                return fn(text)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="plan-appt") as pool:
        return list(pool.map(lambda m: plan_appointment(m, rules, client, doctors, task_ner), merged))


def out_of_scope(note: str, rules: Rules) -> str | None:
    """Từ khóa dịch vụ khoa khác có trong ghi chú (vd IVF) – None nếu thuộc thai kỳ / phụ khoa."""
    from core.rules.visit_infer import _has, _plain

    cfg = rules.get("scope") or {}
    plain = _plain(note)
    hit = next((k for k in cfg.get("exclude_keywords", []) if _has(plain, k)), None)
    if hit is None:
        return None
    keep = [*cfg.get("keep_if_pregnancy_keywords", []), *cfg.get("keep_if_gyn_keywords", []),
            *cfg.get("keep_if_urgent_keywords", [])]
    if find_ga(note) or any(_has(plain, k) for k in keep):
        return None  # vd "thai IVF 20w", "IVF, ra máu" – vẫn thuộc khoa
    return hit


def scope_unclear(note: str, rules: Rules) -> str | None:
    """Từ khóa hỗ trợ sinh sản chung chung (IVF, hiếm muộn…) mà không rõ là thủ thuật → giữ lịch + Cần xác nhận."""
    from core.rules.visit_infer import _has, _plain

    plain = _plain(note)
    return next((k for k in (rules.get("scope") or {}).get("check_keywords", []) if _has(plain, k)), None)


def filter_scope(rows: list[HisRow], rules: Rules) -> tuple[list[HisRow], list[HisRow]]:
    keep: list[HisRow] = []
    drop: list[HisRow] = []
    for r in rows:
        hit = out_of_scope(r.note, rules) if r.note else None
        if hit:
            r.warnings.append(f"OUT_OF_SCOPE:{hit}")
            drop.append(r)
        else:
            keep.append(r)
    return keep, drop


from core.rules.export_worldsms import TEMP_PID_PREFIX  # noqa: E402  mã tạm dùng chung với file SMS


def assign_temp_pids(rows: list[HisRow]) -> None:
    """Khách tự đặt lịch (Autobooking) thường chưa có mã bệnh nhân (HN) trên OH – người dùng xác nhận 17/11/2026.
    Gán mã tạm RIÊNG từng dòng (không gộp nhầm hai khách) và đánh dấu NO_PID để vào Cần xác nhận.
    Dòng thiếu HN mà KHÔNG phải khách tự đặt vẫn là lỗi dữ liệu (E07 – dừng nhập)."""
    from core.textutil import deaccent

    def person(r: HisRow) -> tuple[str, str]:
        return " ".join(deaccent(r.full_name or "").lower().split()), str(r.phone or "")

    # Dòng thiếu HN nhưng cùng tên (bỏ dấu, không phân biệt hoa thường) + cùng SĐT với dòng có HN → dùng HN đó
    # (rà soát 18/11/2026: khách tự đặt thêm lịch bị tách 2 dòng, nhận 2 tin nhắn khác giờ).
    known = {person(r): r.pid for r in rows if str(r.pid or "").strip() and r.full_name and r.phone}
    temp: dict[tuple[str, str], str] = {}
    for r in rows:
        if str(r.pid or "").strip() or r.appt_date is None:
            continue
        key = person(r)
        if key[0] and key[1] and key in known:
            r.pid = known[key]
            continue
        if r.booking_source == "Autobooking":
            # cùng tên + SĐT → cùng một mã tạm (để gộp lịch)
            r.pid = temp.setdefault(key, f"{TEMP_PID_PREFIX}-{r.appt_date:%y%m%d}-{r.row_no:03d}") \
                if key[0] and key[1] else f"{TEMP_PID_PREFIX}-{r.appt_date:%y%m%d}-{r.row_no:03d}"
            if "NO_PID" not in r.warnings:
                r.warnings.append("NO_PID")


def flag_shared_phones(planned: list[PlannedAppt]) -> None:
    """Hai khách KHÁC nhau dùng chung một số điện thoại → cả hai cần xác nhận (một số nhận 2 tin cho 2 người)."""
    from core.rules.export_worldsms import format_phone

    by_phone: dict[str, list[PlannedAppt]] = {}
    for p in planned:
        k = format_phone(p.merged.phone or "", "local") if p.merged.phone else ""
        if k:
            by_phone.setdefault(k, []).append(p)
    for group in by_phone.values():
        if len({p.merged.pid for p in group}) > 1:
            for p in group:
                _flag(p.decision, "SHARED_PHONE")
                p.arrival.details.append("Số điện thoại này dùng chung với khách khác trong ngày – xác nhận người nhận tin")


def process_his(
    raw: pd.DataFrame,
    his_cfg: HisColumns,
    rules: Rules,
    selected_date: date | None,
    client: LlmClient | None = None,
    ner: NerFn | None | bool = True,
) -> ProcessResult:
    """Xử lý file HIS. client=None → chỉ dùng quy tắc (coi như AI trả null)."""
    report = check_columns(raw, his_cfg)
    if not report.ok:
        return ProcessResult(report=report, rules_version=rules.rules_version)
    rows = standardize(raw, his_cfg)
    row_report = validate_rows(rows, rules, selected_date)
    report.fatal += row_report.fatal
    report.row_issues += row_report.row_issues
    if not report.ok:
        return ProcessResult(report=report, rows=rows, rules_version=rules.rules_version)

    kept, dropped = filter_departments(rows, rules["departments_included"])
    kept, out_scope = filter_scope(kept, rules)  # dịch vụ khoa khác (IVF…) → bỏ khỏi lịch, trước khi gộp
    dropped += out_scope
    assign_temp_pids(kept)  # khách tự đặt (Autobooking) chưa có HN → mã tạm + Cần xác nhận
    report.fatal += check_kept_rows(kept)  # E08 không dòng nào của khoa / E07 thiếu PID → dừng, giữ lịch đã có
    if not report.ok:
        return ProcessResult(report=report, rows=rows, dropped=dropped, rules_version=rules.rules_version)
    merged = merge_appointments(
        kept, rules["arrival"]["merge_conflict_minutes"], rules["arrival"].get("fasting_keywords", [])
    )
    doctors = sorted({r.doctor for r in rows if r.doctor})
    planned = plan_all(merged, rules, client, doctors, ner)
    flag_shared_phones(planned)
    return ProcessResult(
        report=report, rows=rows, dropped=dropped, planned=planned, rules_version=rules.rules_version
    )
