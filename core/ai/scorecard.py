"""Bảng chỉ tiêu: so chỉ số đánh giá với config/eval_targets.yaml → ĐẠT / KHÔNG ĐẠT (hàm thuần)."""

from __future__ import annotations

import operator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from core.config import CONFIG_DIR

OPS = {">=": operator.ge, "<=": operator.le, "==": operator.eq, ">": operator.gt, "<": operator.lt}


@dataclass(frozen=True)
class Check:
    label: str
    value: float | None
    op: str
    target: float | None
    blocking: bool
    passed: bool | None  # None = chưa có số liệu

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_targets(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_DIR / "eval_targets.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get(metrics: dict[str, Any], dotted: str) -> Any:
    cur: Any = metrics
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def scorecard(dataset: str, metrics: dict[str, Any], targets: dict[str, Any]) -> list[Check]:
    out: list[Check] = []
    for t in targets.get(dataset, []):
        value = _get(metrics, t["path"])
        target = _get(metrics, t["ref"]) if "ref" in t else t.get("value")
        passed = None
        if isinstance(value, (int, float)) and isinstance(target, (int, float)):
            passed = bool(OPS[t["op"]](value, target))
        out.append(Check(label=t["label"], value=value, op=t["op"], target=target,
                         blocking=bool(t.get("blocking")), passed=passed))
    return out


def blocking_failures(checks: list[Check]) -> list[Check]:
    """Chỉ tiêu bắt buộc không đạt – hoặc chưa đo được (an toàn: coi như không đạt)."""
    return [c for c in checks if c.blocking and c.passed is not True]


def fmt(v: float | None) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v <= 1.0:
        return f"{v:.1%}"
    return str(v)
