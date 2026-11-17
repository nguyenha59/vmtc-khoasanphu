"""Bước 2 – phiếu AI: lọc (mã khái niệm hợp lệ, trích dẫn có thật), gộp đa số, độ nhất quán giữa các lần chạy."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core.ai.reader.concerns import covered_brushing_concern, valid_concern, vote_unmapped
from core.ai.reader.lexicon import effective, finding_level, safety_key
from core.ai.reader.text import clauses, evidence_in, same_mention, tokens_plain
from core.config import Rules
from core.levels import Level
from core.textutil import deaccent, nfc

# Từ được phép chen giữa từ phủ định và triệu chứng: "không BỊ ra máu", "chưa THẤY đạp", "không CÒN ra nước".
NEG_BRIDGE = {"bị", "có", "thấy", "còn", "hề", "bi", "co", "thay", "con", "he"}


@dataclass
class VoteResult:
    """Kết quả các lần chạy AI sau khi lọc."""

    runs: list[dict[str, dict[str, Any]]] = field(default_factory=list)  # mỗi lần chạy: khái niệm → bản an toàn nhất
    unmapped: list[str] = field(default_factory=list)
    ok: bool = False
    confidence: float = 0.0
    n_total: int = 0
    rejected: int = 0
    repairs: list[dict[str, Any]] = field(default_factory=list)
    covered_concerns: list[dict[str, Any]] = field(default_factory=list)


def repair_shared_negation(f: dict, text: str, lex: dict | None, rules: Rules) -> dict | None:
    """Khôi phục nguyên mệnh đề nguồn, chỉ khi AI lặp từ phủ định dùng chung ("không sốt và [không] ra máu").

    Không sửa một khẳng định dương tính, không suy diễn trên các câu khác nhau và không
    chấp nhận bằng chứng bịa. Mọi phiếu gốc vẫn được lưu nguyên vẹn trong raw.outputs.
    """
    if (lex is None or f["present"] or lex.get("assertion_status") != "negated"
            or rules["symptom_concepts"][f["concept"]].get("locked")):
        return None
    start, end = lex.get("evidence_start"), lex.get("evidence_end")
    if start is None or end is None:
        return None
    mention = text[start:end]
    quoted = " ".join(nfc(f.get("evidence", "")).lower().split())
    mention_words = " ".join(mention.lower().split())
    if not quoted.endswith(" " + mention_words):
        return None
    prefix = quoted[: -len(mention_words)].split()
    if (not prefix or prefix[0] not in {"không", "khong", "ko", "k", "chưa", "chua"}
            or len(prefix) > 3 or any(word not in NEG_BRIDGE for word in prefix[1:])):
        return None
    for offset, clause in clauses(text):
        if offset <= start and end <= offset + len(clause):
            before = deaccent(clause[: start - offset]).lower()
            if len(clause.strip()) <= 120 and re.search(r"\b(?:va|hay|hoac)\b", before):
                return {**f, "evidence": clause.strip()}
    return None


def confirmed_inactive(f: dict, lex: dict | None, text: str, ga_weeks: int | None, rules: Rules) -> bool:
    """Hai cách ghi một sự kiện KHÔNG có hiệu lực chỉ tương đương khi danh mục xác nhận cùng chỗ."""
    return bool(lex is not None and not rules["symptom_concepts"][f["concept"]].get("locked")
                and not effective(f) and not effective(lex)
                and finding_level(lex, ga_weeks, rules) == Level.GREEN
                and evidence_in(f.get("evidence", ""), text) and same_mention(f, lex, text))


def vote_signature(findings: list[dict], concerns: list[str], *, lex: dict[str, dict], text: str,
                   ga_weeks: int | None, rules: Rules) -> tuple:
    facts = tuple(sorted((f["concept"], f["present"], f["temporality"], f["experiencer"], f["severity"])
                         for f in findings if not confirmed_inactive(f, lex.get(f["concept"]), text, ga_weeks, rules)))
    concerns_key = tuple(sorted({" ".join(tokens_plain(c)) for c in concerns}))
    return facts, concerns_key


def collect(outputs: list[dict[str, Any]], n_total: int, *, masked: str, text: str,
            masked_lex: dict[str, dict], ga_weeks: int | None, rules: Rules) -> VoteResult:
    """Lọc phiếu: mã khái niệm phải có trong danh mục, trích dẫn phải có trong văn bản đã che (hoặc sửa được do
    phủ định dùng chung). Độ tin cậy = tỷ lệ lần chạy có cùng tập sự kiện (bỏ qua khác biệt không có hiệu lực)."""
    res = VoteResult(n_total=n_total)
    grounded_runs: list[list[dict[str, Any]]] = []
    valid_concerns: list[list[str]] = []
    for run_index, output in enumerate(outputs, 1):
        kept = []
        for f in output["findings"]:
            if f["concept"] not in rules["symptom_concepts"]:
                res.rejected += 1
                continue
            if not evidence_in(f.get("evidence", ""), masked):
                repaired = repair_shared_negation(f, masked, masked_lex.get(f["concept"]), rules)
                if repaired is None:
                    res.rejected += 1
                    continue
                res.repairs.append({"run": run_index, "concept": f["concept"],
                                    "original": f["evidence"], "replacement": repaired["evidence"]})
                f = repaired
            kept.append(f)
        grounded_runs.append(kept)
        valid_concerns.append([u for u in output.get("unmapped_concerns", []) if valid_concern(u, text, rules)])
        res.covered_concerns += [{"run": run_index, "text": u, "reason": "covered_brushing_bleeding"}
                                 for u in output.get("unmapped_concerns", [])
                                 if covered_brushing_concern(u, text, rules)]
    res.unmapped = vote_unmapped(valid_concerns, text, rules)
    if outputs:
        res.ok = True
        sets = Counter(vote_signature(run, concerns, lex=masked_lex, text=masked, ga_weeks=ga_weeks, rules=rules)
                       for run, concerns in zip(grounded_runs, valid_concerns, strict=True))
        res.confidence = sets.most_common(1)[0][1] / n_total
    for run in grounded_runs:  # mỗi lần chạy: một bản cho mỗi khái niệm (bản an toàn nhất)
        per_run: dict[str, dict[str, Any]] = {}
        for f in run:
            f = {**f, "source": "llm"}
            prev = per_run.get(f["concept"])
            if prev is None or safety_key(f) > safety_key(prev):
                per_run[f["concept"]] = f
        res.runs.append(per_run)
    return res


def majority_merge(runs: list[dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Gộp các lần chạy: mỗi khái niệm theo đa số (có hiệu lực / không).

    Khái niệm có hiệu lực ở > n/2 lần chạy → lấy bản an toàn nhất trong các bản có hiệu lực.
    Ngược lại → lấy bản an toàn nhất trong các bản KHÔNG có hiệu lực (nếu có), để vẫn ghi nhận
    khái niệm đã được nhắc tới (phủ định / người khác / đã qua). Lần chạy thiểu số làm giảm độ tin cậy.
    """
    n = len(runs)
    out: dict[str, dict[str, Any]] = {}
    for concept in {c for r in runs for c in r}:
        versions = [r[concept] for r in runs if concept in r]
        eff = [f for f in versions if effective(f)]
        if len(eff) * 2 > n:
            out[concept] = max(eff, key=safety_key)
        else:
            non_eff = [f for f in versions if not effective(f)]
            if non_eff:
                out[concept] = max(non_eff, key=safety_key)
            # chỉ có thiểu số "có hiệu lực" và không ai phủ định → không ghi; độ tin cậy đã giảm
    return out
