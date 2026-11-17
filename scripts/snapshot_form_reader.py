"""Ảnh chụp hành vi phần đọc tự viết (form_reader) – khóa hành vi khi tái cấu trúc. Không gọi AI thật.

Mỗi câu trong các bộ đo (dữ liệu GIẢ) × 12 kịch bản AI giả (đồng ý, người khác, phủ định, đã qua, nặng, chia phiếu,
trích dẫn bịa, chỉ AI thấy, mối lo lạ, AI lỗi, trả rỗng, không có AI) → chữ ký kết quả (mức, độ tin cậy, phát hiện,
mâu thuẫn, các ghi chú raw). Test `tests/test_form_reader_behavior.py` so với ảnh đã lưu.

    python scripts/snapshot_form_reader.py --write     # chỉ chạy khi CỐ Ý đổi hành vi (ghi lý do vào commit)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ai.form_reader import FormReading, lexicon_findings, read_free_text  # noqa: E402
from core.ai.llm_client import LlmError, MockClient  # noqa: E402
from core.config import Rules, load_rules  # noqa: E402

SNAPSHOT = ROOT / "tests" / "snapshots" / "form_reader_behavior.json"
RAW_KEYS = ("ai_overrides", "evidence_repairs", "severity_adjudications", "unresolved_text_concerns",
            "covered_concerns", "rejected_findings", "llm_status", "confidence_basis")


def corpus() -> list[tuple[str, int | None]]:
    """(văn bản, tuần thai) duy nhất từ mọi bộ đo phần tự viết."""
    seen: dict[str, int | None] = {}
    files = sorted((ROOT / "tests").glob("redteam/*.yaml")) + sorted((ROOT / "tests" / "benchmark").glob("**/redteam.yaml"))
    files += sorted((ROOT / "tests" / "benchmark").glob("**/cases.yaml"))
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for c in data.get("cases", []) if isinstance(data, dict) else data:
            if c.get("text"):
                seen.setdefault(str(c["text"]), c.get("ga_weeks"))
    for f in sorted((ROOT / "tests" / "benchmark").glob("**/form_responses.csv")):
        with open(f, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if (row.get("free_text") or "").strip():
                    seen.setdefault(row["free_text"].strip(), 30)
    return sorted(seen.items())


def _finding(concept: str, evidence: str, **kw: Any) -> dict[str, Any]:
    base = {"concept": concept, "present": True, "temporality": "current", "experiencer": "patient",
            "severity": "unknown", "evidence": evidence[:120]}
    return base | kw


def scenarios(text: str, rules: Rules) -> dict[str, MockClient | None]:
    lex = lexicon_findings(text, apply_assertions=False, rules=rules)
    first = lex[0] if lex else None
    concepts = {f["concept"] for f in lex}
    ai_only = next(c for c in ("DYSPNEA", "BLEEDING", "VISUAL") if c not in concepts)

    def fixed(findings: list[dict[str, Any]], unmapped: list[str] | None = None) -> MockClient:
        return MockClient(lambda p, s, seed: {"findings": findings, "unmapped_concerns": unmapped or []})

    def by_seed(fn: Callable[[int], list[dict[str, Any]]]) -> MockClient:
        return MockClient(lambda p, s, seed: {"findings": fn(seed), "unmapped_concerns": []})

    def boom(p: str, s: dict[str, Any], seed: int | None) -> dict[str, Any]:
        raise LlmError("mock lỗi")

    agree = [_finding(f["concept"], f["evidence"]) for f in lex]
    one = (lambda **kw: [_finding(first["concept"], first["evidence"], **kw)]) if first else (lambda **kw: [])
    return {
        "none": None,
        "empty": fixed([]),
        "error": MockClient(boom),
        "agree": fixed(agree),
        "other": fixed(one(experiencer="other")),
        "negated": fixed(one(present=False)),
        "past": fixed(one(temporality="past_resolved")),
        "severe": fixed(one(severity="severe")),
        "split": by_seed(lambda seed: one(experiencer="other") if seed == 1002 else one()),
        "halluc": fixed([_finding(first["concept"], "câu này không có trong văn bản")] if first else []),
        "ai_only": fixed([_finding(ai_only, text)]),
        "unmapped": fixed([], [text[:60]]),
    }


def signature(r: FormReading) -> dict[str, Any]:
    return {
        "level": r.level.code, "ok": r.ok, "confidence": round(r.confidence, 3),
        "findings": sorted([f.concept, f.level, f.present, f.temporality, f.experiencer, f.severity, f.source,
                            f.assertion_status, f.anatomy, f.evidence] for f in r.findings),
        "unmapped": r.unmapped, "conflicts": sorted(r.conflicts),
        "raw": {k: r.raw[k] for k in RAW_KEYS if k in r.raw},
    }


def compute(rules: Rules | None = None) -> dict[str, Any]:
    rules = rules or load_rules()
    out: dict[str, Any] = {}
    for text, ga in corpus():
        for name, client in scenarios(text, rules).items():
            r = read_free_text(text, ga, client, rules, ner=False)
            out[f"{name}␟{ga}␟{text}"] = signature(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="ghi ảnh chụp mới (cố ý đổi hành vi)")
    args = ap.parse_args()
    data = compute()
    if args.write:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text("{\n" + ",\n".join(f"{json.dumps(k, ensure_ascii=False)}: "
                                              f"{json.dumps(v, ensure_ascii=False, default=str)}"
                                              for k, v in sorted(data.items())) + "\n}\n", encoding="utf-8")
        print(f"Đã ghi {len(data)} kết quả → {SNAPSHOT.relative_to(ROOT)}")
    else:
        print(f"{len(data)} kết quả (thêm --write để ghi)")


if __name__ == "__main__":
    main()
