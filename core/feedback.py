"""Vòng học từ kết quả gọi (phương án A): so mức hệ thống đặt với mức điều dưỡng/bác sĩ chốt sau khi gọi.

Nhãn lấy từ Alert.final_level (keep_red = giữ mức, down_yellow = hạ vàng, false_alarm = báo nhầm).
Không tự sửa quy tắc: chỉ chỉ ra lý do nào hay bị báo nhầm để bác sĩ xem lại danh mục / ngưỡng.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Alert, Assessment, FormResponse

KEEP, DOWN, FALSE = "keep_red", "down_yellow", "false_alarm"


@dataclass(frozen=True)
class AlertOutcome:
    alert_id: int
    level: str
    final_level: str | None
    reasons: list[str] = field(default_factory=list)
    ai_reasons: list[str] = field(default_factory=list)
    ai_escalated: bool = False
    rules_version: str = ""
    prompt_version: str = ""


def _counts(items: list[AlertOutcome]) -> dict[str, Any]:
    handled = [o for o in items if o.final_level in (KEEP, DOWN, FALSE)]
    n_h = len(handled)
    keep = sum(o.final_level == KEEP for o in handled)
    down = sum(o.final_level == DOWN for o in handled)
    false = sum(o.final_level == FALSE for o in handled)
    return {
        "n": len(items), "handled": n_h, "keep": keep, "down": down, "false_alarm": false,
        "false_alarm_rate": round(false / n_h, 3) if n_h else None,
        "downgrade_rate": round((down + false) / n_h, 3) if n_h else None,
    }


def _needs_review(c: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if c["handled"] < int(cfg.get("min_handled", 5)):
        return False
    return (c["false_alarm_rate"] or 0) >= float(cfg.get("review_false_alarm_rate", 0.5)) or \
        (c["downgrade_rate"] or 0) >= float(cfg.get("review_downgrade_rate", 0.7))


def summarize(outcomes: list[AlertOutcome], cfg: dict[str, Any]) -> dict[str, Any]:
    """Thống kê theo lý do, theo AI, theo phiên bản quy tắc. Hàm thuần."""
    by_reason: dict[str, list[AlertOutcome]] = defaultdict(list)
    for o in outcomes:
        for r in dict.fromkeys(o.reasons + o.ai_reasons):  # bỏ trùng, giữ thứ tự
            by_reason[r].append(o)
    reasons = []
    for r, items in by_reason.items():
        c = _counts(items)
        levels = {o.level for o in items}
        reasons.append({"reason": r, "levels": sorted(levels), **c, "needs_review": _needs_review(c, cfg)})
    reasons.sort(key=lambda x: (not x["needs_review"], -(x["false_alarm_rate"] or 0), -x["n"]))

    ai_esc = [o for o in outcomes if o.ai_escalated]
    by_version: dict[str, list[AlertOutcome]] = defaultdict(list)
    for o in outcomes:
        by_version[o.rules_version or "?"].append(o)
    versions = [{"rules_version": v, **_counts(items)} for v, items in sorted(by_version.items())]

    return {
        "overall": _counts(outcomes),
        "reasons": reasons,
        "ai_escalated": _counts(ai_esc),
        "by_rules_version": versions,
        "review": [r["reason"] for r in reasons if r["needs_review"]],
        "config": {k: cfg.get(k) for k in ("min_handled", "review_false_alarm_rate", "review_downgrade_rate")},
    }


def outcomes_from_db(s: Session) -> list[AlertOutcome]:
    out: list[AlertOutcome] = []
    q = (select(Alert, Assessment).join(Assessment, Alert.assessment_id == Assessment.id)
         .join(FormResponse, Assessment.response_id == FormResponse.id)
         .where(FormResponse.source != "demo"))  # câu trả lời do nhân viên tự điền ở Form demo: không phải nhãn thật
    for a, asm in s.execute(q).all():
        f = asm.findings or {}
        reasons = list(f.get("reasons") or [x.get("reason", "") for x in f.get("rule", [])])
        ai_reasons = [x["name"] for x in f.get("ai", [])
                      if x.get("present") and x.get("level") in ("red", "yellow") and x.get("name")]
        out.append(AlertOutcome(alert_id=a.id, level=a.level, final_level=a.final_level,
                                reasons=[r for r in reasons if r], ai_reasons=ai_reasons,
                                ai_escalated=bool(f.get("ai_escalated")), rules_version=asm.rules_version or "",
                                prompt_version=asm.prompt_version or ""))
    return out
