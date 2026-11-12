"""Tóm tắt cho bác sĩ (docs/05 mục 5). Bước 1 theo mẫu; bước 2 (tùy chọn) LLM viết lại có kiểm tra số."""

from __future__ import annotations

import json
import re
from typing import Any

from core.ai.llm_client import LlmClient, LlmError, render_prompt
from core.config import Rules, prompt_text

RISK_VI = {"M-THA": "Tăng huyết áp", "M-DTD": "Đái tháo đường thai kỳ", "HTN": "Tăng huyết áp",
           "GDM": "Đái tháo đường thai kỳ", "PREECLAMPSIA": "Tiền sản giật", "TWINS": "Song thai",
           "PRETERM_RISK": "Dọa sinh non", "PREV_CSECTION": "Vết mổ cũ"}
LEVEL_VI = {"red": "ĐỎ", "yellow": "VÀNG", "green": "XANH"}
TEMP_VI = {"current": "hiện tại", "past_resolved": "đã qua", "unknown": "không rõ"}


def template_summary(data: dict[str, Any]) -> str:
    """data: ga, risk_groups, vitals {kind: (value, trend_text)}, concepts [(name, temporality)], level, reasons."""
    ga = data.get("ga") or "chưa rõ"
    # Sau sinh, ga đã là "Hậu sản N ngày" → không thêm "Tuần thai" (trước đây ra "Tuần thai Hậu sản 52 ngày").
    parts = [f"{ga}." if str(ga).startswith("Hậu sản") else f"Tuần thai {ga}."]
    risks = [RISK_VI.get(r, r) for r in data.get("risk_groups", [])]
    parts.append("Nhóm nguy cơ: " + (", ".join(risks) if risks else "không") + ".")
    vit = data.get("vitals", {})
    if "bp" in vit:
        v, trend = vit["bp"]
        parts.append(f"Huyết áp mới nhất {v}" + (f" ({trend})" if trend else "") + ".")
    if "glu" in vit:
        v, trend = vit["glu"]
        parts.append(f"Đường huyết {v} mmol/L" + (f" ({trend})" if trend else "") + ".")
    concepts = data.get("concepts", [])
    if concepts:
        parts.append("Khái niệm tìm thấy: " + ", ".join(f"{n} ({TEMP_VI.get(t, t)})" for n, t in concepts) + ".")
    parts.append(f"Mức {LEVEL_VI.get(data.get('level', ''), data.get('level'))}: "
                 + ("; ".join(data.get("reasons", [])) or "không có dấu hiệu bất thường") + ".")
    return " ".join(parts)


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_ok(text: str, source: dict[str, Any]) -> bool:
    """Mọi con số trong câu viết lại phải có trong dữ liệu đầu vào."""
    allowed = set(_NUM.findall(json.dumps(source, ensure_ascii=False)))
    return all(n in allowed for n in _NUM.findall(text))


def summarize(data: dict[str, Any], rules: Rules, client: LlmClient | None = None) -> tuple[str, str]:
    """Trả (tóm tắt, cách tạo: template | llm)."""
    base = template_summary(data)
    if not rules["llm"].get("summary_llm_rewrite") or client is None:
        return base, "template"
    schema = {"type": "object", "properties": {"text": {"type": "string", "maxLength": 600}}, "required": ["text"]}
    template, _version = prompt_text("summary")
    payload = {"data": data, "template_summary": base}
    try:
        out = client.generate_json(render_prompt(template, payload), schema, temperature=0.0, seed=1)
    except LlmError:
        return base, "template"
    text = out.get("text", "")
    if not text or not numbers_ok(text, payload):
        return base, "template"
    return text, "llm"
