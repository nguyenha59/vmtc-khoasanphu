"""Luồng xử lý file lịch hẹn: kiểm tra → gộp → che → AI → giờ đến → review (hàm thuần)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core.ai.llm_client import LlmClient
from core.ai.masking import MaskResult, NerFn, mask_text
from core.ai.note_extractor import Extraction, extract
from core.config import HisColumns, Rules
from core.ingest.his_reader import HisRow, standardize
from core.ingest.validators import ValidationReport, check_columns, validate_rows
from core.rules.arrival import ArrivalResult, compute_arrival
from core.rules.merge import MergedAppt, filter_departments, merge_appointments
from core.rules.review import ReviewDecision, decide
from core.security import is_valid_phone


@dataclass
class PlannedAppt:
    merged: MergedAppt
    arrival: ArrivalResult
    decision: ReviewDecision
    mask: MaskResult | None = None
    extraction: Extraction | None = None


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
    if m.note:
        mask = mask_text(m.note, full_name=m.full_name, pid=m.pid, doctor_names=doctors, ner=ner)
        if client is not None and mask.ok:
            extraction = extract(mask.text, m.services, m.start_time, client, rules)
    ai = extraction.ai if extraction else None
    arrival = compute_arrival(m.start_time, m.services, m.note, ai, rules)
    decision = decide(
        arrival=arrival,
        note=m.note,
        phone_ok=is_valid_phone(m.phone),
        conflict=m.conflict,
        ai=ai,
        ai_attempted=bool(extraction and extraction.attempted),
        # Chỉ coi là PII_LEFT khi định gửi AI mà che chưa sạch.
        mask_ok=not (client is not None and mask is not None and not mask.ok),
        rules=rules,
    )
    return PlannedAppt(merged=m, arrival=arrival, decision=decision, mask=mask, extraction=extraction)


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
    merged = merge_appointments(
        kept, rules["arrival"]["merge_conflict_minutes"], rules["arrival"].get("fasting_keywords", [])
    )
    doctors = sorted({r.doctor for r in rows if r.doctor})
    planned = [plan_appointment(m, rules, client, doctors, ner) for m in merged]
    return ProcessResult(
        report=report, rows=rows, dropped=dropped, planned=planned, rules_version=rules.rules_version
    )
