"""Chạy bộ kiểm thử AI, ghi bảng eval_run (docs/08 mục 4).

python scripts/run_eval.py --dataset notes
python scripts/run_eval.py --dataset forms     # form + red-team
Mặc định đo trên benchmark chuẩn tests/benchmark/v2/quick; --bench full trước khi chốt phiên bản.
python scripts/run_eval.py --dataset noshow
Thêm --provider ollama để chạy với mô hình thật (mặc định theo LLM_PROVIDER / rules.yaml).
So sánh với mô hình cloud (CHỈ dữ liệu giả, không bao giờ dùng trong app):
python scripts/run_eval.py --dataset notes --model gpt-oss:120b-cloud --cloud-eval
In bảng chỉ tiêu (config/eval_targets.yaml). Mã thoát 1 nếu có chỉ tiêu BẮT BUỘC không đạt
(ví dụ độ nhạy ca đỏ < 100%) → không cho chuyển phiên bản.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_env, load_rules  # noqa: E402


def run(
    dataset: str,
    limit: int | None = None,
    user_id: int | None = None,
    model_override: str | None = None,
    cloud_eval: bool = False,
    bench: str | None = "quick",
    bench_version: str = "v2",
) -> dict:
    from core import audit
    from core.ai.evaluate import bench_dir, evaluate_forms, evaluate_notes, save_eval
    from core.ai.llm_client import OllamaClient, make_client
    from core.db import init_db, session_scope

    init_db()
    rules = load_rules()
    data_dir = None
    bench_info = None
    if bench and dataset in ("notes", "forms"):
        from scripts.build_benchmark import verify

        manifest = verify(bench_version)  # dừng nếu benchmark bị sửa
        data_dir = bench_dir(bench_version, bench)
        bench_info = {"version": bench_version, "tier": bench, "fewshot_sha256": manifest["fewshot_sha256"]}
    if dataset == "noshow":
        from core.ai.noshow import load_latest

        bundle = load_latest()
        if bundle is None:
            from scripts.train_noshow import run as train_run

            train_run(user_id)
            bundle = load_latest()
        assert bundle is not None
        metrics = {**bundle["metrics"], "model_version": bundle["version"]}
        model, prompt = f"lightgbm-{bundle['version']}", None
    else:
        if model_override or cloud_eval:
            # Bộ đánh giá chỉ đọc tests/golden, tests/redteam, data/synthetic – toàn bộ là dữ liệu GIẢ.
            client = OllamaClient(model=model_override or rules["llm"]["model"],
                                  timeout=rules["llm"].get("timeout_seconds", 120),
                                  allow_cloud_eval=cloud_eval)
        else:
            client = make_client(rules)
        if isinstance(client, OllamaClient) and not client.available():
            raise SystemExit(f"Ollama chưa sẵn sàng hoặc chưa có mô hình {client.model}.")
        if dataset == "notes":
            metrics, prompt = evaluate_notes(client, rules, limit=limit, data_dir=data_dir), "note_extractor"
        elif dataset == "forms":
            metrics, prompt = evaluate_forms(client, rules, limit=limit, data_dir=data_dir), "form_reader"
        else:
            raise SystemExit(f"dataset không hợp lệ: {dataset}")
        model = client.name
    from core.ai.scorecard import load_targets, scorecard

    targets = load_targets()
    metrics["scorecard"] = [c.to_dict() for c in scorecard(dataset, metrics, targets)]
    metrics["targets_version"] = targets.get("targets_version")
    metrics["limit"] = limit
    metrics["benchmark"] = bench_info
    with session_scope() as s:
        run_row = save_eval(s, dataset, metrics, rules, model, prompt)
        audit.log("run_eval", "eval_run", run_row.id, {"dataset": dataset, "model": model}, user_id=user_id, session=s)
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, choices=["notes", "forms", "noshow"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--bench", choices=["quick", "full", "none"], default="quick",
                    help="tầng benchmark chuẩn tests/benchmark/<ver>/ (mặc định quick); none = dữ liệu sinh gần nhất")
    ap.add_argument("--bench-version", default="v2")
    ap.add_argument("--provider", choices=["mock", "ollama", "openai", "hybrid"], default=None)
    ap.add_argument("--model", default=None, help="tên mô hình Ollama thay cho rules.yaml (chỉ để so sánh)")
    ap.add_argument("--cloud-eval", action="store_true",
                    help="cho phép mô hình cloud của Ollama – CHỈ với bộ dữ liệu giả của run_eval")
    args = ap.parse_args()
    load_env()
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.cloud_eval:
        print("⚠️  Chế độ so sánh cloud: chỉ chạy trên dữ liệu GIẢ. App vẫn chặn mô hình cloud.")
    m = run(args.dataset, args.limit, model_override=args.model, cloud_eval=args.cloud_eval,
            bench=None if args.bench == "none" else args.bench, bench_version=args.bench_version)
    m.pop("calibration", None)
    card = m.pop("scorecard", [])
    # Bản đầy đủ (kèm từng ca báo nhầm – dữ liệu giả) ghi ra file; màn hình chỉ in số liệu.
    out_dir = ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"last_{args.dataset}.json"
    report.write_text(json.dumps(m, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    short = json.loads(json.dumps(m, default=str))
    n_over = len(short.pop("over_triage_cases", []) or [])
    if isinstance(short.get("redteam"), dict):
        n_over += len(short["redteam"].pop("over_triage_cases", []) or [])
    print(json.dumps(short, ensure_ascii=False, indent=2)[:6000])
    if n_over:
        print(f"\n{n_over} ca báo nhầm (nâng mức) – chi tiết trong {report.relative_to(ROOT)}")
    code = print_scorecard(args.dataset, card, m.get("targets_version"))
    if os.environ.get("LLM_PROVIDER") == "mock" and args.dataset != "noshow":
        print("⚠️  Chạy bằng MOCK: chỉ kiểm tra đường ống (mock trả đáp án từ tests/golden hoặc danh mục),"
              " KHÔNG đo chất lượng AI. Ca cần AI thật (mối lo ngoài danh mục, cách nói lạ) sẽ sai ở mock."
              " Chốt phiên bản phải chạy --provider ollama.")
        code = 0
    sys.exit(code)


def print_scorecard(dataset: str, card: list[dict], version: str | None) -> int:
    """In bảng chỉ tiêu; trả mã thoát 1 nếu có chỉ tiêu bắt buộc không đạt."""
    from core.ai.scorecard import Check, blocking_failures, fmt

    checks = [Check(**c) for c in card]
    print()
    print(f"=== BẢNG CHỈ TIÊU – {dataset} (chỉ tiêu v{version}) ===")
    for c in checks:
        mark = "ĐẠT      " if c.passed else ("CHƯA ĐO  " if c.passed is None else "KHÔNG ĐẠT")
        tag = " [bắt buộc]" if c.blocking else ""
        print(f"  {mark} {c.label}: {fmt(c.value)} (cần {c.op} {fmt(c.target)}){tag}")
    fails = blocking_failures(checks)
    if fails:
        print("❌ Có chỉ tiêu BẮT BUỘC không đạt – KHÔNG chuyển phiên bản.")
        return 1
    soft = [c for c in checks if c.passed is False]
    print("✅ Đạt mọi chỉ tiêu bắt buộc." + (f" Còn {len(soft)} chỉ tiêu cần cải thiện." if soft else " Đạt toàn bộ."))
    return 0


if __name__ == "__main__":
    main()
