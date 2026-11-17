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
        return list(dict.fromkeys(s.name for p in self.service_plans for s in p.services))


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
        skip_ai = (not read_all
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
                                     m.start_time, task_client, rules, estimate=read_all)
                ai_status = "ok" if extraction.ai is not None else "failed"
    ai = extraction.ai if extraction else None
    plans, slot_minutes, cap = plan_services(m, ai, rules)
    arrival = compute_arrival(m.start_time, m.services, m.note, ai, rules, slots=m.slots,
                              slot_minutes=slot_minutes or None, max_early=cap)
    catalog = load_visit_services() if plans else {}
    basis: list[str] = []
    for sp in plans:
        for t in source_titles(sp, catalog):
            if t not in basis:
                basis.append(t)
        arrival.details.append(f"Loại khám: {sp.visit_name}. Dịch vụ: " + ", ".join(x.name for x in sp.services))
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
    problem = (ai_estimate_problem(ai, rules, cap, plans[0].floor_minutes)
               if plans and not arrival.waiting else None)
    if problem:
        arrival.details.append(f"{problem} – tạm dùng số phút mặc định, cần điều dưỡng kiểm lại")
        if "AI_MINUTES" not in decision.reasons:
            decision.reasons.append("AI_MINUTES")
        decision.status = "needs_review"
    for sp in plans:
        if sp.est_minutes is not None:
            srcs = load_visit_services().get("sources", {})
            reason = plain_reason(getattr(ai, "est_reason", "") if ai is not None else "")
            arrival.details.append(f"AI tính đến trước {sp.est_minutes} phút" + (f" ({reason})" if reason else "")
                                   + " – xem Căn cứ bên dưới")
            for b in sp.est_basis:
                t = str((srcs.get(b) or {}).get("title", b))
                if t not in basis:
                    basis.append(t)
    guessed = list(dict.fromkeys(t for sp in plans for t in sp.guessed))
    if guessed and not arrival.waiting:
        arrival.details.append("Ghi chú có viết tắt khoa chưa xác nhận nghĩa: " + ", ".join(guessed))
        if rules["arrival"].get("review_guessed_abbr", True):
            if "ABBR_GUESS" not in decision.reasons:
                decision.reasons.append("ABBR_GUESS")
            decision.status = "needs_review"
    if "NO_PID" in m.warnings:
        arrival.details.append(f"Khách tự đặt (Autobooking) chưa có mã bệnh nhân – đang dùng mã tạm {m.pid}; "
                               "tìm / tạo hồ sơ trên OH rồi xác nhận")
        if "NO_PID" not in decision.reasons:
            decision.reasons.append("NO_PID")
        decision.status = "needs_review"
    pending = [x for sp in plans for x in sp.to_confirm]
    if pending and not arrival.waiting:
        arrival.details.append("Cần xác nhận trước khi dặn khách: " + ", ".join(
            f"{x.name} (suy ra theo tuần thai, ghi chú không ghi)" for x in pending))
        if "SERVICE_CONFIRM" not in decision.reasons:
            decision.reasons.append("SERVICE_CONFIRM")
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
    catalog = load_visit_services()
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
        mins, scap, label = plan_minutes(plan, catalog, default_max)
        plan.floor_minutes, plan.floor_sources = sourced_floor(plan, catalog)
        cap = scap if cap is None else max(cap, scap)
        est = ai_estimate(ai, rules, scap, plan.floor_minutes) if slot is first else None
        if est is not None:
            # AI ước lượng theo nguồn là chính (người dùng chốt 17/11/2026); bảng chỉ dự phòng.
            plan.est_minutes, plan.est_basis = est
            minutes[slot] = (est[0], f"{plan.visit_name}, AI tính theo tài liệu tham khảo")
        else:
            if mins is not None and plan.floor_minutes > mins:
                # Số dự phòng thấp hơn mức sàn có nguồn → dùng mức sàn
                minutes[slot] = (plan.floor_minutes, f"{plan.visit_name}, thời gian tối thiểu theo tài liệu")
            else:
                minutes[slot] = (mins, f"{plan.visit_name}, số phút mặc định cho {label.lower()}")
        plans.append(plan)
    return plans, minutes, cap


