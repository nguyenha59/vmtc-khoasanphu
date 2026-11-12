"""Dữ liệu biểu đồ chỉ số (huyết áp, đường huyết) dạng JSON cho giao diện web; ngưỡng lấy từ rules.yaml."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.ai.trends import compute_trend
from core.config import Rules

GLUCOSE_KINDS = {"glu_fast": "Lúc đói", "glu_1h": "Sau ăn 1 giờ", "glu_2h": "Sau ăn 2 giờ"}


def _pts(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    return sorted(points or [])


def bp_series(history: dict[str, list[tuple[datetime, float]]], rules: Rules) -> dict[str, Any] | None:
    sys_pts, dia_pts = _pts(history.get("bp_sys", [])), _pts(history.get("bp_dia", []))
    if not sys_pts:
        return None
    th = rules["thresholds"]
    tr = compute_trend("bp_sys", sys_pts, rules)
    dia = {t: v for t, v in dia_pts}
    rows = []
    for i, (t, v) in enumerate(sys_pts):
        rows.append({"t": t.isoformat(), "sys": v, "dia": dia.get(t),
                     "ewma": round(tr.ewma[i], 1) if tr.ewma and i < len(tr.ewma) else None})
    return {"rows": rows, "yellow": th["bp_yellow"]["sys"], "red": th["bp_red"]["sys"],
            "upper": round(tr.upper, 1) if tr.upper is not None and tr.ewma else None,
            "sigma_k": rules["trends"]["sigma_k"]}


def glucose_series(history: dict[str, list[tuple[datetime, float]]], rules: Rules) -> dict[str, Any] | None:
    if not any(history.get(k) for k in GLUCOSE_KINDS):
        return None
    by_t: dict[datetime, dict[str, Any]] = {}
    for k in GLUCOSE_KINDS:
        for t, v in _pts(history.get(k, [])):
            by_t.setdefault(t, {"t": t.isoformat()})[k] = v
    return {"rows": [by_t[t] for t in sorted(by_t)], "labels": GLUCOSE_KINDS,
            "fasting_threshold": rules["thresholds"]["glucose_yellow"]["fasting"]}


def series_for(history: dict[str, list[tuple[datetime, float]]], rules: Rules) -> dict[str, Any]:
    return {"bp": bp_series(history, rules), "glucose": glucose_series(history, rules)}
