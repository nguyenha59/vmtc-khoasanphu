"""AI đọc ghi chú lịch hẹn (docs/05 mục 2). Chỉ nhận văn bản ĐÃ CHE."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import time
from functools import lru_cache
from typing import Any

import yaml

from core.ai.consistency import agreement, majority, run_votes
from core.ai.llm_client import LlmClient, render_prompt
from core.config import ROOT, Rules, load_visit_services, prompt_text
from core.rules.arrival import NoteAI
from core.rules.note_patterns import find_explicit_time, find_ga, time_in_note
from core.rules.visit_infer import keyword_services, rule_visit_type
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
SERVICE_FIELDS = ["visit_type", "services"]
ESTIMATE_FIELDS = ["arrival_minutes", "minutes_basis"]


def note_schema(catalog: dict[str, Any]) -> dict[str, Any]:
    """Lược đồ + loại khám / dịch vụ (enum lấy từ config/visit_services.yaml – AI chỉ chọn trong danh mục)."""
    schema = json.loads(json.dumps(NOTE_SCHEMA))
    schema["properties"]["visit_type"] = {"type": ["string", "null"], "enum": [*catalog["visit_types"], None]}
    schema["properties"]["services"] = {"type": "array", "items": {"enum": list(catalog["services"])}}
    # AI tự ước lượng số phút đến trước (người dùng chốt 17/11/2026), phải nêu mã nguồn trong danh mục.
    schema["properties"]["arrival_minutes"] = {"type": ["integer", "null"], "minimum": 0, "maximum": 300}
    verified = [k for k, v in catalog.get("sources", {}).items() if v.get("verified")]
    schema["properties"]["minutes_basis"] = {"type": "array", "items": {"enum": verified}}
    schema["properties"]["minutes_reason"] = {"type": "string", "maxLength": 300}
    return schema


def knowledge_block(catalog: dict[str, Any]) -> str:
    """Danh mục rút gọn đưa vào prompt: loại khám, dịch vụ, mốc tuần thai (kèm nguồn)."""
    srcs = catalog.get("sources", {})
    lines = ["Loại khám (visit_type):"]
    for code, vt in catalog["visit_types"].items():
        kws = ", ".join(vt.get("keywords", [])[:8])
        lines.append(f"- {code}: {vt['name']}" + (f" (gặp: {kws})" if kws else "") + f" → thường làm {', '.join(vt['services'])}")
    lines.append("Dịch vụ (services):")
    for code, svc in catalog["services"].items():
        kws = ", ".join(svc.get("keywords", [])[:6])
        lines.append(f"- {code}: {svc['name']}" + (f" (viết: {kws})" if kws else ""))
    lines.append("Mốc tuần thai (chỉ khám thai định kỳ):")
    for m in catalog.get("ga_milestones", []):
        src = (srcs.get(m["source"]) or {}).get("title", m["source"])
        lines.append(f"- {m['from_week']}–{m['to_week']} tuần: {', '.join(m['services'])} (nguồn: {src})")
    lines.append("Thông tin từ nguồn (câu trích nguyên văn đã kiểm; mã nguồn trong ngoặc vuông):")
    for f in catalog.get("facts", []):
        if not (srcs.get(f["source"]) or {}).get("verified"):
            continue  # chỉ câu trích đã kiểm từ trang gốc
        lines.append(f"- [{f['source']}] ({', '.join(f['services'])}) \"{f.get('quote') or f.get('text')}\"")
    return "\n".join(lines)


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
    template = template.replace("{knowledge}", knowledge_block(load_visit_services()))
    payload = {"note": masked_note, "services": services, "appt_time": appt_time.strftime("%H:%M")}
    return render_prompt(template, payload), version


def _to_time(v: str | None) -> time | None:
    if not v:
        return None
    hh, mm = v.split(":")
    return time(int(hh), int(mm))


def extract(
    masked_note: str, services: list[str], appt_time: time, client: LlmClient, rules: Rules,
    estimate: bool = False,
) -> Extraction:
    """estimate=True: AI ước lượng số phút đến trước (file OH) → luôn chạy đủ số lần để kiểm tra đồng thuận."""
    """Gọi LLM (self-consistency) và tổng hợp. Ghi chú rỗng → không gọi."""
    if not masked_note.strip():
        return Extraction(ai=None, attempted=False, confidence=1.0, model=client.name)
    llm = rules["llm"]
    prompt, version = build_prompt(masked_note, services, appt_time)
    if llm.get("economy_mode") and not estimate and not TIME_HINT_RE.search(deaccent(masked_note)):
        runs, temp = 1, 0.0
    else:
        runs, temp = int(llm["consistency_runs_note"]), float(llm["temperature"])
    catalog = load_visit_services()
    votes = run_votes(client, prompt, note_schema(catalog), runs, temp)
    # Phiếu có explicit_arrival_time không xuất hiện trong ghi chú = bịa / chép giờ hẹn → loại, tính như lỗi.
    grounded = []
    grounded_models = []
    for o, model in zip(votes.outputs, votes.models, strict=True):
        t = o.get("explicit_arrival_time")
        if t and not time_in_note(t, masked_note):
            votes.errors.append(f"Giờ đến {t} không có trong ghi chú – loại phiếu")
        else:
            grounded.append(o)
            grounded_models.append(model)
    votes.outputs = grounded
    votes.models = grounded_models
    confidence_by_field = {
        name: agreement(votes.outputs, fields, votes.n_total)
        for name, fields in {
            "arrival": ["explicit_arrival_time", "minutes_early"],
            "ga": ["ga_weeks", "ga_days"],
            "risk": ["risk_flags"], "fasting": ["needs_fasting"],
            "confirmation": ["asks_confirmation"],
        }.items()
    }
    # Loại khám / dịch vụ: tin cậy riêng – thấp thì bỏ phần này (quy tắc + danh mục vẫn chạy), không hạ cả kết quả.
    services_conf = agreement(votes.outputs, SERVICE_FIELDS, votes.n_total)
    est_conf, est_spread = _estimate_agreement(votes.outputs, votes.n_total)
    conf = min(confidence_by_field.values())
    raw = {"runs": runs, "temperature": temp, "outputs": votes.outputs, "errors": votes.errors, "models": votes.models,
           "min_confidence": float(rules["arrival"]["ai_confidence_min"])}
    if not votes.outputs:
        return Extraction(ai=None, attempted=True, confidence=0.0, errors=votes.errors, model=client.name,
                          prompt_version=version, raw=raw)
    m = majority(votes.outputs, [*FIELDS, *SERVICE_FIELDS, "arrival_minutes"])
    # Không tạo một cặp tuần/ngày bằng hai phiếu đa số độc lập.
    ga_pair = Counter((o.get("ga_weeks"), o.get("ga_days")) for o in votes.outputs).most_common(1)[0][0]
    m["ga_weeks"], m["ga_days"] = ga_pair
    noted_ga = find_ga(masked_note)
    ga_grounded = bool(noted_ga and ga_pair[0] == noted_ga[0]
                       and (ga_pair[1] or 0) == noted_ga[1])
    raw["confidence_by_field"] = {**confidence_by_field, "services": services_conf, "arrival_minutes": est_conf}
    raw["arrival_minutes_spread"] = est_spread
    use_services = services_conf >= float(rules["arrival"]["ai_confidence_min"])
    raw["ga_grounded"] = ga_grounded
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
        ga_confidence=confidence_by_field["ga"], ga_grounded=ga_grounded,
        visit_type=(m.get("visit_type") if use_services else None),
        services=[c for c in (m.get("services") or []) if c in catalog["services"]] if use_services else [],
        services_confidence=services_conf,
        est_minutes=m.get("arrival_minutes"),
        est_basis=_basis_for(votes.outputs, m.get("arrival_minutes")),
        est_reason=next((str(o.get("minutes_reason") or "") for o in votes.outputs
                         if o.get("arrival_minutes") == m.get("arrival_minutes")), ""),
        est_confidence=est_conf, est_spread=est_spread,
        est_max=max((int(o["arrival_minutes"]) for o in votes.outputs
                     if o.get("arrival_minutes") is not None and o.get("minutes_basis")), default=None),
    )
    return Extraction(ai=ai, attempted=True, confidence=conf, votes=votes.outputs, errors=votes.errors,
                      model=client.name, prompt_version=version, raw=raw)


def _estimate_agreement(outputs: list[dict[str, Any]], n_total: int) -> tuple[float, int | None]:
    """Đồng thuận số phút AI ước lượng: tỷ lệ phiếu có số phút + nêu nguồn trùng đa số; độ lệch max−min."""
    vals = [o.get("arrival_minutes") for o in outputs if o.get("arrival_minutes") is not None and o.get("minutes_basis")]
    if not vals or n_total == 0:
        return 0.0, None
    top = Counter(vals).most_common(1)[0][1]
    return top / n_total, max(vals) - min(vals)


def _basis_for(outputs: list[dict[str, Any]], minutes: int | None) -> list[str]:
    """Hợp mã nguồn của các phiếu chọn đúng số phút đa số."""
    out: list[str] = []
    for o in outputs:
        if minutes is not None and o.get("arrival_minutes") == minutes:
            out.extend(b for b in (o.get("minutes_basis") or []) if b not in out)
    return out


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
    catalog = load_visit_services()
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
        # Mock: loại khám + dịch vụ bằng từ khóa danh mục (không phải AI thật).
        "visit_type": rule_visit_type(note, catalog) or ("ANC" if ga else "OTHER"),
        "services": keyword_services(note, catalog),
        **_mock_estimate(note, ga, catalog),
    }


def _mock_estimate(note: str, ga: tuple[int, int | None] | None, catalog: dict[str, Any]) -> dict[str, Any]:
    """Mock: số phút dự phòng của danh mục + nguồn của dịch vụ (không phải AI thật)."""
    from core.rules.visit_infer import infer_services, plan_minutes, sourced_floor

    plan = infer_services(note, catalog, ga=ga)
    minutes, _cap, _label = plan_minutes(plan, catalog, 90)
    minutes = max(minutes or 0, sourced_floor(plan, catalog)[0])
    key = set(plan.codes) - {"KHAM"}
    basis = ["VMC_DEN_TRUOC"] + [f["source"] for f in catalog.get("facts", []) if set(f["services"]) & key]
    return {"arrival_minutes": minutes, "minutes_basis": list(dict.fromkeys(basis))[:4],
            "minutes_reason": "tiếp đón + thời gian tối thiểu của các dịch vụ làm trước"}
