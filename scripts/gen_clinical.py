"""Gán chương trình theo dõi (theo gói / không theo gói) cho thai phụ GIẢ của bộ dữ liệu lịch hẹn. KHÔNG dữ liệu thật.

Kết quả khám chi tiết chỉ có ở 15 hồ sơ soạn sẵn (scripts/gen_cases.py) – người dùng chọn 12/11/2026:
"chỉ cần 10–20 hồ sơ nhưng hoàn chỉnh, chính xác".

python scripts/gen_clinical.py            # chạy trên data/app.db (đã seed_demo)
Gọi tự động ở cuối scripts/seed_demo.py.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import TZ, load_env  # noqa: E402
from core.models import Appointment, Patient, PatientProgram  # noqa: E402

PACKAGE_RATE = 0.55
PACKAGES = ["Gói thai sản trọn gói", "Gói thai sản từ 28 tuần"]


def _rng(*parts: object) -> random.Random:
    """RNG ổn định theo khóa (cùng dữ liệu → cùng kết quả, không phụ thuộc thứ tự duyệt)."""
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def risks_from_csv(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return {r["pid"]: {x for x in r["risk_flags"].split(",") if x} for r in df.to_dict("records")}


def generate(s: Session, today: date, risks_by_pid: dict[str, set[str]] | None = None, seed: int = 42) -> dict[str, int]:
    """Gán theo gói (~55%) cho thai phụ chưa có chương trình. Không tạo kết quả khám."""
    n = 0
    for p in s.scalars(select(Patient).where(Patient.edd.is_not(None))):
        if s.get(PatientProgram, p.id) is not None:
            continue
        r = _rng(seed, "program", p.pid)
        first = s.scalar(select(Appointment.appt_date).where(Appointment.patient_id == p.id).order_by(Appointment.appt_date))
        pkg = r.random() < PACKAGE_RATE
        s.add(PatientProgram(patient_id=p.id, in_package=pkg, package_name=r.choice(PACKAGES) if pkg else None,
                             package_since=first if pkg else None, updated_at=datetime.combine(today, time(8))))
        n += 1
    return {"programs": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    load_env()
    from core import db as dbmod

    dbmod.init_db()
    with dbmod.session_scope() as s:
        out = generate(s, datetime.now(TZ).date(), seed=args.seed)
    print(f"Đã gán chương trình theo dõi cho {out['programs']} thai phụ (dữ liệu giả).")


if __name__ == "__main__":
    main()
