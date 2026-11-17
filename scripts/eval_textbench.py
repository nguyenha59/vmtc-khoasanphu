"""Đo phần tự viết trên bộ câu viết tay (tests/benchmark/<bộ>/cases.yaml hoặc redteam.yaml) – dữ liệu GIẢ.

Báo theo NHÓM hiện tượng (cách nói lạ, không dấu, phủ định, người khác, đã qua…): khớp mức, độ nhạy ca đỏ,
hạ mức sai (bắt buộc = 0), báo nhầm. Mức = gộp như khi chấm form thật (đọc tự viết + kết hợp triệu chứng + combine).

    python scripts/eval_textbench.py --provider none            # chỉ luật, không AI
    python scripts/eval_textbench.py --provider ollama
    python scripts/eval_textbench.py --provider hybrid --bench indep_v1 ood_v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ai.combine import combine  # noqa: E402
from core.ai.form_reader import read_free_text  # noqa: E402
from core.ai.llm_client import LlmClient, make_client_safe  # noqa: E402
from core.assess import text_combo_findings  # noqa: E402
from core.config import Rules, load_env, load_rules  # noqa: E402
from core.levels import Level, max_level  # noqa: E402

RANK = {"green": 0, "yellow": 1, "red": 2}
CACHE = ROOT / "data" / "eval" / "llm_cache.jsonl"  # câu trả lời AI đã trả tiền – đo lại cùng prompt không tốn thêm


class CachingClient:
    """Bọc client thật: trả lại kết quả đã lưu cho cùng (mô hình, prompt, seed, nhiệt độ). CHỈ dùng khi đo (dữ liệu giả)."""

    def __init__(self, inner: LlmClient) -> None:
        self.inner, self.name = inner, inner.name
        self.last_provider: str | None = None
        self.hits = self.misses = 0
        self.store: dict[str, dict[str, Any]] = {}
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.store[row["key"]] = row
    def _key(self, prompt: str, temperature: float, seed: int | None) -> str:
        return hashlib.sha256(f"{self.name}|{temperature}|{seed}|{prompt}".encode()).hexdigest()

    def generate_json(self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None) -> dict[str, Any]:
        key = self._key(prompt, temperature, seed)
        if key in self.store:
            self.hits += 1
            self.last_provider = self.store[key]["provider"]
            return self.store[key]["output"]
        self.misses += 1
        out = self.inner.generate_json(prompt, schema, temperature, seed)
        from core.ai.llm_client import result_provider

        self.last_provider = result_provider(self.inner)
        row = {"key": key, "provider": self.last_provider, "output": out}
        self.store[key] = row
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return out

    def available(self) -> bool:
        return True


def load_cases(name: str) -> list[dict[str, Any]]:
    folder = ROOT / "tests" / "benchmark" / name
    path = folder / "cases.yaml" if (folder / "cases.yaml").exists() else folder / "redteam.yaml"
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]
    for i, c in enumerate(cases):
        c.setdefault("id", f"{name}-{i + 1}")
        c["bench"] = name
    return cases


def level_for(text: str, ga_weeks: int | None, client: LlmClient | None, rules: Rules) -> tuple[str, dict[str, Any]]:
    """Mức phần tự viết khi chấm form (không có câu trả lời có cấu trúc). Hàm thuần theo (văn bản, client, cấu hình)."""
    reading = read_free_text(text, ga_weeks, client, rules, ner=False)
    combos = text_combo_findings(reading, [], rules)
    rule_level = max_level(Level.GREEN, *(f.level for f in combos)) if combos else Level.GREEN
    c = combine(rule_level, Level.GREEN, reading.level, ai_ok=reading.ok, ai_confidence=reading.confidence,
                min_confidence=rules["arrival"]["ai_confidence_min"])
    detail = {"reading": reading.level.code, "confidence": round(reading.confidence, 2), "needs_human": c.needs_human,
              "findings": [(f.concept, f.level, f.experiencer, f.temporality, f.present, f.source) for f in reading.findings],
              "unmapped": reading.unmapped, "overrides": reading.raw.get("ai_overrides"),
              "red_checks": reading.raw.get("ai_red_checks")}
    return c.level.code, detail


def evaluate(cases: list[dict[str, Any]], client: LlmClient | None, rules: Rules) -> list[dict[str, Any]]:
    rows = []
    for c in cases:
        got, detail = level_for(c["text"], c.get("ga_weeks"), client, rules)
        rows.append({"id": c["id"], "bench": c["bench"], "group": c.get("group", "?"), "text": c["text"],
                     "expected": c["expected_level"], "got": got, **detail})
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def stats(rs: list[dict[str, Any]]) -> dict[str, Any]:
        reds = [r for r in rs if r["expected"] == "red"]
        return {"n": len(rs), "exact": sum(r["got"] == r["expected"] for r in rs),
                "red_sens": f"{sum(r['got'] == 'red' for r in reds)}/{len(reds)}",
                "under": sum(RANK[r["got"]] < RANK[r["expected"]] for r in rs),
                "over": sum(RANK[r["got"]] > RANK[r["expected"]] for r in rs)}

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[f"{r['bench']}/{r['group']}"].append(r)
    return {"total": stats(rows), "groups": {k: stats(v) for k, v in sorted(groups.items())}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", nargs="+", default=["indep_v1"])
    ap.add_argument("--provider", choices=["none", "mock", "ollama", "openai", "hybrid"], default="none")
    ap.add_argument("--groups", nargs="*", default=None, help="chỉ đo các nhóm này (tiết kiệm ngân sách API)")
    ap.add_argument("--ids", nargs="*", default=None, help="chỉ đo các câu có id này")
    ap.add_argument("--keep-red", action="store_true",
                    help="bật form_cap.ai_only_red_keep (giữ Đỏ khi AI + bộ đọc theo nghĩa cùng xác nhận) – chỉ khi đo")
    ap.add_argument("--no-cache", action="store_true", help="không dùng lại câu trả lời AI đã lưu")
    args = ap.parse_args()
    load_env()
    rules = load_rules()
    client = None
    if args.provider != "none":
        os.environ["LLM_PROVIDER"] = args.provider
        client, why = make_client_safe(rules)
        if client is None:
            sys.exit(f"Không tạo được client {args.provider}: {why}")
        if not args.no_cache and args.provider != "mock":
            client = CachingClient(client)
    if args.keep_red:
        rules.data["form_cap"]["ai_only_red_keep"]["enabled"] = True
    cases = [c for b in args.bench for c in load_cases(b)]
    if args.groups:
        cases = [c for c in cases if c.get("group") in set(args.groups)]
    if args.ids:
        cases = [c for c in cases if c.get("id") in set(args.ids)]
    print(f"{len(cases)} câu")
    rows = evaluate(cases, client, rules)
    summary = summarize(rows)
    name = getattr(client, "name", "none")
    print(f"rules_version {rules.rules_version} · client {name} · giữ đỏ {'BẬT' if args.keep_red else 'tắt'}")
    if isinstance(client, CachingClient):
        print(f"AI: {client.misses} lần gọi mới, {client.hits} lần lấy lại từ bộ đệm")
    t = summary["total"]
    print(f"TỔNG: khớp {t['exact']}/{t['n']} · độ nhạy đỏ {t['red_sens']} · hạ mức sai {t['under']} · báo nhầm {t['over']}")
    for g, s in summary["groups"].items():
        print(f"  {g:32} khớp {s['exact']:>3}/{s['n']:<3} đỏ {s['red_sens']:>6}  hạ sai {s['under']:>2}  báo nhầm {s['over']:>2}")
    for r in rows:
        if RANK[r["got"]] < RANK[r["expected"]]:
            print(f"  HẠ SAI [{r['id']}] {r['text']!r}: mong {r['expected']} → {r['got']}")
    suffix = (f"_{'+'.join(args.groups)}" if args.groups else "") + ("_keepred" if args.keep_red else "")
    out = ROOT / "data" / "eval" / f"textbench_{'+'.join(args.bench)}_{args.provider}{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rules_version": rules.rules_version, "client": name, "summary": summary, "rows": rows},
                              ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Chi tiết: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
