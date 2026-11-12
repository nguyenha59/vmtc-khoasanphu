"""Lớp 2 – xu hướng chỉ số bằng EWMA (docs/05 mục 4)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from core.config import Rules
from core.levels import Level


@dataclass
class TrendResult:
    kind: str
    level: Level
    reason: str = ""
    baseline: float | None = None
    sigma: float | None = None
    ewma: list[float] = field(default_factory=list)
    upper: float | None = None


def ewma(values: list[float], lam: float, start: float) -> list[float]:
    z, out = start, []
    for x in values:
        z = lam * x + (1 - lam) * z
        out.append(z)
    return out


def compute_trend(kind: str, readings: list[tuple[datetime, float]], rules: Rules) -> TrendResult:
    cfg = rules["trends"]
    pts = [v for _, v in sorted(readings, key=lambda r: r[0])]
    if len(pts) < cfg.get("min_readings", 3):
        return TrendResult(kind, Level.GREEN, "Chưa đủ 3 lần đo – không tính xu hướng")
    base_pts = pts[: cfg["baseline_readings"]]
    mean = statistics.fmean(base_pts)
    sigma = max(statistics.pstdev(base_pts) if len(base_pts) > 1 else 0.0, float(cfg.get("min_sigma", 0)))
    z = ewma(pts, cfg["ewma_lambda"], mean)
    upper = mean + cfg["sigma_k"] * sigma
    res = TrendResult(kind, Level.GREEN, "", baseline=mean, sigma=sigma, ewma=z, upper=upper)
    reasons = []
    if z[-1] > upper:
        reasons.append(f"EWMA {z[-1]:.0f} vượt nền {mean:.0f} + {cfg['sigma_k']}σ ({upper:.0f})")
    if kind == "bp_sys" and len(pts) >= cfg["consecutive_rises"]:
        tail = pts[-cfg["consecutive_rises"]:]
        rising = all(b > a for a, b in zip(tail, tail[1:], strict=False))
        if rising and tail[-1] >= mean + cfg["min_rise_mmhg"]:
            reasons.append(f"{cfg['consecutive_rises']} lần đo liên tiếp tăng, lần cuối {tail[-1]:.0f} ≥ nền + {cfg['min_rise_mmhg']}")
    if reasons:
        res.level, res.reason = Level.YELLOW, "; ".join(reasons)
    return res


def weight_trend(readings: list[tuple[datetime, float]], rules: Rules) -> TrendResult:
    limit = rules["thresholds"].get("weight_jump_kg_per_week")
    pts = sorted(readings, key=lambda r: r[0])
    if limit is None or len(pts) < 2:
        return TrendResult("weight", Level.GREEN, "")
    (t0, w0), (t1, w1) = pts[-2], pts[-1]
    weeks = max((t1 - t0).days / 7, 1 / 7)
    rate = (w1 - w0) / weeks
    if rate > float(limit):
        return TrendResult("weight", Level.YELLOW, f"Tăng cân {rate:.1f} kg/tuần > {limit}")
    return TrendResult("weight", Level.GREEN, "")


def trend_level(history: dict[str, list[tuple[datetime, float]]], rules: Rules) -> tuple[Level, list[TrendResult]]:
    results = [compute_trend(k, history[k], rules) for k in rules["trends"].get("ewma_kinds", []) if k in history]
    if "weight" in history:
        results.append(weight_trend(history["weight"], rules))
    level = max((r.level for r in results), default=Level.GREEN)
    return level, results
