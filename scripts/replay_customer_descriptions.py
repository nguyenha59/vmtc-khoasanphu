"""Re-run the assessment using saved provider JSON only: no API, Ollama or database calls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.ai.llm_client import LlmError, validate  # noqa: E402
from core.assess import assess_answers  # noqa: E402
from core.config import load_forms, load_rules, prompt_text  # noqa: E402
from scripts.eval_customer_descriptions import write_report  # noqa: E402


class SavedResponses:
    def __init__(self, case: dict, model: str):
        self.outputs = copy.deepcopy(case["raw"].get("outputs", []))
        self.providers = case["raw"].get("models", [])
        self.calls = 0
        self.name = f"replay:{model}"
        self.last_provider = None

    def generate_json(self, prompt, schema, temperature, seed):
        if self.calls >= len(self.outputs):
            raise LlmError("Thiếu phiếu JSON đã lưu; replay không gọi mô hình để bù")
        output = self.outputs[self.calls]
        self.last_provider = f"replay:{self.providers[self.calls]}" if self.providers else self.name
        self.calls += 1
        return validate(copy.deepcopy(output), schema)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "reports/customer-descriptions/openai.json")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/customer-descriptions/openai-replay.json")
    args = parser.parse_args()
    source_bytes = args.input.read_bytes()
    source = json.loads(source_bytes)
    if not source.get("synthetic"):
        raise SystemExit("Chỉ dùng bộ ca giả để đánh giá")
    gold = yaml.safe_load((ROOT / "tests/golden/customer_descriptions.yaml").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in gold["cases"]}
    rules, forms = load_rules(), load_forms()
    result = {"synthetic": True, "provider": "saved_response_replay", "model": f"replay:{source['model']}",
              "rules_version": rules.rules_version, "source_rules_version": source["rules_version"],
              "source_sha256": hashlib.sha256(source_bytes).hexdigest(), "source": args.input.name,
              "new_api_calls": 0, "api_retested": False, "prompt_retested": False,
              "evaluation_note": "Phát lại JSON OpenAI đã lưu để kiểm tra phần mềm; không gọi API mới. Prompt mới chưa được kiểm thử bằng lượt phát lại này.",
              "active_prompt_version": prompt_text("form_reader")[1],
              "planned_cases": len(source["cases"]), "status": "running", "cases": [], "summary": {}}
    for previous in source["cases"]:
        case = cases[previous["id"]]
        assert case["text"] == previous["text"] and case.get("answers", {}) == previous["answers"]
        assert case["expected"] == previous["expected"]
        client = SavedResponses(previous, source["model"])
        assessed = assess_answers("F5", case.get("answers", {}), case["text"], case.get("ga_weeks", 30),
                                  rules, forms, client=client, history={}, ner=False)
        assert client.calls == len(client.outputs), f"Số phiếu đã dùng khác lượt gốc: {case['id']}"
        row = {"id": case["id"], "group": case["group"], "text": case["text"],
               "answers": case.get("answers", {}), "expected": case["expected"],
               "actual": assessed.level.code, "before": previous["actual"],
               "needs_human": assessed.combined.needs_human,
               "passed": assessed.level.code == case["expected"] and
               (not case.get("needs_human") or assessed.combined.needs_human),
               "confidence": assessed.reading.confidence, "confidence_before": previous["confidence"],
               "reasons": assessed.reasons, "findings": [asdict(f) for f in assessed.reading.findings],
               "raw": assessed.reading.raw}
        result["cases"].append(row)
    rows = result["cases"]
    result["summary"] = {"total": len(rows), "passed": sum(row["passed"] for row in rows),
                         "missed_red": sum(row["expected"] == "red" and row["actual"] != "red" for row in rows),
                         "false_red": sum(row["expected"] != "red" and row["actual"] == "red" for row in rows),
                         "extra_yellow": sum(row["expected"] == "green" and row["actual"] == "yellow" for row in rows),
                         "model_errors": sum(len(row["raw"].get("errors", [])) for row in rows),
                         "models": dict(Counter(model for row in rows for model in row["raw"].get("models", []))),
                         "replayed_responses": sum(len(row["raw"].get("outputs", [])) for row in rows),
                         "new_api_calls": 0}
    result["status"] = "completed"
    write_report(result, args.out)
    assert hashlib.sha256(args.input.read_bytes()).hexdigest() == result["source_sha256"]
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    if any(not row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
