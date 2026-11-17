"""Xử lý một file OH (6 cột) → file Excel mới: file gốc + loại khám, dịch vụ, giờ đến, căn cứ, cần xác nhận.

Chạy: python scripts/process_oh_file.py <file_oh.xlsx> [--out <file_ra.xlsx>] [--ai]
  --ai: dùng AI theo LLM_PROVIDER (openai chỉ với dữ liệu giả: SYNTHETIC_DATA_ONLY=1). Không có → quy tắc + mock.
Ghi chú vẫn đi qua core/ai/masking.py trước khi gửi AI (như luồng tải file trên app).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_env, load_his_columns, load_rules  # noqa: E402
from core.ingest.his_reader import present_fields, read_raw, standardize  # noqa: E402
from core.pipeline import process_his  # noqa: E402
from core.rules.review import reason_label  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--out")
    ap.add_argument("--ai", action="store_true")
    args = ap.parse_args()
    load_env()
    rules, his = load_rules(), load_his_columns()
    src = Path(args.file)
    raw = read_raw(src)
    fields = present_fields(raw, his)
    # File mẫu có thể để trống HN: gán mã tạm theo dòng (không gộp nhầm nhiều khách vào một hồ sơ).
    pid_col = fields.get("pid")
    if pid_col is not None:
        blank = raw[pid_col].astype(str).str.strip().isin(["", "nan", "None"])
        raw.loc[blank, pid_col] = [f"DONG{i + 2}" for i in raw.index[blank]]
    rows = standardize(raw, his)
    days = sorted({r.appt_date for r in rows if r.appt_date})
    d: date | None = days[0] if len(days) == 1 else None

    from core.ai.llm_client import MockClient, make_client

    client = make_client(rules) if args.ai else MockClient()
    res = process_his(raw, his, rules, d, client=client, ner=False)
    if not res.ok:
        sys.exit("File lỗi: " + "; ".join(i["message"] for i in res.report.as_dicts() if i["fatal"]))

    out = raw.copy()
    cols = ["Loại khám", "Dịch vụ dự kiến", "Đến trước (phút)", "Giờ đến", "Cách tính / căn cứ", "Cần xác nhận",
            "Lý do cần xác nhận", "Nhịn ăn", "Model AI"]
    for c in cols:
        out[c] = ""
    for p in res.planned:
        a = p.arrival
        mins = ""
        if a.arrival_time is not None:
            t0 = p.merged.start_time
            mins = (t0.hour * 60 + t0.minute) - (a.arrival_time.hour * 60 + a.arrival_time.minute)
        reasons = [r for r in p.decision.reasons if r != "BAD_PHONE"]  # file mẫu không có SĐT
        ex = p.extraction
        for row_no in p.merged.merged_from:
            i = row_no - 2
            out.loc[i, "Loại khám"] = " · ".join(sp.visit_name for sp in p.service_plans)
            out.loc[i, "Dịch vụ dự kiến"] = ", ".join(p.service_names)
            out.loc[i, "Đến trước (phút)"] = str(mins)
            out.loc[i, "Giờ đến"] = (a.arrival_time.strftime("%H:%M") if a.arrival_time
                                     else ("Khám chờ" if a.waiting else ""))
            out.loc[i, "Cách tính / căn cứ"] = "\n".join([a.rule_applied, *a.details])
            out.loc[i, "Cần xác nhận"] = "Có" if reasons else ""
            out.loc[i, "Lý do cần xác nhận"] = "; ".join(reason_label(r, rules) for r in reasons)
            out.loc[i, "Nhịn ăn"] = "Có" if any(sp.fasting for sp in p.service_plans) else ""
            out.loc[i, "Model AI"] = f"{ex.model} · {ex.prompt_version}" if ex else ""
    dst = Path(args.out) if args.out else src.with_name(src.stem + "_gio_den.xlsx")
    with pd.ExcelWriter(dst) as w:
        out.to_excel(w, index=False, sheet_name="Giờ đến")
    print(f"Đã ghi {dst} – {len(res.planned)} lịch, cần xác nhận "
          f"{sum(1 for p in res.planned if [r for r in p.decision.reasons if r != 'BAD_PHONE'])}")


if __name__ == "__main__":
    main()
