"""Màn Chất lượng AI: độ nhạy ca đỏ, AI khớp người, chuyển người, red-team; theo tuần và phiên bản."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from api.deps import CurrentUser, require
from api.util import df_records, jsonable
from core import audit
from core.ai.scorecard import fmt, load_targets
from core.ai.scorecard import scorecard as evaluate_scorecard
from core.db import session_scope
from core.feedback import outcomes_from_db, summarize
from core.quality import eval_table, human_agreement, latest_eval, recent_errors, weekly_table

router = APIRouter(prefix="/api/quality", tags=["quality"])
DS_VI = {"notes": "Ghi chú", "forms": "Form + red-team", "noshow": "Bỏ hẹn"}


@router.get("")
def quality(user: CurrentUser = Depends(require("view_quality"))) -> dict:
    with session_scope() as s:
        audit.log("view_quality", "eval_run", None, user_id=user.user_id, session=s)
        runs = {ds: latest_eval(s, ds) for ds in DS_VI}
        targets = load_targets()
        scorecard = []
        for ds, r in runs.items():
            # Recompute against the current target set. A missing run or missing stored
            # scorecard cannot turn required, unmeasured metrics into a passing dashboard.
            for check in evaluate_scorecard(ds, (r.metrics or {}) if r else {}, targets):
                c = check.to_dict()
                scorecard.append({
                    "dataset": DS_VI[ds], "label": c["label"], "value": fmt(c["value"]), "target": f"{c['op']} {fmt(c['target'])}",
                    "passed": c["passed"], "blocking": bool(c["blocking"]),
                    "run": (f"{r.ts:%d/%m %H:%M} · {r.model} · {r.prompt_version or ''} · quy tắc {r.rules_version}"
                            + (f" · {(r.metrics or {}).get('limit')} mẫu" if (r.metrics or {}).get("limit") else ""))
                           if r else "Chưa có lần chạy đánh giá",
                })
        human = human_agreement(s)
        feedback = summarize(outcomes_from_db(s), load_targets().get("feedback", {}))
        table = df_records(eval_table(s))
        weekly = df_records(weekly_table(s))
        errors = jsonable(recent_errors(s))
        notes = runs["notes"]
        forms = runs["forms"]
        fm = (forms.metrics if forms else {}) or {}
        nm = (notes.metrics if notes else {}) or {}
        notes_info = None if notes is None else {"ts": notes.ts.isoformat(), "model": notes.model, "prompt": notes.prompt_version,
                                                 "auto_rate": nm.get("auto_rate"), "acc_auto": nm.get("arrival_accuracy_auto"),
                                                 "ga_acc": nm.get("ga_accuracy")}
    rt = fm.get("redteam", {}) or {}
    return {
        "cards": {"red_sensitivity": fm.get("rules_plus_ai", {}).get("red_sensitivity"),
                  "arrival_agreement": human["arrival_agreement"], "arrival_reviewed": human["arrival_reviewed"],
                  "alert_agreement": human["alert_agreement"], "transfer_rate": human["transfer_rate"],
                  "arrival_total": human["arrival_total"], "redteam_sensitivity": rt.get("red_sensitivity"),
                  "redteam_n": rt.get("n", 0), "redteam_false_alarm": rt.get("false_alarm_rate")},
        "scorecard": scorecard,
        "blocking_failed": any(c["passed"] is not True and c["blocking"] for c in scorecard),
        "notes": notes_info,
        "forms": None if not fm else {
            "rules_only": fm.get("rules_only", {}).get("red_sensitivity"), "rules_plus_ai": fm.get("rules_plus_ai", {}).get("red_sensitivity"),
            "extra_red": fm.get("extra_red_caught_by_ai", 0), "confusion": fm.get("rules_plus_ai", {}).get("confusion", {}),
            "by_group": fm.get("by_group", {}), "missed_red": rt.get("missed_red", [])},
        "history": table, "weekly": weekly, "errors": errors,
        "feedback": feedback,
    }


@router.post("/run/{dataset}")
async def run_eval(dataset: Literal["notes", "forms", "noshow"], user: CurrentUser = Depends(require("run_eval"))) -> dict:
    from core.ai.noshow import ModelNotReady
    from scripts.run_eval import run

    try:
        await run_in_threadpool(run, dataset, None, user.user_id)
    except (SystemExit, ModelNotReady) as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}
