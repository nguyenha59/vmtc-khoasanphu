"""Vòng học từ kết quả gọi: thống kê báo nhầm theo lý do (hàm thuần)."""

from __future__ import annotations

from core.ai.scorecard import load_targets
from core.feedback import AlertOutcome, summarize

CFG = {"min_handled": 3, "review_false_alarm_rate": 0.5, "review_downgrade_rate": 0.7}


def _o(i: int, reasons: list[str], final: str | None, level: str = "red", **kw) -> AlertOutcome:
    return AlertOutcome(alert_id=i, level=level, final_level=final, reasons=reasons, **kw)


def test_counts_and_rates_per_reason():
    outs = [_o(1, ["Đau đầu nhiều"], "false_alarm"), _o(2, ["Đau đầu nhiều"], "false_alarm"),
            _o(3, ["Đau đầu nhiều"], "keep_red"), _o(4, ["Ra máu âm đạo"], "keep_red"),
            _o(5, ["Ra máu âm đạo"], None)]
    r = {x["reason"]: x for x in summarize(outs, CFG)["reasons"]}
    assert r["Đau đầu nhiều"]["handled"] == 3 and r["Đau đầu nhiều"]["false_alarm"] == 2
    assert r["Đau đầu nhiều"]["false_alarm_rate"] == 0.667
    assert r["Ra máu âm đạo"]["n"] == 2 and r["Ra máu âm đạo"]["handled"] == 1  # chưa gọi không tính tỷ lệ


def test_needs_review_requires_enough_handled():
    few = [_o(1, ["Sốt"], "false_alarm"), _o(2, ["Sốt"], "false_alarm")]
    s = summarize(few, CFG)
    assert s["review"] == []  # mới 2 ca < min_handled
    more = few + [_o(3, ["Sốt"], "false_alarm")]
    assert summarize(more, CFG)["review"] == ["Sốt"]


def test_downgrade_rate_triggers_review():
    outs = [_o(i, ["Phù mặt / tay"], "down_yellow") for i in range(3)]
    assert summarize(outs, CFG)["review"] == ["Phù mặt / tay"]


def test_ai_reasons_and_escalation_counted():
    outs = [_o(1, [], "false_alarm", ai_reasons=["Khó thở"], ai_escalated=True),
            _o(2, ["Sốt"], "keep_red", ai_reasons=["Sốt"])]
    s = summarize(outs, CFG)
    assert s["ai_escalated"]["n"] == 1 and s["ai_escalated"]["false_alarm"] == 1
    sot = next(x for x in s["reasons"] if x["reason"] == "Sốt")
    assert sot["n"] == 1  # không đếm trùng khi quy tắc và AI cùng ra một lý do


def test_by_rules_version_shows_before_after():
    outs = [_o(1, ["A"], "false_alarm", rules_version="0.4.0"), _o(2, ["A"], "keep_red", rules_version="0.5.0")]
    v = {x["rules_version"]: x for x in summarize(outs, CFG)["by_rules_version"]}
    assert v["0.4.0"]["false_alarm_rate"] == 1.0 and v["0.5.0"]["false_alarm_rate"] == 0.0


def test_targets_file_has_feedback_section():
    fb = load_targets()["feedback"]
    assert {"min_handled", "review_false_alarm_rate", "review_downgrade_rate"} <= set(fb)
