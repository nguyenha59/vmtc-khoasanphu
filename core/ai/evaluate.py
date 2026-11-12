"""Chạy bộ kiểm thử AI và tính chỉ số (docs/05 mục 7, docs/08)."""

from __future__ import annotations

from collections import Counter
from datetime import date, time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from core.ai.combine import combine
from core.ai.llm_client import LlmClient
from core.config import ROOT, Rules, prompt_text
from core.db import now
from core.levels import Level
from core.models import EvalRun

GOLDEN = ROOT / "tests" / "golden"
REDTEAM = ROOT / "tests" / "redteam" / "form_cases.yaml"
BENCH = ROOT / "tests" / "benchmark"


def bench_dir(version: str, tier: str) -> Path:
    """Thư mục một tầng của benchmark, ví dụ tests/benchmark/v1/quick."""
    return BENCH / version / tier


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


# ---------------------------------------------------------------- ghi chú
def evaluate_notes(client: LlmClient | None, rules: Rules, limit: int | None = None, ner: Any = True,
                   data_dir: Path | None = None) -> dict[str, Any]:
    from core.pipeline import plan_appointment
    from core.rules.arrival import arrival_text
    from core.rules.merge import MergedAppt

    src = data_dir / "notes.csv" if data_dir else GOLDEN / "notes_eval.csv"
    df = pd.read_csv(src, dtype=str, keep_default_na=False)
    if limit:
        df = df.head(limit)
    n = len(df)
    auto = correct_auto = ga_total = ga_ok = status_ok = 0
    errors: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        m = MergedAppt(pid=r["pid"], appt_date=date.fromisoformat(r["appt_date"]),
                       start_time=time.fromisoformat(r["start_time"]), end_time=None,
                       services=[x.strip() for x in r["services"].split(";") if x.strip()], note=r["note"],
                       full_name="", phone="0900000000", phone_raw="", nationality=r["nationality"],
                       department="", doctor=r.get("doctor", ""), booking_source="", booked_at=None,
                       conflict="CONFLICT" in r["reasons_ai"])
        p = plan_appointment(m, rules, client, [r.get("doctor", "")], ner=ner)
        got = arrival_text(p.arrival, rules["arrival"])
        if p.decision.status == "auto":
            auto += 1
            if got == r["arrival_true"]:
                correct_auto += 1
            else:
                errors.append({"note": r["note"], "expected": r["arrival_true"], "got": got})
        if r["ga_weeks"]:
            ga_total += 1
            ga_ok += int(str(p.decision.ga_weeks) == r["ga_weeks"])
        status_ok += int(p.decision.status == r["status_ai"])
    return {
        "n": n,
        "auto_rate": _rate(auto, n),
        "transfer_rate": _rate(n - auto, n),
        "arrival_accuracy_auto": _rate(correct_auto, auto),
        "ga_accuracy": _rate(ga_ok, ga_total),
        "status_agreement": _rate(status_ok, n),
        "errors": errors[:20],
    }


