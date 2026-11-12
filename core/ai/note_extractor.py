"""AI đọc ghi chú lịch hẹn (docs/05 mục 2). Chỉ nhận văn bản ĐÃ CHE."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from functools import lru_cache
from typing import Any

import yaml

from core.ai.consistency import agreement, majority, run_votes
from core.ai.llm_client import LlmClient, render_prompt
from core.config import ROOT, Rules, prompt_text
from core.rules.arrival import NoteAI
from core.rules.note_patterns import find_explicit_time, find_ga, time_in_note
from core.textutil import contains_keyword, deaccent

NOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "explicit_arrival_time": {"type": ["string", "null"], "pattern": "^([01]\\d|2[0-3]):[0-5]\\d$"},
        "minutes_early": {"type": ["integer", "null"], "minimum": 0, "maximum": 120},
        "needs_fasting": {"type": "boolean"},
        "ga_weeks": {"type": ["integer", "null"], "minimum": 4, "maximum": 42},
        "ga_days": {"type": ["integer", "null"], "minimum": 0, "maximum": 6},
        "risk_flags": {
            "type": "array",
            "items": {"enum": ["GDM", "HTN", "PREECLAMPSIA", "PRETERM_RISK", "PREV_CSECTION", "TWINS", "OTHER"]},
        },
        "asks_confirmation": {"type": "boolean"},
        "evidence": {"type": "string", "maxLength": 200},
    },
    "required": [
        "explicit_arrival_time", "minutes_early", "needs_fasting", "ga_weeks", "ga_days",
        "risk_flags", "asks_confirmation", "evidence",
    ],
}
FIELDS = list(NOTE_SCHEMA["required"])
TIME_HINT_RE = re.compile(r"\d+\s*(h|g|p|phut|gio|tieng|:)|truoc|som|gio|luc", re.IGNORECASE)


@dataclass
class Extraction:
    ai: NoteAI | None
    attempted: bool
    confidence: float
    votes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _fewshot_block() -> str:
    path = ROOT / "config" / "prompts" / "note_extractor_fewshot.yaml"  # cố định, đi theo prompt
    if not path.exists():
        return ""
    import json

    examples = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("examples", [])
    lines = []
    for ex in examples[:20]:
        out = ex["output"]
        # Ví dụ dùng đúng định dạng đầu vào thật; giờ hẹn luôn KHÁC giờ đến để mô hình không chép giờ hẹn.
        appt = _shot_appt_time(out.get("explicit_arrival_time"))
        inp = {"note": ex["note"], "services": ["Khám thai"], "appt_time": appt}
        lines.append(
            f"Đầu vào: {json.dumps(inp, ensure_ascii=False)}\nJSON: {json.dumps(out, ensure_ascii=False)}"
        )
    return "\n\n".join(lines)


def _shot_appt_time(explicit: str | None) -> str:
    if not explicit:
        return "09:00"
    hh, mm = (int(x) for x in explicit.split(":"))
    total = hh * 60 + mm + 30
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


def build_prompt(masked_note: str, services: list[str], appt_time: time) -> tuple[str, str]:
    template, version = prompt_text("note_extractor")
    template = template.replace("{fewshot}", _fewshot_block())
    payload = {"note": masked_note, "services": services, "appt_time": appt_time.strftime("%H:%M")}
    return render_prompt(template, payload), version


def _to_time(v: str | None) -> time | None:
    if not v:
        return None
    hh, mm = v.split(":")
    return time(int(hh), int(mm))


def extract(
    masked_note: str, services: list[str], appt_time: time, client: LlmClient, rules: Rules
) -> Extraction:
    """Gọi LLM (self-consistency) và tổng hợp. Ghi chú rỗng → không gọi."""
    if not masked_note.strip():
        return Extraction(ai=None, attempted=False, confidence=1.0, model=client.name)
    llm = rules["llm"]
    prompt, version = build_prompt(masked_note, services, appt_time)
    if llm.get("economy_mode") and not TIME_HINT_RE.search(deaccent(masked_note)):
        runs, temp = 1, 0.0
    else:
        runs, temp = int(llm["consistency_runs_note"]), float(llm["temperature"])
    votes = run_votes(client, prompt, NOTE_SCHEMA, runs, temp)
    # Phiếu có explicit_arrival_time không xuất hiện trong ghi chú = bịa / chép giờ hẹn → loại, tính như lỗi.
    grounded = []
    for o in votes.outputs:
        t = o.get("explicit_arrival_time")
        if t and not time_in_note(t, masked_note):
            votes.errors.append(f"Giờ đến {t} không có trong ghi chú – loại phiếu")
        else:
            grounded.append(o)
    votes.outputs = grounded
    conf = agreement(votes.outputs, ["explicit_arrival_time", "minutes_early"], votes.n_total)
    raw = {"runs": runs, "temperature": temp, "outputs": votes.outputs, "errors": votes.errors}
    if not votes.outputs:
        return Extraction(ai=None, attempted=True, confidence=0.0, errors=votes.errors, model=client.name,
                          prompt_version=version, raw=raw)
    m = majority(votes.outputs, FIELDS)
    ai = NoteAI(
        ok=True,
        confidence=conf,
        explicit_arrival_time=_to_time(m.get("explicit_arrival_time")),
        minutes_early=m.get("minutes_early"),
        needs_fasting=bool(m.get("needs_fasting")),
        ga_weeks=m.get("ga_weeks"),
        ga_days=m.get("ga_days"),
        risk_flags=list(m.get("risk_flags") or []),
        asks_confirmation=bool(m.get("asks_confirmation")),
        evidence=str(m.get("evidence") or ""),
    )
    return Extraction(ai=ai, attempted=True, confidence=conf, votes=votes.outputs, errors=votes.errors,
                      model=client.name, prompt_version=version, raw=raw)


def ga_inconsistent(ga_weeks: int | None, expected_weeks: float | None, tolerance: float = 2.0) -> bool:
    """Tuần thai AI đọc lệch > 2 tuần so với dự tính theo lần khám trước."""
    if ga_weeks is None or expected_weeks is None:
        return False
    return abs(ga_weeks - expected_weeks) > tolerance


# ---------------------------------------------------------------- heuristic (cho MockClient)
_EARLY_RE = re.compile(r"(?:truoc|som)\s*(\d{1,3})\s*(p|phut)\b")
_EARLY_HOUR_RE = re.compile(r"(?:truoc|som)\s*(\d)\s*tieng")
_RISK_WORDS = {
    "HTN": ["tha", "ha cao", "tang huyet ap"],
    "GDM": ["dtdtk", "dai thao duong", "tieu duong"],
    "PREECLAMPSIA": ["tsg", "tien san giat"],
    "PRETERM_RISK": ["doa sinh non", "co tu cung ngan"],
    "PREV_CSECTION": ["vmc", "mo lay thai"],
    "TWINS": ["song thai"],
}
_CALL_WORDS = ["gọi", "xác nhận", "hỏi lại", "chưa rõ", "đổi lịch", "dời lịch", "?"]


def heuristic_extract(note: str) -> dict[str, Any]:
    """Trích xuất bằng luật đơn giản – dùng khi mock không có đáp án sẵn."""
    plain = deaccent(note).lower()
    early: int | None = None
    if m := _EARLY_RE.search(plain):
        early = min(int(m.group(1)), 120)
    elif m := _EARLY_HOUR_RE.search(plain):
        early = min(int(m.group(1)) * 60, 120)
    explicit = find_explicit_time(note)
    ga = find_ga(note)
    risks = [k for k, words in _RISK_WORDS.items() if any(re.search(rf"\b{w}\b", plain) for w in words)]
    return {
        "explicit_arrival_time": explicit.strftime("%H:%M") if explicit else None,
        "minutes_early": early,
        "needs_fasting": bool(re.search(r"nhin an|npdn|lay mau", plain)),
        "ga_weeks": ga[0] if ga else None,
        "ga_days": ga[1] if ga else None,
        "risk_flags": risks,
        "asks_confirmation": any(contains_keyword(note, k) for k in _CALL_WORDS),
        "evidence": note[:200],
    }
