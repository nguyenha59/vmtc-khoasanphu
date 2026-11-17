"""Cá nhân hóa bộ câu hỏi theo tình trạng từng thai phụ (13/11/2026, docs/12).

Hàm thuần: hồ sơ (tiền sử, chẩn đoán ICD-10, nhóm nguy cơ) + ghi chú AI + tuần thai → đề xuất module / bộ câu hỏi.
- Ánh xạ tình trạng → bộ câu hỏi nằm trong rules.yaml `personalization` (bác sĩ duyệt), không viết cứng.
- Câu hỏi trong bộ phải qua cổng chất lượng (core.question_quality); câu không đạt bị loại, kèm lý do.
- Hệ thống chỉ ĐỀ XUẤT; điều dưỡng xác nhận (lưu vào Patient.risk_groups như module hiện có, docs/04 B3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.config import FormsConfig, Rules
from core.question_quality import QuestionCheck, passing_questions
from core.rules.form_scheduler import schedule_text
from core.textutil import deaccent

SOURCE_VI = {"icd10": "Chẩn đoán", "risk": "Nhóm nguy cơ hồ sơ", "history": "Tiền sử", "ai_note": "AI đọc ghi chú lịch hẹn"}


@dataclass
class Evidence:
    source: str  # icd10 | risk | history | ai_note
    text: str


@dataclass
class FlagFound:
    flag: str
    name: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class PlanItem:
    code: str
    name: str
    kind: str  # module | pack
    flags: list[str]
    questions: list[str]
    rejected: list[QuestionCheck]
    confirmed: bool
    active_now: bool
    schedule: str  # "2 lần/tuần (T2, T5)" | "ghép vào form theo mốc" …
    window: str | None


@dataclass
class FormPlan:
    flags: list[FlagFound]
    items: list[PlanItem]
    pending_flags: list[str]  # nhóm chưa có bộ câu đạt chuẩn
    stale_confirmed: list[str]  # đã xác nhận nhưng hồ sơ hiện không còn căn cứ – để người duyệt xem lại


def _pack_schedule(rules: Rules) -> str:
    mid = (rules["forms"].get("packs") or {}).get("mid_gap_days")
    return "ghép vào form theo mốc" + (f"; thêm 1 lần nếu 2 lần khám cách nhau > {mid} ngày" if mid else "")


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(
        r"[;,–\n]|(?<!\d)\.(?!\d)|\s+(?:nhưng|nhung|tuy nhiên|tuy nhien|vì|vi)\s+",
        text, flags=re.IGNORECASE,
    ) if c.strip()]


def _keyword_hit(clause: str, keyword: str, negations: list[str]) -> bool:
    c, k = deaccent(clause.lower()), deaccent(keyword.lower())
    bridge = r"(?:bi|co|tien|su|ghi|nhan|mac|con|tung|dung|su|voi|dau|hieu)"
    for match in re.finditer(rf"(?<!\w){re.escape(k)}(?!\w)", c):
        before, after = c[:match.start()], c[match.end():]
        negated = any(
            re.search(rf"(?<!\w){re.escape(deaccent(n.lower()))}(?!\w)(?:\s+{bridge}){{0,5}}\s*$", before)
            or re.match(rf"\s*(?:truoc day\s*)?[:=]\s*{re.escape(deaccent(n.lower()))}(?!\w)", after)
            for n in negations
        )
        if not negated:
            return True
    return False


def find_flags(history: dict[str, Any] | None, diagnoses: list[dict[str, Any]], note_flags: list[str],
               rules: Rules) -> list[FlagFound]:
    cfg = rules.get("personalization") or {}
    names: dict[str, str] = cfg.get("flag_names", {})
    found: dict[str, FlagFound] = {}

    def add(flag: str, source: str, text: str) -> None:
        f = found.setdefault(flag, FlagFound(flag=flag, name=names.get(flag, flag)))
        if not any(e.source == source and e.text == text for e in f.evidence):
            f.evidence.append(Evidence(source, text))

    for d in diagnoses:
        code = str(d.get("code", ""))
        for prefix, flag in sorted(cfg.get("icd10_prefixes", {}).items(), key=lambda kv: -len(kv[0])):
            if code.startswith(prefix):
                add(flag, "icd10", f"{code} {d.get('name', '')}".strip())
                break
    h = history or {}
    for r in h.get("risk_summary") or []:
        flag = cfg.get("risk_codes", {}).get(r)
        if flag:
            add(flag, "risk", names.get(flag, r))
    negations = cfg.get("negation_words", [])
    for line in [*(h.get("medical_history") or []), *(h.get("obstetric_history") or [])]:
        for clause in _clauses(str(line)):
            for flag, kws in cfg.get("history_keywords", {}).items():
                if any(_keyword_hit(clause, k, negations) for k in kws):
                    add(flag, "history", str(line))
    for nf in note_flags:
        flag = cfg.get("note_flags", {}).get(nf)
        if flag:
            add(flag, "ai_note", nf)
    order = list(names)
    return sorted(found.values(), key=lambda f: order.index(f.flag) if f.flag in order else len(order))


def _in_window(ga: tuple[int, int] | None, mcfg: dict[str, Any]) -> bool:
    if "ga_from" not in mcfg:
        return True  # module bệnh lý (M-THA, M-DTD): không giới hạn tuần thai
    if ga is None:
        return False
    return tuple(mcfg["ga_from"]) <= ga <= tuple(mcfg["ga_to"])


def _window_text(mcfg: dict[str, Any]) -> str | None:
    if "ga_from" not in mcfg:
        return None
    a, b = mcfg["ga_from"], mcfg["ga_to"]
    return f"{a[0]}+{a[1]} – {b[0]}+{b[1]} tuần"


def build_plan(flags: list[FlagFound], confirmed: list[str], ga: tuple[int, int] | None, postpartum: bool,
               rules: Rules, forms_cfg: FormsConfig, *, on_insulin: bool = False) -> FormPlan:
    cfg = rules.get("personalization") or {}
    targets_by_flag: dict[str, list[str]] = cfg.get("flag_targets", {})
    modules_cfg: dict[str, Any] = rules["forms"]["modules"]
    packs_standalone_off = not (rules["forms"].get("packs") or {}).get("standalone", True)
    reasons: dict[str, list[str]] = {}
    pending: list[str] = []
    for f in flags:
        targets = targets_by_flag.get(f.flag, [])
        if not targets:
            pending.append(f.flag)
        for t in targets:
            reasons.setdefault(t, []).append(f.flag)
    codes = list(dict.fromkeys([*reasons, *[c for c in confirmed if c in modules_cfg]]))
    items: list[PlanItem] = []
    for code in codes:
        fdef = forms_cfg.forms.get(code)
        mcfg = modules_cfg.get(code, {})
        if fdef is None:
            continue
        kind = "pack" if fdef.get("kind") == "pack" else "module"
        # Module bệnh lý (đo HA / đường huyết) giữ nguyên bộ câu hiện hành; bộ theo tình trạng phải qua cổng chất lượng.
        ok, rejected = passing_questions(fdef["questions"], forms_cfg) if kind == "pack" else (list(fdef["questions"]), [])
        items.append(PlanItem(code=code, name=fdef["name"], kind=kind, flags=reasons.get(code, []), questions=ok,
                              rejected=rejected, confirmed=code in confirmed,
                              active_now=not postpartum and _in_window(ga, mcfg) and bool(ok),
                              schedule=schedule_text(code, mcfg, on_insulin, [f.flag for f in flags])
                              if kind == "module" or not packs_standalone_off else _pack_schedule(rules),
                              window=_window_text(mcfg)))
    stale = [c for c in confirmed if c in modules_cfg and c not in reasons]
    return FormPlan(flags=flags, items=items, pending_flags=pending, stale_confirmed=stale)


def compose_questions(form_code: str, confirmed: list[str], ga: tuple[int, int] | None, rules: Rules,
                      forms_cfg: FormsConfig) -> list[str]:
    """Câu hỏi thật sự hiện trên form của MỘT thai phụ.

    - Form định kỳ (F1–F6): câu gốc + câu đạt chuẩn của các bộ theo tình trạng đã xác nhận và đang trong cửa sổ tuần thai.
    - Bộ theo tình trạng (P-…): chỉ các câu đạt cổng chất lượng.
    - Module bệnh lý, sau sinh: giữ nguyên.
    """
    fdef = forms_cfg.forms[form_code]
    base = list(fdef["questions"])
    if fdef.get("kind") == "pack":
        return passing_questions(base, forms_cfg)[0]
    if form_code not in rules["forms"]["windows"]:
        return base
    extra: list[str] = []
    for code in confirmed:
        p = forms_cfg.forms.get(code)
        mcfg = rules["forms"]["modules"].get(code, {})
        if p is not None and p.get("kind") == "pack" and _in_window(ga, mcfg):
            extra += passing_questions(p["questions"], forms_cfg)[0]
    return list(dict.fromkeys([*base, *extra]))


def compose_merged(form_codes: list[str], confirmed: list[str], ga: tuple[int, int] | None, rules: Rules,
                   forms_cfg: FormsConfig) -> list[str]:
    """Câu hỏi của MỘT đường dẫn gộp nhiều form cùng ngày: hợp theo thứ tự, mỗi câu chỉ hỏi một lần."""
    out: list[str] = []
    for code in form_codes:
        if code in forms_cfg.forms:
            out += compose_questions(code, confirmed, ga, rules, forms_cfg)
    return list(dict.fromkeys(out))


def plan_as_dict(plan: FormPlan) -> dict[str, Any]:
    return {
        "flags": [{"flag": f.flag, "name": f.name,
                   "evidence": [{"source": e.source, "source_vi": SOURCE_VI.get(e.source, e.source), "text": e.text}
                                for e in f.evidence]} for f in plan.flags],
        "items": [{"code": i.code, "name": i.name, "kind": i.kind, "flags": i.flags, "questions": i.questions,
                   "rejected": [r.as_dict() for r in i.rejected], "confirmed": i.confirmed, "active_now": i.active_now,
                   "schedule": i.schedule, "window": i.window} for i in plan.items],
        "pending_flags": plan.pending_flags,
        "stale_confirmed": plan.stale_confirmed,
    }
