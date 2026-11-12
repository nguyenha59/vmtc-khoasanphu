"""15 hồ sơ thai phụ soạn sẵn (scripts/gen_cases.py): đầy đủ, nhất quán theo diễn tiến thai kỳ, đúng quy ước y khoa."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.models import Appointment, Arrival, Base, Patient, PatientProgram
from core.patients import history_of, visit_results
from scripts.gen_cases import CASES, generate

TODAY = date(2026, 11, 12)


@pytest.fixture(scope="module")
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        out = generate(s, TODAY)
        s.commit()
        yield s, out


def _case(s: Session, pid: str):
    p = s.scalar(select(Patient).where(Patient.pid == pid))
    appts = s.scalars(select(Appointment).where(Appointment.patient_id == p.id).order_by(Appointment.appt_date)).all()
    res = visit_results(s, [a.id for a in appts])
    return p, appts, res


def test_counts(db):
    s, out = db
    assert out["cases"] == len(CASES) == 15 and out["visits"] >= 100


@pytest.mark.parametrize("c", CASES, ids=[c.pid for c in CASES])
def test_every_case_complete_and_consistent(db, c):
    s, _ = db
    p, appts, res = _case(s, c.pid)
    hist = history_of(s, p.id)
    assert hist and {"age", "para", "lmp", "edd", "blood_group", "allergy", "obstetric_history"} <= set(hist)
    assert (date.fromisoformat(hist["edd"]) - date.fromisoformat(hist["lmp"])).days == 280  # quy tắc Naegele
    past = [a for a in appts if a.appt_date < TODAY]
    assert past and all(a.id in res for a in past), "mọi lần khám đã qua đều có kết quả"
    assert {a.appt_date for a in past} <= set(s.scalars(select(Arrival.visit_date).where(Arrival.pid == c.pid)))
    for a in past:
        r = res[a.id]
        # dịch vụ của lịch hẹn khớp đúng việc đã làm
        assert ("Tổng phân tích nước tiểu" in a.services) == any(x["code"].startswith("U_") for x in r["labs"])
        assert bool(r["ultrasound"]) == any(sv.startswith("Siêu âm") for sv in a.services)
        assert r["diagnoses"] and r["diagnoses"][0]["primary"]
        codes = [d["code"] for d in r["diagnoses"]]
        if any(x.startswith("O") for x in codes):  # quy ước ICD-10
            assert not any(x.startswith(("Z34", "Z35")) for x in codes)
        names = " ".join(x["name"] for x in r["labs"]).lower()
        assert "hiv" not in names and "hbsag" not in names
    upcoming = [a for a in appts if a.appt_date >= TODAY]
    assert len(upcoming) == (0 if c.delivered_days_ago else 1), "thai phụ đang mang thai có lịch hẹn kế tiếp"
    pr = s.get(PatientProgram, p.id)
    assert pr.in_package == c.package and pr.override_monitoring == (c.override is not None)


def test_gdm_course(db):
    """ĐTĐ thai kỳ: NPDN đúng một lần ở 24 tuần, bất thường; mã O24.4 chỉ từ sau đó."""
    s, _ = db
    _, appts, res = _case(s, "HS000103")
    ogtt = [a for a in appts if any(x["code"] == "OGTT_2" for x in res.get(a.id, {}).get("labs", []))]
    assert len(ogtt) == 1 and "Nghiệm pháp dung nạp glucose (NPDN)" in ogtt[0].services
    for a in appts:
        if a.id not in res:
            continue
        has = any(d["code"] == "O24.4" for d in res[a.id]["diagnoses"])
        assert has == (a.appt_date >= ogtt[0].appt_date)


def test_four_ultrasounds_for_term_pregnancy(db):
    s, _ = db
    _, appts, res = _case(s, "HS000113")  # 38 tuần
    kinds = [res[a.id]["ultrasound"]["kind"] for a in appts if a.id in res and res[a.id]["ultrasound"]]
    assert len(kinds) == 4 and len(set(kinds)) == 4


def test_postpartum_case(db):
    s, _ = db
    p, appts, res = _case(s, "HS000112")
    hist = history_of(s, p.id)
    assert hist["delivery"]["mode"].startswith("Mổ lấy thai") and p.delivery_date
    pp = [a for a in appts if a.appt_date >= p.delivery_date]
    assert [a.services[0] for a in pp] == ["Khám hậu sản", "Khám hậu sản"]
    assert all(res[a.id]["diagnoses"][0]["code"] == "Z39.2" for a in pp)


def test_override_case_not_in_package(db):
    s, _ = db
    p = s.scalar(select(Patient).where(Patient.pid == "HS000115"))
    pr = s.get(PatientProgram, p.id)
    assert not pr.in_package and pr.override_monitoring and len(pr.override_reason or "") >= 10


def test_regenerate_is_idempotent(db):
    s, _ = db
    generate(s, TODAY)
    s.flush()
    assert s.scalar(select(Patient).where(Patient.pid == "HS000101")) is not None
    assert len(s.scalars(select(Patient).where(Patient.pid.like("HS0001%"))).all()) == 15
