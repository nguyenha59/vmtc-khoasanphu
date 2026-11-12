"""Dựng bộ dataset chuẩn (benchmark) cố định, có phiên bản – CHỈ dữ liệu giả.

python scripts/build_benchmark.py            # dựng tests/benchmark/v1 (từ chối ghi đè nếu đã có)
python scripts/build_benchmark.py --force    # dựng lại (chỉ khi cố ý đổi phiên bản dữ liệu)

Hai tầng:
  quick – 200 ghi chú + 300 form (100 xanh / 100 vàng / 100 đỏ) + red-team đầy đủ; ~30 phút Ollama.
  full  – 600 ghi chú + 2.000 form + red-team đầy đủ; chạy trước khi chốt phiên bản.
Mốc ngày và seed cố định → dựng lại cho ra đúng từng byte (manifest.json lưu SHA-256 để kiểm tra).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
BENCH_ROOT = ROOT / "tests" / "benchmark"

VERSION = "v2"  # v2: đáp án giờ đến theo rules 0.8.0 (XN 30', SA 20', khám 15') – 12/11/2026
GEN_ARGS = ["--anchor", "2026-11-10", "--seed", "42", "--days", "90", "--per-day", "110", "--forms", "2000"]
SAMPLE_SEED = 20261110
QUICK_NOTES = 200
QUICK_FORMS_PER_LEVEL = 100


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _counts(s: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in s.value_counts().sort_index().items()}


def build(force: bool = False) -> Path:
    out = BENCH_ROOT / VERSION
    if out.exists() and not force:
        raise SystemExit(f"{out} đã có. Benchmark là cố định – dùng --force nếu cố ý dựng lại.")
    sys.path.insert(0, str(ROOT))
    from core.config import load_rules

    rules = load_rules()
    fewshot_path = ROOT / "config" / "prompts" / "note_extractor_fewshot.yaml"
    fewshot = {e["note"] for e in (yaml.safe_load(fewshot_path.read_text(encoding="utf-8")) or {})["examples"]}

    with tempfile.TemporaryDirectory(prefix="bench_") as tmp:
        syn, gold = Path(tmp) / "synthetic", Path(tmp) / "golden"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_synthetic.py"), *GEN_ARGS,
                        "--out", str(syn), "--golden", str(gold)], check=True, cwd=ROOT,
                       stdout=subprocess.DEVNULL)

        # ---------------- ghi chú: bỏ mọi ghi chú trùng ví dụ few-shot (chống rò rỉ)
        notes = pd.read_csv(gold / "notes_eval.csv", dtype=str, keep_default_na=False)
        notes = notes[~notes["note"].isin(fewshot)].reset_index(drop=True)
        strata = notes["status_rule"] + "|" + (notes["note"] != "").astype(str)
        quick_idx = (notes.groupby(strata, group_keys=False)
                     .apply(lambda g: g.sample(frac=QUICK_NOTES / len(notes), random_state=SAMPLE_SEED))
                     .index)
        notes_quick = notes.loc[sorted(quick_idx)].head(QUICK_NOTES)

        # ---------------- form: câu trả lời (định dạng CSV Microsoft Forms) + đáp án
        resp = pd.read_csv(syn / "form_responses.csv", dtype=str, keep_default_na=False)
        labels = pd.read_csv(gold / "form_expected.csv", dtype=str, keep_default_na=False)
        quick_tokens: list[str] = []
        for lvl in ("green", "yellow", "red"):
            sub = labels[labels["expected_level"] == lvl]
            quick_tokens += sub.sample(min(QUICK_FORMS_PER_LEVEL, len(sub)), random_state=SAMPLE_SEED)["token"].tolist()
        resp_quick = resp[resp["token"].isin(quick_tokens)]
        labels_quick = labels[labels["token"].isin(quick_tokens)]

        # ---------------- red-team: 58 ca gốc + ca bổ sung, bỏ trùng
        base = yaml.safe_load((ROOT / "tests" / "redteam" / "form_cases.yaml").read_text(encoding="utf-8"))["cases"]
        extra = yaml.safe_load((BENCH_ROOT / "sources" / "redteam_extra_v1.yaml").read_text(encoding="utf-8"))["cases"]
        seen, redteam = set(), []
        for c in base + extra:
            key = (c["text"], c["ga_weeks"])
            if key not in seen:
                seen.add(key)
                redteam.append(c)

        if out.exists():
            shutil.rmtree(out)
        (out / "quick").mkdir(parents=True)
        (out / "full").mkdir(parents=True)
        files = {
            "full/notes.csv": notes, "quick/notes.csv": notes_quick,
            "full/form_responses.csv": resp, "full/form_labels.csv": labels,
            "quick/form_responses.csv": resp_quick, "quick/form_labels.csv": labels_quick,
        }
        for rel, df in files.items():
            df.to_csv(out / rel, index=False, encoding="utf-8", lineterminator="\n")
        rt_text = yaml.safe_dump({"cases": redteam}, allow_unicode=True, sort_keys=False)
        for tier in ("quick", "full"):
            (out / tier / "redteam.yaml").write_text(rt_text, encoding="utf-8", newline="\n")

    rt = pd.DataFrame(redteam)
    manifest = {
        "benchmark_version": VERSION,
        "data": "100% dữ liệu GIẢ sinh bởi scripts/gen_synthetic.py – không có dữ liệu thật",
        "generator_args": GEN_ARGS,
        "sample_seed": SAMPLE_SEED,
        "rules_version_at_build": rules.rules_version,
        "fewshot_sha256": sha256(fewshot_path),
        "tiers": {
            tier: {
                "notes": {"n": len(n), "with_note": int((n["note"] != "").sum()), "status_rule": _counts(n["status_rule"])},
                "forms": {"n": len(lab), "expected_level": _counts(lab["expected_level"]),
                          "form_code": _counts(lab["form_code"]), "group": _counts(lab["group"]),
                          "with_free_text": int((r["free_text"] != "").sum())},
                "redteam": {"n": len(rt), "expected_level": _counts(rt["expected_level"]), "group": _counts(rt["group"])},
            }
            for tier, n, lab, r in (("quick", notes_quick, labels_quick, resp_quick), ("full", notes, labels, resp))
        },
        "sha256": {p.relative_to(out).as_posix(): sha256(p) for p in sorted(out.rglob("*")) if p.is_file()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return out


def verify(version: str = VERSION) -> dict:
    """Kiểm tra benchmark không bị sửa: so SHA-256 với manifest. Trả manifest nếu khớp."""
    out = BENCH_ROOT / version
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    bad = [rel for rel, h in manifest["sha256"].items() if not (out / rel).exists() or sha256(out / rel) != h]
    if bad:
        raise SystemExit(f"Benchmark {version} bị thay đổi ({', '.join(bad)}) – không dùng để đo.")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    out = build(force=args.force)
    m = verify()
    for tier, d in m["tiers"].items():
        print(f"[{tier}] ghi chú {d['notes']['n']} · form {d['forms']['n']} {d['forms']['expected_level']} · "
              f"red-team {d['redteam']['n']} {d['redteam']['expected_level']}")
    print(f"Đã dựng {out} (đã kiểm tra SHA-256).")


if __name__ == "__main__":
    main()