def ai_estimate(ai: object | None, rules: Rules, cap: int, floor: int = 0) -> tuple[int, list[str]] | None:
    """Số phút AI ước lượng (có nêu nguồn). Các lần chạy lệch nhau → lấy số LỚN nhất (đến sớm hơn = an toàn);
    thấp hơn mức sàn có nguồn (tiếp đón + tối thiểu các dịch vụ làm trước) → nâng lên mức sàn.
    None khi AI không đưa ra số có nguồn hoặc vượt giới hạn an toàn của dịch vụ."""
    minutes = getattr(ai, "est_minutes", None)
    basis = list(getattr(ai, "est_basis", []) or [])
    if minutes is None or not basis:
        return None
    spread = getattr(ai, "est_spread", None) or 0
    if spread > int(rules["arrival"]["ai_rule_diff_minutes"]) and getattr(ai, "est_max", None) is not None:
        minutes = int(ai.est_max)  # type: ignore[attr-defined]
    minutes = max(int(minutes), floor)
    if minutes > cap:
        return None
    return minutes, basis


def ai_estimate_problem(ai: object | None, rules: Rules, cap: int | None, floor: int = 0) -> str | None:
    """Chỉ đưa vào Cần xác nhận khi AI không cho được số phút có nguồn, hoặc số vượt giới hạn an toàn."""
    if ai is None or cap is None:
        return None
    minutes = getattr(ai, "est_minutes", None)
    if minutes is None or not getattr(ai, "est_basis", None):
        return "AI không đưa ra số phút đến trước có nguồn"
    if max(int(minutes), floor) > cap:
        return f"AI ước lượng {minutes} phút, vượt giới hạn an toàn {cap} phút"
    return None


_CODE_RE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")


def plain_reason(text: str) -> str:
    """Bỏ mã nguồn (VMC_..., TA_...) khỏi câu giải thích của AI – màn hình chỉ hiện câu tiếng Việt."""
    t = _CODE_RE.sub("", text or "")
    t = re.sub(r"[\[(][\s,;]*[\])]", "", t)  # ngoặc rỗng sau khi bỏ mã
    t = re.sub(r"\s+([,;.)\]])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ;,-–")


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


TEMP_PID_PREFIX = "TAM"


def assign_temp_pids(rows: list[HisRow]) -> None:
    """Khách tự đặt lịch (Autobooking) thường chưa có mã bệnh nhân (HN) trên OH – người dùng xác nhận 17/11/2026.
    Gán mã tạm RIÊNG từng dòng (không gộp nhầm hai khách) và đánh dấu NO_PID để vào Cần xác nhận.
    Dòng thiếu HN mà KHÔNG phải khách tự đặt vẫn là lỗi dữ liệu (E07 – dừng nhập)."""
    for r in rows:
        if not str(r.pid or "").strip() and r.booking_source == "Autobooking" and r.appt_date is not None:
            r.pid = f"{TEMP_PID_PREFIX}-{r.appt_date:%y%m%d}-{r.row_no:03d}"
            if "NO_PID" not in r.warnings:
                r.warnings.append("NO_PID")


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
    assign_temp_pids(kept)  # khách tự đặt (Autobooking) chưa có HN → mã tạm + Cần xác nhận
    report.fatal += check_kept_rows(kept)  # E08 không dòng nào của khoa / E07 thiếu PID → dừng, giữ lịch đã có
    if not report.ok:
        return ProcessResult(report=report, rows=rows, dropped=dropped, rules_version=rules.rules_version)
    merged = merge_appointments(
        kept, rules["arrival"]["merge_conflict_minutes"], rules["arrival"].get("fasting_keywords", [])
    )
    doctors = sorted({r.doctor for r in rows if r.doctor})
    planned = plan_all(merged, rules, client, doctors, ner)
    return ProcessResult(
        report=report, rows=rows, dropped=dropped, planned=planned, rules_version=rules.rules_version
    )
