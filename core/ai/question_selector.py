"""AI chấm điểm câu hỏi cho từng thai phụ (lớp 3, docs/13 mục 5c).

- Đầu vào KHÔNG định danh (tuần thai, tên nhóm nguy cơ, mã + nội dung câu hỏi) – vẫn qua masking.py (luật 4);
  che không sạch → không gửi, dùng điểm quy tắc.
- Nhà cung cấp: rules.yaml `llm.question_selector_provider` (openai chỉ với dữ liệu giả – luật 5, check_provider).
- Kết quả chỉ được NÂNG điểm câu (core.rules.question_select lấy max). Mã câu lạ bị bỏ qua.
- Lưu: model, prompt_version, rules_version, confidence, JSON thô (quy ước dự án).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.ai.llm_client import (
    LlmClient,
    LlmError,
    make_client,
    make_client_for,
    real_model,
    result_provider,
)
from core.ai.masking import NerFn, mask_text
from core.config import FormsConfig, Rules, prompt_text
from core.rules.question_select import Context, base_questions, candidates

SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {"type": "array", "items": {
            "type": "object",
            "properties": {"code": {"type": "string"}, "score": {"type": "number", "minimum": 0, "maximum": 1},
                           "reason": {"type": "string", "maxLength": 160}},
            "required": ["code", "score"]}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["scores", "confidence"],
}


@dataclass
class AiScores:
    scores: dict[str, tuple[float, str]] = field(default_factory=dict)
    ok: bool = False
    model: str | None = None
    prompt_version: str | None = None
    confidence: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def selector_client(rules: Rules) -> LlmClient | None:
    """Client cho tác vụ chọn câu; None = chỉ chấm theo quy tắc (cấu hình 'rules' hoặc nhà cung cấp bị chặn)."""
    import os

    # Biến môi trường QUESTION_SELECTOR_PROVIDER ưu tiên (test đặt "mock" để không gọi API thật).
    provider = (os.environ.get("QUESTION_SELECTOR_PROVIDER") or str(rules.get("llm.question_selector_provider") or "inherit"))
    provider = provider.strip().lower()
    if provider == "rules":
        return None
    try:
        if provider == "inherit":
            return make_client(rules)
        return make_client_for(rules, provider)
    except (ValueError, LlmError):
        return None  # vd. openai khi chưa đặt SYNTHETIC_DATA_ONLY=1 hoặc APP_ENV=production → quy tắc


def build_payload(form_codes: list[str], ctx: Context, rules: Rules, forms_cfg: FormsConfig) -> dict[str, Any]:
    names = (rules.get("personalization") or {}).get("flag_names", {})
    concepts = rules.get("symptom_concepts") or {}
    base = base_questions(form_codes, ctx, rules, forms_cfg)
    extra = candidates(base, ctx, forms_cfg)
    eligible = set(base) | set(extra)
    q = forms_cfg.questions
    return {
        "ga_weeks": ctx.ga[0] if ctx.ga else None,
        "stage": "postpartum" if ctx.postpartum else "pregnancy",
        "conditions": [names.get(f, f) for f in ctx.flags],
        "previous_flagged": [{"text": q[c]["text"], "answer": a} for c, a in ctx.flagged_answers.items()
                             if c in q and (not ctx.postpartum or c in eligible)],
        "recent_concepts": [concepts.get(c, {}).get("name", c) for c in ctx.recent_concepts],
        **ctx.clinical,  # số đo đã phân loại, nhóm thuốc, tiền sử sản khoa, khoảng cách lần khám
        "mandatory": [{"code": c, "text": q[c]["text"]} for c in base],
        "candidates": [{"code": c, "text": q[c]["text"]} for c in extra],
    }



def ai_input_preview(form_codes: list[str], ctx: Context, rules: Rules, forms_cfg: FormsConfig) -> dict[str, Any]:
    """Đúng phần thông tin thai phụ sẽ gửi AI (đã qua masking), bỏ danh sách câu hỏi – để điều dưỡng / bác sĩ xem."""
    payload = build_payload(form_codes, ctx, rules, forms_cfg)
    masked = mask_text(json.dumps(payload, ensure_ascii=False), ner=False)
    data = json.loads(masked.text) if masked.ok else {}
    return {"mask_ok": masked.ok, "n_questions": len(payload["mandatory"]) + len(payload["candidates"]),
            "fields": {k: v for k, v in data.items() if k not in ("mandatory", "candidates")}}


def ai_scores(form_codes: list[str], ctx: Context, rules: Rules, forms_cfg: FormsConfig, client: LlmClient | None,
              *, ner: NerFn | None | bool = False) -> AiScores:
    """Gọi AI chấm điểm. Mọi lỗi → AiScores(ok=False), nơi gọi dùng điểm quy tắc."""
    if client is None:
        return AiScores(error="Không dùng AI (cấu hình rules hoặc nhà cung cấp bị chặn)")
    template, version = prompt_text("question_selector")
    payload = build_payload(form_codes, ctx, rules, forms_cfg)
    allowed = {x["code"] for x in [*payload["mandatory"], *payload["candidates"]]}
    # Luật 4: mọi văn bản gửi mô hình đi qua masking (ở đây không có tên / SĐT – NER tắt mặc định vì toàn là câu hỏi
    # của khoa; quét số / email / mã vẫn chạy). Còn sót → không gửi.
    masked = mask_text(json.dumps(payload, ensure_ascii=False), ner=ner)
    if not masked.ok:
        return AiScores(model=client.name, prompt_version=version, error="Che dữ liệu chưa sạch – không gửi AI")
    sel = rules.get("personalization.selection") or {}
    runs = max(1, int(sel.get("ai_runs", 3)))
    from core.ai.llm_client import can_send_external

    local = str(getattr(client, "name", "")).startswith("ollama") or (
        type(client).__name__ == "HybridClient" and not can_send_external(client))
    if local and sel.get("ai_runs_local") is not None:
        runs = max(1, int(sel["ai_runs_local"]))  # Ollama xử lý tuần tự – chấm 1 lần cho nhanh
    prompt = template.replace("{input_json}", masked.text)
    outs: list[dict[str, Any]] = []
    models: list[str] = []
    try:
        for i in range(runs):  # tự nhất quán: chấm nhiều lần, lấy trung vị
            outs.append(client.generate_json(prompt, SCHEMA, temperature=float(sel.get("ai_temperature", 0.3)),
                                             seed=i + 1))
            models.append(result_provider(client))
    except LlmError as e:
        return AiScores(model=client.name, prompt_version=version, error=str(e)[:200],
                        raw={"runs": outs, "models": models})
    scores, unstable, conf = combine_runs(outs, allowed, float(sel.get("ai_max_spread", 0.3)))
    raw = {"runs": outs, "models": models, "unstable": unstable}
    if conf < float(sel.get("ai_min_confidence", 0.6)):
        return AiScores(model=real_model(models, client), prompt_version=version, confidence=conf, raw=raw,
                        error=f"AI kém tự tin ({conf:.2f}) – dùng điểm quy tắc")
    return AiScores(scores=scores, ok=True, model=real_model(models, client), prompt_version=version, confidence=conf, raw=raw)


def combine_runs(outs: list[dict[str, Any]], allowed: set[str], max_spread: float
                 ) -> tuple[dict[str, tuple[float, str]], list[str], float]:
    """Gộp nhiều lần chấm: trung vị điểm; câu lệch giữa các lần > max_spread hoặc thiếu ở một lần → bỏ điểm AI câu đó
    (không ổn định); mã lạ (AI bịa) → bỏ. Độ tin cậy = trung vị confidence các lần."""
    per: dict[str, list[tuple[float, str]]] = {}
    duplicates: set[str] = set()
    for out in outs:
        seen: set[str] = set()
        for item in out.get("scores", []):
            code = str(item.get("code", ""))
            if code in allowed:
                if code in seen:
                    duplicates.add(code)
                    continue
                seen.add(code)
                per.setdefault(code, []).append((max(0.0, min(1.0, float(item.get("score", 0)))),
                                                 str(item.get("reason", ""))[:160]))
    scores: dict[str, tuple[float, str]] = {}
    unstable: list[str] = []
    for code, vals in per.items():
        xs = sorted(v for v, _r in vals)
        if code in duplicates or len(vals) != len(outs) or xs[-1] - xs[0] > max_spread:
            unstable.append(code)
            continue
        med = xs[len(xs) // 2]
        reason = next((r for v, r in vals if v == med and r), "")
        scores[code] = (med, reason)
    confs = sorted(float(o.get("confidence", 0)) for o in outs)
    return scores, unstable, confs[len(confs) // 2] if confs else 0.0
