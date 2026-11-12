"""Nạp dữ liệu GIẢ (data/synthetic) vào CSDL để chạy demo.

python scripts/seed_demo.py --reset
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DATA_DIR, load_env, load_forms, load_rules  # noqa: E402

DEPT = "Phòng khám Sản phụ khoa"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="xóa data/app.db trước khi nạp")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--demo-forms", type=int, default=40, help="số câu trả lời form mẫu để tạo cảnh báo")
    args = ap.parse_args()
    load_env()

    from core import db as dbmod

    if args.reset:
        p = Path(dbmod.db_url().replace("sqlite:///", ""))
        if p.exists():
            p.unlink()
        # Cảnh báo mock cũ không còn khớp CSDL mới → xóa cùng lúc.
        outbox = ROOT / "data" / "outbox" / "alerts.jsonl"
        if outbox.exists():
            outbox.unlink()
    dbmod.init_db()

    from core.ai.llm_client import MockClient
    from core.alerts import submit_response
    from core.channels.alert_mock import MockAlertChannel
    from core.models import Appointment, Arrival, FormResponse, Patient, VitalReading
    from core.security import encrypt

    syn = DATA_DIR / "synthetic"
    anchor = date.fromisoformat((syn / "anchor.txt").read_text().strip())
    rng = random.Random(args.seed)
    rules, forms_cfg = load_rules(), load_forms()

    pats = pd.read_csv(syn / "patients.csv", dtype=str, keep_default_na=False)
    appts = pd.read_csv(syn / "appointments_all.csv", dtype=str, keep_default_na=False)
    arrivals = pd.read_csv(syn / "arrivals_all.csv", dtype=str, keep_default_na=False)
    vitals = pd.read_csv(syn / "vitals.csv", dtype=str, keep_default_na=False)

    with dbmod.session_scope() as s:
        if s.query(Patient).count():
            print("CSDL đã có dữ liệu – dùng --reset để nạp lại.")
            return
        id_by_pid: dict[str, int] = {}
        for i, r in enumerate(pats.to_dict("records")):
            preg = r["pregnant"] == "1"
            risks = [x for x in r["risk_flags"].split(",") if x]
            modules = sorted({"HTN": "M-THA", "GDM": "M-DTD"}[x] for x in risks if x in ("HTN", "GDM"))
            confirmed = modules if rng.random() < 0.8 else []
            p = Patient(
                code=f"R{i + 1:04d}", pid=r["pid"], full_name_enc=encrypt(r["full_name"]), phone_enc=encrypt(r["phone"]),
                nationality=r["nationality"],
                consent_status=("granted" if preg and rng.random() < 0.75 else "none"),
                consent_at=datetime.combine(anchor - timedelta(days=rng.randint(10, 80)), time(9)) if preg else None,
                risk_groups=confirmed, proposed_risk_flags=[x for x in risks if x in ("HTN", "GDM")] if not confirmed else [],
                on_insulin=r["insulin"] == "1",
                edd=date.fromisoformat(r["edd"]) if r["edd"] else None,
                delivery_date=date.fromisoformat(r["delivery_date"]) if r["delivery_date"] else None,
            )
            if p.consent_status == "none":
                p.consent_at = None
            if p.code == "R0412":  # thai phụ mẫu cho kiểm thử chấp nhận 4
                p.edd, p.delivery_date, p.consent_status = anchor + timedelta(days=280 - 32 * 7), None, "granted"
                p.consent_at = datetime.combine(anchor - timedelta(days=30), time(9))
            s.add(p)
            s.flush()
            id_by_pid[p.pid] = p.id

        tomorrow = anchor + timedelta(days=1)
        appts = appts[(appts["department"] == DEPT) & (appts["appt_date"] != tomorrow.isoformat())]
        n_appt = 0
        for (pid, d), g in appts.groupby(["pid", "appt_date"]):
            g = g.sort_values("start_time")
            first = g.iloc[0]
            notes = [n for n in g["note"] if n]
            s.add(Appointment(
                patient_id=id_by_pid[pid], appt_date=date.fromisoformat(d),
                start_time=time.fromisoformat(first["start_time"]), end_time=time.fromisoformat(g.iloc[-1]["end_time"]),
                services=list(dict.fromkeys(g["service"])), department=DEPT, doctor=first["doctor"],
                note_enc=encrypt(" | ".join(notes)) if notes else None, booking_source=first["booking_source"],
                booked_at=datetime.fromisoformat(first["booked_at"]), merged_from=[],
            ))
            n_appt += 1
        for r in arrivals.to_dict("records"):
            s.add(Arrival(pid=r["pid"], visit_date=date.fromisoformat(r["visit_date"]),
                          checkin_time=time.fromisoformat(r["checkin_time"]) if r["checkin_time"] else None))
        for r in vitals.to_dict("records"):
            s.add(VitalReading(patient_id=id_by_pid[r["pid"]], measured_at=datetime.fromisoformat(r["measured_at"]),
                               kind=r["kind"], value=float(r["value"])))
        s.flush()
        print(f"Đã nạp {len(pats)} bệnh nhân, {n_appt} lịch hẹn, {len(arrivals)} lượt đến, {len(vitals)} chỉ số.")

        # Câu trả lời form mẫu gần đây → đánh giá thật qua pipeline (AI mock) để có cảnh báo demo
        from core.ingest.forms_import import parse_msforms_csv

        labels = pd.read_csv(ROOT / "tests" / "golden" / "form_expected.csv", dtype=str, keep_default_na=False)
        lab = labels.set_index("token")
        parsed = parse_msforms_csv((syn / "form_responses.csv").read_bytes(), forms_cfg)
        # Chỉ lấy câu trả lời 3 ngày gần nhất để có cảnh báo "mới"; giữ nguyên ngày gửi gốc
        # để tuần thai / hậu sản khớp với hồ sơ.
        recent = [x for x in parsed if x.submitted_at and x.submitted_at.date() >= anchor - timedelta(days=2)]
        picked = [x for x in recent if lab.loc[x.token, "expected_level"] != "green"][: args.demo_forms]
        picked += [x for x in recent if lab.loc[x.token, "expected_level"] == "green"][:20]
        channel = MockAlertChannel()
        client = MockClient()
        n_alert = 0
        for item in picked:
            pid = lab.loc[item.token, "pid"]
            p = s.get(Patient, id_by_pid[pid])
            if p is None or p.consent_status != "granted" or p.edd is None:
                continue
            at = item.submitted_at
            _r, _a, alert, _res = submit_response(
                s, p, item.form_code, item.answers, item.free_text, rules=rules, forms_cfg=forms_cfg, client=client,
                alert_channel=channel, source="seed", submitted_at=at, ner=False)
            n_alert += int(alert is not None)
        print(f"Đã tạo {s.query(FormResponse).count()} câu trả lời mẫu, {n_alert} cảnh báo.")


if __name__ == "__main__":
    main()
