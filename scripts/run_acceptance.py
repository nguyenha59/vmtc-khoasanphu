"""Kiểm thử chấp nhận docs/08 mục 5 – bước 1 (file ngày mai) và bước 4 (form R0412). CHỈ dữ liệu giả.

python scripts/run_acceptance.py               # mô hình theo LLM_PROVIDER (.env), thường là Ollama
Bước 4 dùng CSDL tạm, không ghi vào data/app.db. Bước 2, 3, 5, 6 có test tự động trong tests/.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="acc_"))
os.environ["APP_DB_URL"] = f"sqlite:///{(TMP / 'acc.db').as_posix()}"

from core.config import load_env, load_forms, load_his_columns, load_rules  # noqa: E402

load_env()
import pandas as pd  # noqa: E402

from core.ai.llm_client import make_client  # noqa: E402
from core.ingest.his_reader import read_raw  # noqa: E402
from core.pipeline import process_his  # noqa: E402
from core.rules.arrival import arrival_text  # noqa: E402

rules = load_rules()
client = make_client(rules)
print("rules_version", rules.rules_version, "| client", type(client).__name__, getattr(client, "model", ""))

# ---------- Bước 1
day = sorted((ROOT / "tests" / "golden").glob("expected_arrivals_*.csv"))[0].stem.split("_")[-1]  # ngày mai
raw = read_raw(ROOT / "data" / "synthetic" / f"his_export_{day}.xlsx")
t0 = time.time()
res = process_his(raw, load_his_columns(), rules, date.fromisoformat(day), client=client)
dt = time.time() - t0
exp = pd.read_csv(ROOT / "tests" / "golden" / f"expected_arrivals_{day}.csv", dtype=str,
                  keep_default_na=False).set_index("pid")
n = len(res.planned)
with_note = sum(1 for p in res.planned if p.merged.note)
ai_called = sum(1 for p in res.planned if p.extraction and p.extraction.attempted)
auto = [p for p in res.planned if p.decision.status == "auto"]
wrong = []
for p in auto:
    pid = str(p.merged.pid)
    if pid in exp.index:
        got = arrival_text(p.arrival, rules["arrival"])
        if got != exp.loc[pid, "arrival_true"]:
            wrong.append((pid, got, exp.loc[pid, "arrival_true"]))
review = [p for p in res.planned if p.decision.status != "auto"]
print("\n== BƯỚC 1")
print(f"kiểm tra file: ok={res.ok}")
print(f"lịch hẹn sau gộp: {n} | có ghi chú: {with_note} | gọi AI: {ai_called}")
print(f"thời gian xử lý (gồm AI): {dt:.0f} giây = {dt/60:.1f} phút")
print(f"tự điền: {len(auto)}/{n} = {len(auto)/n:.1%}")
print(f"tự điền sai giờ so với đáp án: {len(wrong)} {wrong[:5]}")
print(f"khám chờ: {sum(1 for p in res.planned if p.arrival.waiting)}")
print(f"cần duyệt: {len(review)}")
from collections import Counter  # noqa: E402

print("lý do cần duyệt:", Counter(r for p in review for r in p.decision.reasons).most_common())

# ---------- Bước 4
from core import db  # noqa: E402
from core.alerts import submit_response  # noqa: E402
from core.channels.alert_mock import MockAlertChannel  # noqa: E402
from core.models import Patient  # noqa: E402
from core.repo import upsert_patient  # noqa: E402

db.init_db()
ch = MockAlertChannel(path=TMP / "alerts.jsonl")
forms_cfg = load_forms()
print("\n== BƯỚC 4")
with db.session_scope() as s:
    for i in range(412):
        upsert_patient(s, f"P{i:04d}", "Phạm Thị Thu", f"09000{i:05d}", "Việt Nam")
    p = s.query(Patient).filter_by(code="R0412").one()
    p.edd = date.today() + timedelta(days=280 - 32 * 7)
    p.consent_status = "granted"
    answers = {q: "Không" for q in forms_cfg.forms["F6"]["questions"]}
    t0 = time.time()
    _resp, asm, alert, r = submit_response(
        s, p, "F6", answers, "chiều nay hoa mắt, mặt hơi sưng", rules=rules, forms_cfg=forms_cfg,
        client=client, alert_channel=ch)
    print(f"thời gian: {time.time()-t0:.1f} giây")
    print(f"mức: {asm.level} (quy tắc={asm.rule_level}, xu hướng={asm.trend_level}, AI={asm.ai_level}, "
          f"tin cậy={asm.ai_confidence}) | model={asm.model} prompt={asm.prompt_version}")
    print("tóm tắt:", asm.summary)
print(f"cảnh báo: {alert is not None}, đã gửi: {bool(ch.sent)}")  # gửi sau khi lưu xong
payload = json.dumps(ch.sent[0].to_dict(), ensure_ascii=False) if ch.sent else ""
print("payload:", payload)
print("payload không định danh:", "Thu" not in payload and not re.search(r"0\d{9}", payload))
