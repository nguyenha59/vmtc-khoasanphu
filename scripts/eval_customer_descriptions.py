"""Đánh giá mô tả khách giả qua luồng phân mức thật, không ghi vào CSDL.

Mặc định dùng mock để kiểm tra chính sách; --provider ollama/openai kiểm tra mô hình thật.
OpenAI đọc key từ .env, qua kiểm tra dữ liệu giả và production, không dự phòng Ollama.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.ai.llm_client import MockClient, OllamaClient, make_client_for, openai_spent  # noqa: E402
from core.assess import assess_answers  # noqa: E402
from core.config import TZ, load_env, load_forms, load_rules, ollama_model, prompt_text  # noqa: E402


def write_report(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    esc = lambda value: html.escape(str(value))  # noqa: E731
    rows = []
    for row in result["cases"]:
        rows.append(f'<tr class="{"pass" if row["passed"] else "fail"}"><td>{esc(row["id"])}</td>'
                    f'<td>{esc(row["text"]) or esc(row["answers"])}</td><td>{esc(row["expected"])}</td>'
                    f'<td>{esc(row["actual"])}</td><td>{"Có" if row["needs_human"] else "Không"}</td>'
                    f'<td>{esc("; ".join(row["reasons"]))}</td></tr>')
    summary = result["summary"]
    document = ('<!doctype html><html lang="vi"><meta charset="utf-8"><title>Kiểm thử mô tả khách</title>'
                '<style>body{font:16px system-ui;margin:32px;color:#142133}table{border-collapse:collapse;width:100%}'
                'td,th{padding:10px;text-align:left;border:1px solid #cad3de;vertical-align:top}'
                '.fail{background:#ffe3e3}.pass{background:#f1faf5}code{overflow-wrap:anywhere}</style>'
                '<h1>Kiểm thử mô tả khách</h1><p>Dữ liệu giả; so với chính sách đang cấu hình. '
                'Chưa thay thế bộ ca do bác sĩ duyệt.</p>'
                f'<p>Mô hình: <code>{esc(result["model"])}</code>; quy tắc: {esc(result["rules_version"])}</p>'
                f'<p>{esc(result.get("evaluation_note", ""))}</p>'
                f'<p>Trạng thái: {esc(result.get("status", "completed"))}; '
                f'{len(result["cases"])}/{result.get("planned_cases", len(result["cases"]))} ca.</p>'
                f'<p>Khớp mức: {summary["passed"]}/{summary["total"]}; bỏ sót mức đỏ: {summary["missed_red"]}; '
                f'đỏ quá mức: {summary["false_red"]}; lỗi gọi mô hình: {summary["model_errors"]}.</p>'
                '<table><thead><tr><th>Mã</th><th>Mô tả / câu trả lời</th><th>Kỳ vọng</th>'
                '<th>Thực tế</th><th>Cần người xem</th><th>Lý do</th></tr></thead><tbody>'
                + ''.join(rows) + '</tbody></table></html>')
    path.with_suffix(".html").write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "ollama", "openai"), default="mock")
    parser.add_argument("--model", help="Tên mô hình Ollama cục bộ, ví dụ qwen2.5:7b-instruct")
    parser.add_argument("--ids", help="Các mã ca cách nhau bằng dấu phẩy; bỏ trống = toàn bộ")
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "customer-descriptions" / "policy.json")
    args = parser.parse_args()
    if args.provider == "openai":
        load_env()
    os.environ.setdefault("APP_JOBS", "0")
    rules, forms = load_rules(), load_forms()
    cases = yaml.safe_load((ROOT / "tests/golden/customer_descriptions.yaml").read_text(encoding="utf-8"))["cases"]
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [case for case in cases if case["id"] in wanted]
        if {case["id"] for case in cases} != wanted:
            raise SystemExit("Có mã ca không tồn tại")
    if args.provider == "openai":
        if args.model:
            os.environ["OPENAI_MODEL"] = args.model
        client = make_client_for(rules, "openai")  # Không đi qua make_client / hybrid.
    elif args.provider == "ollama":
        client = OllamaClient(args.model or ollama_model(rules), timeout=rules.get("llm.timeout_seconds", 120))
    else:
        client = MockClient()
    if args.provider == "ollama" and not client.available():
        raise SystemExit(f"Chưa sẵn sàng: {client.name}. Cần ollama pull trước khi kiểm thử mô hình thật.")
    spent_before = openai_spent() if args.provider == "openai" else None
    result = {"at": datetime.now(TZ).isoformat(), "synthetic": True, "provider": args.provider,
              "model": client.name, "fallback_enabled": False,
              "rules_version": rules.rules_version, "prompt_version": prompt_text("form_reader")[1],
              "planned_cases": len(cases), "status": "running",
              "cases": [], "summary": {}}
    for case in cases:
        assessed = assess_answers("F5", case.get("answers", {}), case["text"], case.get("ga_weeks", 30),
                                  rules, forms, client=client, history={}, ner=False)
        row = {"id": case["id"], "group": case["group"], "text": case["text"],
               "answers": case.get("answers", {}), "expected": case["expected"],
               "actual": assessed.level.code, "needs_human": assessed.combined.needs_human,
               "passed": assessed.level.code == case["expected"] and
               (not case.get("needs_human") or assessed.combined.needs_human),
               "confidence": assessed.reading.confidence, "reasons": assessed.reasons,
               "findings": [asdict(f) for f in assessed.reading.findings], "raw": assessed.reading.raw}
        result["cases"].append(row)
        rows = result["cases"]
        result["summary"] = {"total": len(rows), "passed": sum(r["passed"] for r in rows),
                             "missed_red": sum(r["expected"] == "red" and r["actual"] != "red" for r in rows),
                             "false_red": sum(r["expected"] != "red" and r["actual"] == "red" for r in rows),
                             "extra_yellow": sum(r["expected"] == "green" and r["actual"] == "yellow" for r in rows),
                             "model_errors": sum(len(r["raw"].get("errors", [])) for r in rows),
                             "actual_levels": dict(Counter(r["actual"] for r in rows)),
                             "models": dict(Counter(m for r in rows for m in r["raw"].get("models", [])))}
        if spent_before is not None:
            result["summary"]["openai_spend_usd"] = round(openai_spent() - spent_before, 8)
            result["summary"]["daily_budget_usd"] = client.budget
        if args.provider == "openai" and row["raw"].get("errors"):
            result["status"] = "stopped_api_error"
        write_report(result, args.out)
        print(json.dumps({"id": row["id"], "expected": row["expected"], "actual": row["actual"],
                          "passed": row["passed"]}, ensure_ascii=False), flush=True)
        if args.provider == "openai" and row["raw"].get("errors"):
            raise SystemExit("OpenAI có lỗi: đã lưu kết quả từng phần; dừng lượt kiểm thử, không chuyển Ollama.")
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    result["status"] = "completed"
    write_report(result, args.out)
    if result["summary"]["passed"] != result["summary"]["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
