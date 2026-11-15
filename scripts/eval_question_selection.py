"""Đo chất lượng chọn câu hỏi cá nhân hóa trên bộ đáp án (tests/golden/question_selection_gold.yaml). DỮ LIỆU GIẢ.

So sánh: chỉ quy tắc  vs  quy tắc + AI (QUESTION_SELECTOR_PROVIDER / rules.yaml). Đo:
- An toàn: câu bắt buộc giữ lại = 100% (bắt buộc).
- Độ chính xác (chỉ ca bác sĩ đã duyệt – status: approved): độ phủ must_ask, tỷ lệ chọn nhầm should_not_ask.
- Độ ổn định AI: chạy 2 lần, tỷ lệ bộ câu giống nhau.
- Độ dài form trung bình.
Kết quả ghi data/eval/question_selection_<ngày>.json.

Chạy: .venv\\Scripts\\python.exe scripts\\eval_question_selection.py [--provider inherit|hybrid|ollama|openai|mock|rules]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.ai.question_selector import ai_scores, selector_client  # noqa: E402
from core.config import TZ, load_env, load_forms, load_rules, work_dir  # noqa: E402
from core.rules.personalize import compose_merged  # noqa: E402
from core.rules.question_select import Context, select_questions  # noqa: E402

GOLD = ROOT / "tests" / "golden" / "question_selection_gold.yaml"


def run_case(case: dict[str, Any], rules: Any, forms: Any, client: Any) -> dict[str, Any]:
    ctx = Context(ga=tuple(case["ga"]), flags=case["flags"], confirmed=case["confirmed"],  # type: ignore[arg-type]
                  flagged_answers=case.get("flagged_answers") or {}, recent_concepts=case.get("recent_concepts") or [])
    base = compose_merged([case["form"]], ctx.confirmed, ctx.ga, rules, forms)
    rules_only = [c.code for c in select_questions([case["form"]], ctx, rules, forms)]
    out: dict[str, Any] = {"id": case["id"], "base": base, "rules": rules_only}
    if client is not None:
        runs = []
        for _ in range(2):
            ai = ai_scores([case["form"]], ctx, rules, forms, client)
            runs.append((ai, [c.code for c in select_questions([case["form"]], ctx, rules, forms,
                                                               ai.scores if ai.ok else None)]))
        out["ai"] = runs[0][1]
        out["ai_ok"] = runs[0][0].ok
        out["ai_error"] = runs[0][0].error
        valid_pair = all(ai.ok for ai, _chosen in runs)
        out["ai_stability_eligible"] = valid_pair
        out["ai_stable"] = runs[0][1] == runs[1][1] if valid_pair else None
        out["ai_set_stable"] = set(runs[0][1]) == set(runs[1][1]) if valid_pair else None
        out["ai_unstable_codes"] = runs[0][0].raw.get("unstable", [])
    return out


def stability_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in results if r.get("ai_stability_eligible")]
    attempted = [r for r in results if "ai_ok" in r]
    return {
        "ai_stability_cases": len(eligible),
        "ai_success_rate": sum(r["ai_ok"] for r in attempted) / len(attempted) if attempted else None,
        "ai_stable_rate": sum(r["ai_stable"] for r in eligible) / len(eligible) if eligible else None,
        "ai_set_stable_rate": sum(r["ai_set_stable"] for r in eligible) / len(eligible) if eligible else None,
    }


def score(sel: list[str], case: dict[str, Any]) -> dict[str, float | None]:
    must, bad = set(case.get("must_ask") or []), set(case.get("should_not_ask") or [])
    return {"recall_must": len(must & set(sel)) / len(must) if must else None,
            "wrong_rate": len(bad & set(sel)) / len(sel) if bad and sel else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", help="inherit | rules | mock | ollama | hybrid | openai (mặc định: .env / rules.yaml)")
    args = ap.parse_args()
    load_env()
    if args.provider:
        os.environ["QUESTION_SELECTOR_PROVIDER"] = args.provider
    rules, forms = load_rules(), load_forms()
    client = selector_client(rules)
    gold = yaml.safe_load(GOLD.read_text(encoding="utf-8"))
    results, safety_ok = [], True
    for case in gold["cases"]:
        r = run_case(case, rules, forms, client)
        for key in ("rules", "ai"):
            if key in r and not set(r["base"]) <= set(r[key]):
                safety_ok = False
        approved = case.get("status") == "approved"
        r["score_rules"] = score(r["rules"], case) if approved else None
        r["score_ai"] = score(r["ai"], case) if approved and "ai" in r else None
        results.append(r)
        print(f"{r['id']}: quy tắc {len(r['rules'])} câu {r['rules']}")
        if "ai" in r:
            print(f"      AI      {len(r['ai'])} câu {r['ai']}  ổn định={r['ai_stable']}"
                  + (f"  lỗi={r['ai_error']}" if r["ai_error"] else ""))
    n_approved = sum(c.get("status") == "approved" for c in gold["cases"])
    summary = {
        "at": datetime.now(TZ).isoformat(timespec="seconds"), "gold_version": gold["version"],
        "rules_version": rules.rules_version, "model": getattr(client, "name", None),
        "safety_mandatory_kept": safety_ok, "approved_cases": n_approved,
        "avg_len_rules": sum(len(r["rules"]) for r in results) / len(results),
        "avg_len_ai": (sum(len(r["ai"]) for r in results if "ai" in r) / len(results)) if client else None,
        **stability_summary(results),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if n_approved == 0:
        print("Chưa có ca nào bác sĩ duyệt (status: approved) → CHƯA đo được độ chính xác, chỉ đo an toàn / ổn định / độ dài.")
    out = work_dir("eval") / f"question_selection_{datetime.now(TZ):%Y%m%d_%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {out}")
    if not safety_ok:
        raise SystemExit("LỖI AN TOÀN: có câu bắt buộc bị bỏ.")


if __name__ == "__main__":
    main()
