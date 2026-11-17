"""So sánh mô hình embedding cục bộ cho bộ đọc theo nghĩa (core/ai/reader/semantic.py) – dữ liệu GIẢ.

Ngưỡng của từng mô hình chọn trên bộ ĐÃ LỘ (tests/redteam, v2 red-team, ood_v1): bắt nhiều ca đỏ nhất với điều kiện
"xác nhận nhầm chủ đề" ≤ --max-wrong (mỗi câu × mọi khái niệm đỏ KHÁC chủ đề của câu). Rồi kiểm trên bộ KÍN
(indep_v1, ood_v2) bằng đúng ngưỡng đó. Chỉ xét top-1 (khái niệm AI chọn phải giống nghĩa nhất).

    python scripts/compare_embedders.py --models bge-m3 qwen3-embedding:0.6b openai:text-embedding-3-small
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.ai.reader.semantic import OllamaEmbedder, OpenAIEmbedder, scores  # noqa: E402
from core.config import Rules, load_env, load_rules  # noqa: E402

EXPOSED = ["tests/redteam/form_cases.yaml", "tests/benchmark/v2/quick/redteam.yaml", "tests/benchmark/ood_v1/redteam.yaml"]
HELDOUT = ["tests/benchmark/indep_v1/cases.yaml", "tests/benchmark/ood_v2/redteam.yaml"]


def load(files: list[str], rules: Rules) -> list[tuple[str, str, bool]]:
    """(văn bản, khái niệm chủ đề, có phải ca đỏ)."""
    out, seen = [], set()
    for f in files:
        data = yaml.safe_load((ROOT / f).read_text(encoding="utf-8"))
        for c in data["cases"] if isinstance(data, dict) else data:
            concept = c.get("concept") or (c.get("expected_concepts") or [None])[0]
            if concept in rules["symptom_concepts"] and c["text"] not in seen:
                seen.add(c["text"])
                out.append((c["text"], concept, c.get("expected_level") == "red"))
    return out


def evaluate(cases, sc_by_text, red: list[str], th: float) -> tuple[int, int, int, int]:
    tp = n_pos = wrong = total = 0
    for text, concept, is_red in cases:
        sc = sc_by_text[text]
        top = max(sc, key=lambda c: sc[c])
        if is_red:
            n_pos += 1
            tp += sc[concept] >= th and top == concept
        for w in red:
            if w != concept:
                total += 1
                wrong += sc[w] >= th and top == w
    return tp, n_pos, wrong, total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--max-wrong", type=float, default=0.005)
    args = ap.parse_args()
    load_env()
    rules = load_rules()
    red = [c for c, v in rules["symptom_concepts"].items() if v["level"] == "red" and not v.get("locked")]
    exposed, heldout = load(EXPOSED, rules), load(HELDOUT, rules)
    print(f"bộ đã lộ {len(exposed)} câu · bộ kín {len(heldout)} câu · {len(red)} khái niệm đỏ")
    for model in args.models:
        # "openai:<model>" = OpenAI embeddings (CHỈ dữ liệu giả – bộ đo ở đây là dữ liệu GIẢ)
        emb = (OpenAIEmbedder(model.split(':', 1)[1]) if model.startswith("openai:")
               else OllamaEmbedder(model, timeout=600))
        sc = {t: scores(t, emb, rules) for t, _c, _r in exposed + heldout}
        best = None
        for i in range(10, 91):
            th = i / 100
            tp, n, wrong, total = evaluate(exposed, sc, red, th)
            if wrong / total <= args.max_wrong and (best is None or tp > best[1]):
                best = (th, tp, n, wrong, total)
        if best is None:
            print(f"{model:28} không có ngưỡng nào đạt nhầm ≤ {args.max_wrong:.1%}")
            continue
        th, tp, n, wrong, total = best
        htp, hn, hw, ht = evaluate(heldout, sc, red, th)
        print(f"{model:28} ngưỡng {th:.2f} | đã lộ: bắt {tp}/{n}, nhầm {wrong}/{total} "
              f"| KÍN: bắt {htp}/{hn}, nhầm {hw}/{ht} ({hw / ht:.1%})")


if __name__ == "__main__":
    main()
