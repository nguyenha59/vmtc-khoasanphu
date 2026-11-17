"""Nạp dữ liệu GIẢ (data/synthetic) vào CSDL để chạy demo.

python scripts/seed_demo.py --reset
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DATA_DIR, load_env, load_forms, load_rules  # noqa: E402

DEPT = "Phòng khám Sản phụ khoa"

# Căn cứ GIẢ cho cờ nguy cơ demo – như luồng thật (core/repo.py): cờ chỉ có khi AI đọc một ghi chú lịch hẹn.
NOTE_EVIDENCE = {
    "HTN": "Theo dõi THA thai kỳ, HA lần khám 142/92, dặn đo HA sáng tối tại nhà",
    "GDM": "ĐTĐ thai kỳ, NPDH 75g dương tính, tư vấn chế độ ăn, hẹn kiểm tra đường huyết",
}


def seed_note_evidence(s, rules) -> int:  # type: ignore[no-untyped-def]
    """Mỗi cờ nguy cơ demo (Patient.proposed_risk_flags) phải có căn cứ để điều dưỡng kiểm trước khi xác nhận:
    thêm ghi chú lịch hẹn GIẢ (không tên / SĐT) vào lần hẹn gần nhất + kết quả AI đọc (NoteExtraction, model
    `seed:synthetic`). Cờ đã có căn cứ thì bỏ qua. Trả số cờ được bổ sung."""
    from sqlalchemy import select

    from core.models import Appointment, NoteExtraction, Patient
    from core.patients import note_evidence
    from core.security import decrypt, encrypt

    added = 0
    for p in s.scalars(select(Patient).where(Patient.proposed_risk_flags.is_not(None))):
        for flag in p.proposed_risk_flags or []:
            text = NOTE_EVIDENCE.get(flag)
            if text is None or note_evidence(s, p.id, flag) is not None:
                continue
            appt = s.scalar(select(Appointment).where(Appointment.patient_id == p.id)
                            .order_by(Appointment.appt_date.desc()).limit(1))
            if appt is None:
                continue
            old = decrypt(appt.note_enc) if appt.note_enc else ""
            appt.note_enc = encrypt(f"{old} | {text}" if old else text)
            s.add(NoteExtraction(appointment_id=appt.id, risk_flags=[flag], confidence=0.9, model="seed:synthetic",
                                 prompt_version="seed", rules_version=rules.rules_version, masked_note=text,
                                 votes=[], raw={"seed": True}))
            added += 1
    s.flush()
    return added


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="xóa data/app.db trước khi nạp")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--demo-forms", type=int, default=40, help="số câu trả lời form mẫu để tạo cảnh báo")
    ap.add_argument("--backfill-notes", action="store_true",
                    help="chỉ bổ sung căn cứ (ghi chú giả) cho cờ nguy cơ demo trên CSDL đang có – không nạp lại")
    args = ap.parse_args()
    load_env()

    from core import db as dbmod

    if args.backfill_notes:
        # Ghi chú GIẢ chỉ được thêm vào CSDL demo – không bao giờ vào hồ sơ thật (không hợp thức hóa cờ thiếu căn cứ).
        from core.config import is_production

        if is_production() or os.environ.get("SYNTHETIC_DATA_ONLY") != "1":
            sys.exit("Từ chối: --backfill-notes chỉ chạy trên CSDL dữ liệu GIẢ (SYNTHETIC_DATA_ONLY=1, không production).")
        dbmod.init_db()
        with dbmod.session_scope() as s:
            print(f"Đã bổ sung căn cứ cho {seed_note_evidence(s, load_rules())} cờ nguy cơ demo.")
        return

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
        print(f"Căn cứ ghi chú cho {seed_note_evidence(s, rules)} cờ nguy cơ chờ điều dưỡng xác nhận.")
        from scripts.gen_cases import generate as gen_cases
        from scripts.gen_clinical import generate as gen_programs

        ck = gen_programs(s, anchor)
        cs = gen_cases(s, anchor)
        print(f"Đã gán chương trình theo dõi cho {ck['programs']} thai phụ; tạo {cs['cases']} hồ sơ đầy đủ "
              f"({cs['visits']} lần khám có kết quả).")

        # Câu trả lời form mẫu gần đây → đánh giá thật qua pipeline (AI mock) để có cảnh báo demo
        from core.ingest.forms_import import parse_msforms_csv

        # Đáp án đi cùng form_responses.csv của data/synthetic (không lấy tests/golden – đó là bộ cố định cho test).
        src = syn / "form_expected.csv"
        labels = pd.read_csv(src if src.exists() else ROOT / "tests" / "golden" / "form_expected.csv", dtype=str,
                             keep_default_na=False)
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
