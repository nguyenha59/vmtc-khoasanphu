"""Self-consistency: gọi LLM nhiều lần, lấy đa số từng trường."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.ai.llm_client import LlmClient, LlmError, result_provider


@dataclass
class Votes:
    outputs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.outputs) + len(self.errors)


def _key(v: Any) -> str:
    return json.dumps(sorted(v) if isinstance(v, list) else v, ensure_ascii=False, sort_keys=True)


def run_votes(
    client: LlmClient, prompt: str, schema: dict[str, Any], runs: int, temperature: float
) -> Votes:
    votes = Votes()
    for i in range(runs):
        try:
            votes.outputs.append(
                client.generate_json(prompt, schema, temperature=temperature, seed=1000 + i)
            )
            votes.models.append(result_provider(client))
        except LlmError as e:
            votes.errors.append(str(e))
    return votes


def majority(outputs: Sequence[dict[str, Any]], fields: Sequence[str]) -> dict[str, Any]:
    """Giá trị đa số cho từng trường."""
    result: dict[str, Any] = {}
    for f in fields:
        counter = Counter(_key(o.get(f)) for o in outputs)
        if counter:
            winner = counter.most_common(1)[0][0]
            result[f] = json.loads(winner)
    return result


def agreement(outputs: Sequence[dict[str, Any]], fields: Sequence[str], n_total: int) -> float:
    """Tỷ lệ lần gọi trùng giá trị đa số của bộ trường đã chọn (lần lỗi tính là không trùng)."""
    if n_total == 0 or not outputs:
        return 0.0
    counter = Counter(tuple(_key(o.get(f)) for f in fields) for o in outputs)
    return counter.most_common(1)[0][1] / n_total
