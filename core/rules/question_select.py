"""Chọn + xếp câu hỏi cho MỘT đường dẫn form của MỘT thai phụ (docs/13 mục 5c). Hàm thuần.

Lớp 1 – bắt buộc (không bao giờ bị bỏ): câu của form mốc / module / bộ theo tình trạng (đã qua cổng chất lượng) +
        câu "hỏi lại" (lần trước trả lời Có / Không chắc, hoặc phần tự viết nói tới triệu chứng đó).
Lớp 2 – câu tùy chọn từ kho: đạt cổng chất lượng, đúng tuần thai, không phải câu chỉ dành cho module; chấm điểm theo
        quy tắc (liên quan tình trạng, phần tự viết) – cấu hình rules.yaml `personalization.selection`.
Lớp 3 – điểm AI (tùy chọn): chỉ được NÂNG điểm (điểm cuối = max(quy tắc, AI)), không xóa được câu bắt buộc.
Thứ tự: 1 hỏi lại → 2 dấu hiệu nguy hiểm theo tình trạng → 3 dấu hiệu nguy hiểm khác → 4 số đo → 5 hỗ trợ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import FormsConfig, Rules
from core.question_quality import check_question
from core.rules.personalize import compose_merged
from core.rules.triage_rules import RED_ITEMS

GROUPS = {1: "Cần hỏi lại", 2: "Dấu hiệu cần chú ý theo tình trạng của chị", 3: "Dấu hiệu cần chú ý",
          4: "Số đo", 5: "Theo dõi chung"}


@dataclass
class Context:
    """Thông tin KHÔNG định danh dùng để chọn câu (cũng là thứ gửi cho AI, sau khi qua masking)."""
    ga: tuple[int, int] | None
    flags: list[str]  # nhóm nguy cơ đã nhận ra (TWINS, HTN…)
    confirmed: list[str]  # module / bộ câu đã xác nhận
    flagged_answers: dict[str, str] = field(default_factory=dict)  # câu → "Có" / "Không chắc" (lần trước)
    recent_concepts: list[str] = field(default_factory=list)  # khái niệm AI đọc từ phần tự viết lần trước
    clinical: dict[str, Any] = field(default_factory=dict)  # tóm tắt đã phân loại (core/rules/ai_context.py) – chỉ AI dùng
    postpartum: bool = False


@dataclass
class Chosen:
    code: str
    group: int
    score: float
    mandatory: bool
    reasons: list[str]


def _applies(q: dict[str, Any], ga: tuple[int, int] | None) -> bool:
    rng = q.get("ga")
    if not rng:
        return True
    return ga is not None and rng[0] <= ga[0] <= rng[1]


def _postpartum_codes(forms_cfg: FormsConfig) -> set[str]:
    return {q for code, form in forms_cfg.forms.items() if code.startswith("S") and code[1:].isdigit()
            for q in form["questions"]}


def _eligible(code: str, ctx: Context, forms_cfg: FormsConfig, base: list[str]) -> bool:
    q = forms_cfg.questions[code]
    if ctx.postpartum:
        # Reuse the clinic's explicit postpartum bank. A deliberately requested measurement
        # module can retain its measurements; pregnancy symptoms cannot re-enter through AI/carry.
        return code in _postpartum_codes(forms_cfg) or (code in base and bool(q.get("module_only")))
    return _applies(q, ctx.ga)


def base_questions(form_codes: list[str], ctx: Context, rules: Rules, forms_cfg: FormsConfig) -> list[str]:
    base = compose_merged(form_codes, ctx.confirmed, ctx.ga, rules, forms_cfg)
    return [code for code in base if not ctx.postpartum or _eligible(code, ctx, forms_cfg, base)]


def candidates(base: list[str], ctx: Context, forms_cfg: FormsConfig) -> list[str]:
    """Câu tùy chọn có thể thêm: đạt cổng chất lượng, đúng tuần thai, không phải câu chỉ dành cho module, chưa có."""
    out = []
    for code, q in forms_cfg.questions.items():
        if code in base or q.get("module_only") or not _eligible(code, ctx, forms_cfg, base):
            continue
        if check_question(code, q, forms_cfg.quality).passed:
            out.append(code)
    return out


def rule_scores(cands: list[str], ctx: Context, rules: Rules, forms_cfg: FormsConfig) -> dict[str, tuple[float, list[str]]]:
    cfg = (rules.get("personalization") or {})
    sc = cfg.get("selection", {}).get("rule_scores", {})
    names = cfg.get("flag_names", {})
    from_concept = {cfg.get("concept_questions", {}).get(c) for c in ctx.recent_concepts}
    out: dict[str, tuple[float, list[str]]] = {}
    for code in cands:
        q = forms_cfg.questions[code]
        score, why = float(sc.get("base", 0.1)), []
        hit = [f for f in q.get("conditions", []) if f in ctx.flags]
        if hit:
            score = max(score, float(sc.get("condition", 0.7)))
            why.append("Liên quan: " + ", ".join(names.get(f, f) for f in hit))
        if code in from_concept:
            score = max(score, float(sc.get("concept", 0.8)))
            why.append("Lần trước chị tự viết về triệu chứng này")
        out[code] = (score, why)
    return out


def select_questions(form_codes: list[str], ctx: Context, rules: Rules, forms_cfg: FormsConfig,
                     ai: dict[str, tuple[float, str]] | None = None) -> list[Chosen]:
    """Trả danh sách câu đã chọn, đã xếp. `ai` = {mã câu: (điểm 0–1, lý do)} – chỉ được nâng điểm."""
    cfg = (rules.get("personalization") or {})
    sel = cfg.get("selection", {})
    names = cfg.get("flag_names", {})
    ai = ai or {}
    base = base_questions(form_codes, ctx, rules, forms_cfg)
    base_set = set(base)

    # Lớp 1: câu hỏi lại (đúng tuần thai, đạt chất lượng; câu số đo chỉ khi đang ở module có câu đó)
    carry: dict[str, list[str]] = {}
    for code, ans in ctx.flagged_answers.items():
        q = forms_cfg.questions.get(code)
        if q and _eligible(code, ctx, forms_cfg, base) and (code in base_set or not q.get("module_only")) \
                and check_question(code, q, forms_cfg.quality).passed:
            carry.setdefault(code, []).append(f"Lần trước chị trả lời “{ans}”")
    for concept in ctx.recent_concepts:
        code = cfg.get("concept_questions", {}).get(concept)
        q = forms_cfg.questions.get(code or "")
        if code and q and _eligible(code, ctx, forms_cfg, base) and check_question(code, q, forms_cfg.quality).passed:
            carry.setdefault(code, []).append("Lần trước chị tự viết về triệu chứng này")

    chosen: dict[str, Chosen] = {}

    def group_of(code: str, cond_hit: bool) -> int:
        if code in carry:
            return 1
        section = forms_cfg.questions[code].get("section", "danger")
        if section == "measure":
            return 4
        if section == "support":
            return 5
        return 2 if cond_hit else 3

    for code in base:
        q = forms_cfg.questions[code]
        hit = [f for f in q.get("conditions", []) if f in ctx.flags]
        why = ["Câu của form theo mốc / module / bộ theo tình trạng"]
        if hit:
            why.append("Liên quan: " + ", ".join(names.get(f, f) for f in hit))
        a = ai.get(code)
        if a and a[1]:
            why.append(f"AI: {a[1]}")
        chosen[code] = Chosen(code, group_of(code, bool(hit)), max(1.0 if hit else 0.5, a[0] if a else 0.0), True,
                              carry.get(code, []) + why)
    for code, why in carry.items():
        if code not in chosen:
            chosen[code] = Chosen(code, 1, 1.0, True, why)

    # Lớp 2 + 3: câu tùy chọn
    cands = [c for c in candidates(base, ctx, forms_cfg) if c not in chosen]
    rs = rule_scores(cands, ctx, rules, forms_cfg)
    threshold = float(sel.get("include_threshold", 0.6))
    ai_add = float(sel.get("ai_add_threshold", threshold))
    scored = []
    for code in cands:
        r_score, why = rs[code]
        a = ai.get(code)
        # AI chỉ nâng; câu quy tắc chưa chọn thì AI phải chắc (≥ ai_add_threshold) mới được thêm (ổn định hơn).
        a_score = a[0] if a and (r_score >= threshold or a[0] >= ai_add) else 0.0
        score = max(r_score, a_score)
        if a_score > r_score and a and a[1]:
            why = [*why, f"AI: {a[1]}"]
        scored.append((score, code, why))
    room = max(0, int(sel.get("max_questions", 15)) - len(chosen))
    for score, code, why in sorted(scored, key=lambda x: -x[0]):
        if room <= 0 or score < threshold:
            break
        hit = any(f in ctx.flags for f in forms_cfg.questions[code].get("conditions", []))
        chosen[code] = Chosen(code, group_of(code, hit), score, False, why or ["Điểm phù hợp cao"])
        room -= 1

    order = {c: i for i, c in enumerate([*base, *carry, *[c for _s, c, _w in scored]])}
    # Thứ tự trong khối: câu mức ĐỎ trước VÀNG, rồi thứ tự của khoa – KHÔNG theo chênh lệch điểm nhỏ của AI
    # (đo thực tế: xếp theo điểm AI làm thứ tự đổi giữa các lần chạy). Điểm chỉ quyết định thêm câu nào.
    return sorted(chosen.values(), key=lambda c: (c.group, 0 if c.code in RED_ITEMS or c.code == "C9" else 1, order.get(c.code, 999)))


def as_dicts(chosen: list[Chosen], forms_cfg: FormsConfig) -> list[dict[str, Any]]:
    return [{"code": c.code, "text": forms_cfg.questions[c.code]["text"], "group": c.group, "group_label": GROUPS[c.group],
             "score": round(c.score, 2), "mandatory": c.mandatory, "reasons": c.reasons} for c in chosen]
