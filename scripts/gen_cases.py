"""15 hồ sơ thai phụ GIẢ, đầy đủ như hồ sơ khám thai thật – để xem / thử màn Hồ sơ. KHÔNG dùng dữ liệu thật.

Mỗi ca: thông tin sản khoa (tuổi, PARA, kỳ kinh cuối, ngày dự sinh, nhóm máu, dị ứng, tiền sử) + mọi lần khám từ
8 tuần tới hôm nay theo lịch khám thai mẫu (tham khảo WHO ANC 2016 – 8 lần tiếp xúc; QĐ 4128/QĐ-BYT – tối thiểu 4 lần;
thêm các mốc siêu âm 12, 20 tuần và NPDN 24 tuần) + kết quả từng lần (core/clinical.py) + cuộc sinh và khám sau sinh.
Dịch vụ của lịch hẹn ghi đúng những gì đã làm ở lần đó (khám, xét nghiệm, NPDN, siêu âm).

python scripts/gen_cases.py            # thêm / làm lại 15 ca trên data/app.db
Gọi tự động ở cuối scripts/seed_demo.py.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.clinical import History, make_baseline, make_visit_result  # noqa: E402
from core.config import TZ, load_clinical_reference, load_env  # noqa: E402
from core.models import Appointment, Arrival, NoteExtraction, Patient, PatientProgram, VisitResult  # noqa: E402
from core.patients import save_history, save_visit_result  # noqa: E402
from core.repo import next_code  # noqa: E402
from core.security import encrypt  # noqa: E402

DEPT = "Phòng khám Sản phụ khoa"
ANC_WEEKS = [8, 12, 16, 20, 24, 28, 32, 34, 36, 37, 38, 39]
POSTPARTUM_DAYS = [7, 42]
PKG = "Gói thai sản trọn gói"


@dataclass(frozen=True)
class Case:
    pid: str
    name: str
    age: int
    para: str              # PARA: đủ tháng – đẻ non – sẩy/phá – con sống
    ga_today: int | None   # tuổi thai hôm nay (ngày); None = đã sinh
    risks: frozenset[str] = frozenset()
    blood: str = "O Rh(+)"
    allergy: str = "Không ghi nhận"
    medical: tuple[str, ...] = ("Không ghi nhận bệnh lý nội khoa",)
    obstetric: tuple[str, ...] = ()
    nationality: str = "Việt Nam"
    package: bool = True
    override: str | None = None          # lý do bác sĩ chỉ định theo dõi ngoài gói
    delivered_days_ago: int | None = None
    birth_ga: int | None = None          # tuổi thai lúc sinh (ngày)
    birth: dict[str, str] = field(default_factory=dict)
    anemic: bool = False
    uti: bool = False
    breech: bool = False
    note: str = ""                       # ghi chú bác sĩ trên lịch hẹn (HIS)


def w(weeks: int, days: int = 0) -> int:
    return weeks * 7 + days


CASES: list[Case] = [
    Case("HS000101", "Nguyễn Thị Minh Anh", 26, "0000", w(34, 2), note="Khám thai định kỳ"),
    Case("HS000102", "Trần Ngọc Bích", 31, "1001", w(22, 4), obstetric=("2022: sinh thường 39 tuần, bé gái 3.100 g",)),
    Case("HS000103", "Lê Thu Hà", 33, "1001", w(32, 1), frozenset({"GDM"}),
         medical=("Thừa cân trước mang thai (BMI 27)",), obstetric=("2020: sinh thường 40 tuần, bé trai 3.600 g",),
         note="ĐTĐ thai kỳ – theo dõi đường huyết"),
    Case("HS000104", "Phạm Thị Hồng Nhung", 29, "0000", w(36, 0), frozenset({"HTN"}), note="Theo dõi huyết áp"),
    Case("HS000105", "Vũ Lan Phương", 30, "0000", w(30, 3), frozenset({"TWINS"}), note="Song thai – khám 2 tuần/lần"),
    Case("HS000106", "Đỗ Thị Thanh Mai", 34, "1001", w(37, 1), frozenset({"PREV_CSECTION"}),
         obstetric=("2021: mổ lấy thai 39 tuần do ngôi mông, bé trai 3.200 g",), note="Vết mổ cũ – tư vấn phương pháp sinh"),
    Case("HS000107", "Hoàng Thị Yến", 24, "0000", w(28, 2), anemic=True, note="Thiếu máu – uống sắt"),
    Case("HS000108", "Bùi Thị Ngọc Diệp", 28, "0010", w(31, 0), frozenset({"PRETERM"}),
         obstetric=("2023: sẩy thai 9 tuần",), note="Dọa sinh non 26 tuần – đã điều trị"),
    Case("HS000109", "Ngô Thị Quỳnh", 27, "1001", w(18, 5), uti=True,
         obstetric=("2023: sinh thường 38 tuần, bé gái 2.900 g",)),
    Case("HS000110", "Đặng Thị Thu Trang", 25, "0000", w(12, 6), note="Thai lần đầu – khám lần đầu 8 tuần"),
    Case("HS000111", "Mai Thị Kim Liên", 30, "1001", None, delivered_days_ago=10, birth_ga=w(39, 3),
         birth={"mode": "Sinh thường", "baby": "Bé gái 3.250 g", "apgar": "9/10"}),
    Case("HS000112", "Trịnh Thị Hải Yến", 32, "1001", None, frozenset({"HTN"}), delivered_days_ago=45, birth_ga=w(37, 4),
         birth={"mode": "Mổ lấy thai (tăng huyết áp)", "baby": "Bé trai 2.850 g", "apgar": "8/9"}),
    Case("HS000113", "Lý Thị Bảo Ngọc", 36, "2002", w(38, 2), frozenset({"GDM"}),
         obstetric=("2015: sinh thường 39 tuần, bé trai 3.400 g", "2019: sinh thường 40 tuần, bé gái 3.500 g"),
         allergy="Penicillin (nổi mẩn)", note="ĐTĐ thai kỳ – dự kiến sinh 39 tuần"),
    Case("HS000114", "Sato Yuki", 29, "0000", w(26, 1), nationality="Nhật Bản", blood="A Rh(+)",
         note="Khám thai định kỳ"),
    Case("HS000115", "Cao Thị Mỹ Duyên", 35, "1001", w(33, 4), frozenset({"HTN"}), package=False,
         override="Tăng huyết áp từ 28 tuần, nguy cơ tiền sản giật – theo dõi từ xa",
         medical=("Tăng huyết áp trước đây: không",), obstetric=("2018: sinh thường 39 tuần, bé trai 3.000 g",)),
]


def _rng(*parts: object) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def _services(res: dict, postpartum: bool) -> list[str]:
    """Dịch vụ của lần khám = đúng những gì đã làm."""
    out = ["Khám hậu sản" if postpartum else "Khám thai"]
    codes = {x["code"] for x in res["labs"]}
    if codes & {"OGTT_0", "OGTT_1", "OGTT_2"}:
        out.append("Nghiệm pháp dung nạp glucose (NPDN)")
    if codes - {"U_PRO", "U_GLU", "U_LEU", "U_NIT", "OGTT_0", "OGTT_1", "OGTT_2"}:
        out.append("Xét nghiệm máu")
    if codes & {"U_PRO", "U_GLU", "U_LEU", "U_NIT"}:
        out.append("Tổng phân tích nước tiểu")
    if res.get("ultrasound"):
        out.append(res["ultrasound"]["kind"])
    return out


def _remove(s: Session, pids: list[str]) -> None:
    ids = list(s.scalars(select(Patient.id).where(Patient.pid.in_(pids))))
    if not ids:
        return
    appt_ids = list(s.scalars(select(Appointment.id).where(Appointment.patient_id.in_(ids))))
    from core.models import PatientHistory
    from scripts.gen_monitoring import remove_monitoring

    remove_monitoring(s, ids)
    from core.models import FormPlanDecline

    s.execute(delete(FormPlanDecline).where(FormPlanDecline.patient_id.in_(ids)))

    s.execute(delete(VisitResult).where(VisitResult.appointment_id.in_(appt_ids)))
    s.execute(delete(NoteExtraction).where(NoteExtraction.appointment_id.in_(appt_ids)))
    s.execute(delete(Appointment).where(Appointment.id.in_(appt_ids)))
    s.execute(delete(Arrival).where(Arrival.pid.in_(pids)))
    s.execute(delete(PatientProgram).where(PatientProgram.patient_id.in_(ids)))
    s.execute(delete(PatientHistory).where(PatientHistory.patient_id.in_(ids)))
    s.execute(delete(Patient).where(Patient.id.in_(ids)))
    s.flush()


def build_case(s: Session, c: Case, today: date, ref: dict) -> int:
    """Tạo một hồ sơ hoàn chỉnh. Trả số lần khám."""
    rng = _rng("case", c.pid)
    if c.ga_today is not None:
        lmp = today - timedelta(days=c.ga_today)
        delivery = None
    else:
        assert c.delivered_days_ago is not None and c.birth_ga is not None
        delivery = today - timedelta(days=c.delivered_days_ago)
        lmp = delivery - timedelta(days=c.birth_ga)
    edd = lmp + timedelta(days=280)
    doctor = rng.choice(["BS. Phạm Văn Khải", "BS. Lê Thị Bích", "BS. Trần Quốc Tuấn", "BS. Vũ Thị Hằng"])
    p = Patient(pid=c.pid, full_name_enc=encrypt(c.name) or "", phone_enc=encrypt(f"09{rng.randint(10**7, 10**8 - 1)}") or "",
                nationality=c.nationality, code=next_code(s), consent_status="granted",
                consent_at=datetime.combine(lmp + timedelta(days=w(8)), time(9)),
                risk_groups=sorted({"HTN": "M-THA", "GDM": "M-DTD"}[r] for r in c.risks if r in ("HTN", "GDM")),
                proposed_risk_flags=[], edd=edd, delivery_date=delivery)
    s.add(p)
    s.flush()
    s.add(PatientProgram(patient_id=p.id, in_package=c.package, package_name=PKG if c.package else None,
                         package_since=lmp + timedelta(days=w(8)) if c.package else None,
                         override_monitoring=c.override is not None, override_reason=c.override,
                         updated_at=datetime.combine(lmp + timedelta(days=w(8)), time(9))))
    visits: list[tuple[date, int | None, int | None]] = [
        (lmp + timedelta(days=w(k)), w(k), None) for k in ANC_WEEKS
        if lmp + timedelta(days=w(k)) < today and (delivery is None or lmp + timedelta(days=w(k)) < delivery)]
    if c.risks & {"TWINS", "HTN"}:  # thai nguy cơ: thêm lần khám xen giữa từ 26 tuần
        visits += [(lmp + timedelta(days=w(k)), w(k), None) for k in (26, 30)
                   if lmp + timedelta(days=w(k)) < today and (delivery is None or lmp + timedelta(days=w(k)) < delivery)]
    if delivery:
        visits += [(delivery + timedelta(days=d), None, d) for d in POSTPARTUM_DAYS if delivery + timedelta(days=d) < today]
    visits.sort()
    base = make_baseline(rng, anemic=c.anemic, uti_risk=0.5 if c.uti else 0.0, breech=c.breech)
    hist = History()
    for i, (d, ga_days, pp_days) in enumerate(visits):
        res = make_visit_result(rng, ga_days=ga_days, postpartum_days=pp_days, risks=set(c.risks), base=base,
                                hist=hist, ref=ref)
        start = time(rng.choice([7, 8, 9, 10, 14, 15]), rng.choice([0, 15, 30, 45]))
        a = Appointment(patient_id=p.id, appt_date=d, start_time=start,
                        end_time=(datetime.combine(d, start) + timedelta(minutes=30)).time(),
                        services=_services(res, pp_days is not None), department=DEPT, doctor=doctor,
                        note_enc=encrypt(c.note if i else (c.note or "Khám thai lần đầu")) if (c.note or not i) else None,
                        booking_source="Bác sĩ hẹn" if i else "MyVinmec",
                        booked_at=datetime.combine(d - timedelta(days=14), time(10)), merged_from=[])
        s.add(a)
        s.flush()
        s.add(Arrival(pid=c.pid, visit_date=d, checkin_time=(datetime.combine(d, start) - timedelta(minutes=15)).time()))
        save_visit_result(s, a.id, res, source="synthetic", reference_version=str(ref.get("version")),
                          at=datetime.combine(d, start))
    if delivery is None:  # lần hẹn kế tiếp theo lịch khám thai
        upcoming = next((lmp + timedelta(days=w(k)) for k in ANC_WEEKS + [40] if lmp + timedelta(days=w(k)) >= today), None)
        if upcoming:
            s.add(Appointment(patient_id=p.id, appt_date=upcoming, start_time=time(8, 30), end_time=time(9, 0),
                              services=["Khám thai"], department=DEPT, doctor=doctor, note_enc=None,
                              booking_source="Bác sĩ hẹn", booked_at=datetime.combine(today, time(10)), merged_from=[]))
    history = {
        # PARA trước lần mang thai này; gravida = số lần có thai, tính cả lần này
        "age": c.age, "para": c.para, "gravida": sum(int(x) for x in c.para[:3]) + 1,
        "lmp": lmp.isoformat(), "edd": edd.isoformat(), "edd_basis": "Theo kỳ kinh cuối, khớp siêu âm quý 1",
        "blood_group": c.blood, "allergy": c.allergy, "medical_history": list(c.medical),
        "obstetric_history": list(c.obstetric) or ["Con so – chưa sinh lần nào"],
        "family_history": "Không ghi nhận bệnh di truyền",
        "risk_summary": sorted(c.risks),
    }
    if delivery:
        gw, gd = divmod(c.birth_ga or 0, 7)
        history["delivery"] = {"date": delivery.isoformat(), "ga": f"{gw}+{gd}", **c.birth}
    save_history(s, p.id, history, source="synthetic", at=datetime.combine(today, time(8)))
    return len(visits)


def generate(s: Session, today: date) -> dict[str, int]:
    ref = load_clinical_reference()
    _remove(s, [c.pid for c in CASES])
    n = sum(build_case(s, c, today, ref) for c in CASES)
    from scripts.gen_monitoring import generate as gen_monitoring

    mon = gen_monitoring(s, today)  # theo dõi tại nhà cho 5 ca nguy cơ (số đo, form, cảnh báo, xử trí)
    return {"cases": len(CASES), "visits": n, "monitoring_responses": mon["responses"], "monitoring_alerts": mon["alerts"]}


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_env()
    from core import db as dbmod

    dbmod.init_db()
    with dbmod.session_scope() as s:
        out = generate(s, datetime.now(TZ).date())
    print(f"Đã tạo {out['cases']} hồ sơ thai phụ đầy đủ, {out['visits']} lần khám có kết quả, "
          f"{out['monitoring_responses']} câu trả lời form tại nhà, {out['monitoring_alerts']} cảnh báo (dữ liệu giả).")


if __name__ == "__main__":
    main()
