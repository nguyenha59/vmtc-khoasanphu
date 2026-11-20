"""Đo độ chính xác đọc ghi chú kiểu OH: loại khám, dịch vụ, giờ đến, Cần xác nhận (tests/golden/oh_notes.yaml).

Chạy:  python scripts/eval_oh_notes.py            # quy tắc + AI mock (không tốn tiền)
       python scripts/eval_oh_notes.py --ai       # AI theo LLM_PROVIDER (ollama / openai với dữ liệu giả)
Một ca ĐÚNG khi: đúng loại khám, đủ dịch vụ bắt buộc, đúng trạng thái (Cần xác nhận hay tự động) và – nếu tự động –
đúng giờ đến. Ca đáp án cần xác nhận thì giờ đến do điều dưỡng chốt, không tính.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_env, load_rules  # noqa: E402
from core.pipeline import plan_appointment  # noqa: E402
from core.rules.merge import MergedAppt, Slot  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "oh_notes.yaml"


def _t(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def run(client: Any | None, golden: Path = GOLDEN) -> dict[str, Any]:
    rules = load_rules()
    cases = yaml.safe_load(golden.read_text(encoding="utf-8"))["cases"]
    rows: list[dict[str, Any]] = []
    for c in cases:
        start = _t(c["appt"])
        m = MergedAppt(
            pid=f"SYN{c['id']:04d}",
            appt_date=date(2026, 11, 17),
            start_time=start,
            end_time=None,
            services=[],
            note=c["note"],
            full_name="Khách Giả",
            phone="0901000000",
            phone_raw="",
            nationality="Việt Nam",
            department="",
            doctor="",
            booking_source="",
            booked_at=None,
            slots=[Slot(start, "", c["note"])],
        )
        p = plan_appointment(m, rules, client, [], ner=False)
        sp = p.service_plans[0] if p.service_plans else None
        got_visit = sp.visit_type if sp else None
        got_services = set(sp.codes) if sp else set()
        need = bool(c.get("review"))
        flagged = p.decision.status == "needs_review"
        arrival = p.arrival.arrival_time.strftime("%H:%M") if p.arrival.arrival_time else None
        ok_visit = got_visit == c["visit"]
        ok_services = set(c["services"]) <= got_services
        ok_status = flagged == need
        ok_arrival = need or arrival == c["arrival"] or (arrival is None and c["arrival"] == "Khám chờ")
        rows.append(
            {
                "id": c["id"],
                "note": c["note"],
                "visit": got_visit,
                "want_visit": c["visit"],
                "missing": sorted(set(c["services"]) - got_services),
                "arrival": arrival,
                "want_arrival": c["arrival"],
                "flagged": flagged,
                "want_review": need,
                "reasons": p.decision.reasons,
                "ok": ok_visit and ok_services and ok_status and ok_arrival,
                "ok_visit": ok_visit,
                "ok_services": ok_services,
                "ok_status": ok_status,
                "ok_arrival": ok_arrival,
            }
        )
    n = len(rows)
    return {
        "n": n,
        "case_accuracy": sum(r["ok"] for r in rows) / n,
        "visit_accuracy": sum(r["ok_visit"] for r in rows) / n,
        "services_recall": sum(r["ok_services"] for r in rows) / n,
        "status_accuracy": sum(r["ok_status"] for r in rows) / n,
        "arrival_accuracy_auto": (
            sum(r["ok_arrival"] for r in rows if not r["want_review"])
            / max(1, sum(not r["want_review"] for r in rows))
        ),
        "review_recall": (
            sum(r["flagged"] for r in rows if r["want_review"])
            / max(1, sum(r["want_review"] for r in rows))
        ),
        "errors": [r for r in rows if not r["ok"]],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true", help="Dùng AI theo LLM_PROVIDER thay cho mock")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    load_env()
    from core.ai.llm_client import MockClient, make_client

    client = make_client(load_rules()) if args.ai else MockClient()
    res = run(client)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        return
    print(f"Số ca: {res['n']}")
    for k in (
        "case_accuracy",
        "visit_accuracy",
        "services_recall",
        "status_accuracy",
        "arrival_accuracy_auto",
        "review_recall",
    ):
        print(f"  {k:24} {res[k]:.1%}")
    for e in res["errors"]:
        print(
            f"  ✗ #{e['id']} {e['note']!r}: loại {e['visit']} (đúng {e['want_visit']}), thiếu {e['missing']}, "
            f"giờ {e['arrival']} (đúng {e['want_arrival']}), cần xác nhận {e['flagged']} (đúng {e['want_review']}) "
            f"{e['reasons']}"
        )


if __name__ == "__main__":
    main()