# ---------------------------------------------------------------- form
def _confusion(rows: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    levels = ["green", "yellow", "red"]
    m = {e: dict.fromkeys(levels, 0) for e in levels}
    for exp, got in rows:
        m[exp][got] += 1
    return m


def _form_metrics(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    reds = [(e, g) for e, g in pairs if e == "red"]
    non_red = [(e, g) for e, g in pairs if e != "red"]
    greens = [(e, g) for e, g in pairs if e == "green"]
    return {
        "n": len(pairs),
        "red_sensitivity": _rate(sum(1 for _, g in reds if g == "red"), len(reds)),
        "red_specificity": _rate(sum(1 for _, g in non_red if g != "red"), len(non_red)),
        "exact_agreement": _rate(sum(1 for e, g in pairs if e == g), len(pairs)),
        "false_alarm_rate": _rate(sum(1 for _, g in greens if g != "green"), len(greens)),
        "green_to_red_rate": _rate(sum(1 for _, g in greens if g == "red"), len(greens)),
        "under_triage": sum(1 for e, g in pairs if ["green", "yellow", "red"].index(g) < ["green", "yellow", "red"].index(e)),
        "confusion": _confusion(pairs),
    }


def _why(reading: Any) -> list[str]:
    """Lý do AI nâng mức: khái niệm có mặt + mối lo ngoài danh mục (để soi báo nhầm)."""
    out = [f"{f.concept}/{f.experiencer}/{f.temporality}/{f.source}" for f in reading.findings
           if f.present and f.level != "green"]
    return out[:5] + [f"unmapped: {u}" for u in reading.unmapped[:2]]


def _over(expected: str, got: str) -> bool:
    order = ["green", "yellow", "red"]
    return order.index(got) > order.index(expected)


def evaluate_redteam(client: LlmClient | None, rules: Rules, ner: Any = True,
                     path: Path | None = None) -> dict[str, Any]:
    from core.ai.form_reader import read_free_text

    cases = yaml.safe_load((path or REDTEAM).read_text(encoding="utf-8"))["cases"]
    pairs, by_group, misses, over = [], {}, [], []
    for c in cases:
        r = read_free_text(c["text"], c["ga_weeks"], client, rules, ner=ner)
        # Đi đúng đường như hệ thống thật: combine (AI lỗi / độ tin cậy thấp → tối thiểu vàng).
        has_text = bool((c["text"] or "").strip())
        got = combine(Level.GREEN, Level.GREEN, r.level, ai_ok=r.ok or not has_text,
                      ai_confidence=r.confidence if has_text else 1.0,
                      min_confidence=rules["arrival"]["ai_confidence_min"]).level.code
        pairs.append((c["expected_level"], got))
        by_group.setdefault(c["group"], []).append((c["expected_level"], got))
        if c["expected_level"] == "red" and got != "red":
            misses.append(c["text"])
        if _over(c["expected_level"], got):
            over.append({"group": c["group"], "expected": c["expected_level"], "got": got,
                         "text": c["text"], "why": _why(r)})
    out = _form_metrics(pairs)
    out["by_group"] = {g: _form_metrics(v)["exact_agreement"] for g, v in by_group.items()}
    out["missed_red"] = misses
    out["over_triage_cases"] = over
    return out


def evaluate_forms(client: LlmClient | None, rules: Rules, limit: int | None = None, ner: Any = True,
                   data_dir: Path | None = None) -> dict[str, Any]:
    """Bộ câu trả lời form có đáp án + so sánh quy tắc / quy tắc + AI."""
    from core.assess import assess_answers
    from core.config import load_forms
    from core.ingest.forms_import import parse_msforms_csv

    forms_cfg = load_forms()
    label_src = data_dir / "form_labels.csv" if data_dir else GOLDEN / "form_expected.csv"
    labels = pd.read_csv(label_src, dtype=str, keep_default_na=False).set_index("token")
    path = data_dir / "form_responses.csv" if data_dir else ROOT / "data" / "synthetic" / "form_responses.csv"
    if not path.exists():
        return {"error": "Chưa có data/synthetic/form_responses.csv – chạy scripts/gen_synthetic.py"}
    parsed = parse_msforms_csv(path.read_bytes(), forms_cfg)
    if limit:
        parsed = parsed[:limit]
    pairs_full, pairs_rule, groups, over = [], [], {}, []
    extra_red_by_ai = 0
    for item in parsed:
        lab = labels.loc[item.token]
        ga = int(lab["ga_weeks"]) if lab["ga_weeks"] else None
        res = assess_answers(item.form_code, item.answers, item.free_text, ga, rules, forms_cfg,
                             client=client, history={}, on_insulin=False, ner=ner)
        pairs_full.append((lab["expected_level"], res.level.code))
        pairs_rule.append((lab["expected_level"], res.rule_level.code))
        groups.setdefault(lab["group"], []).append((lab["expected_level"], res.level.code))
        if _over(lab["expected_level"], res.level.code):
            over.append({"token": item.token, "group": lab["group"], "expected": lab["expected_level"],
                         "got": res.level.code, "text": item.free_text or "", "why": res.reasons[:3] + _why(res.reading)})
        if res.level.code == "red" and res.rule_level.code != "red" and lab["expected_level"] == "red":
            extra_red_by_ai += 1
    out = {
        "rules_plus_ai": _form_metrics(pairs_full),
        "rules_only": _form_metrics(pairs_rule),
        "extra_red_caught_by_ai": extra_red_by_ai,
        "by_group": {g: _form_metrics(v)["red_sensitivity"] for g, v in groups.items()},
        "by_group_exact": {g: _form_metrics(v)["exact_agreement"] for g, v in groups.items()},
        "over_triage_cases": over,
    }
    out["redteam"] = evaluate_redteam(client, rules, ner=ner, path=data_dir / "redteam.yaml" if data_dir else None)
    return out


# ---------------------------------------------------------------- lưu
def save_eval(session: Any, dataset: str, metrics: dict[str, Any], rules: Rules, model: str,
              prompt_name: str | None) -> EvalRun:
    version = prompt_text(prompt_name)[1] if prompt_name else None
    run = EvalRun(ts=now(), dataset=dataset, prompt_version=version, rules_version=rules.rules_version,
                  model=model, metrics=metrics)
    session.add(run)
    session.flush()
    return run


def summarize_counter(c: Counter[str]) -> dict[str, int]:
    return dict(c.most_common())


