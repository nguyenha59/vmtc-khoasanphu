"""Sinh dữ liệu giả cho MVP (docs/03 mục 3). KHÔNG dùng dữ liệu thật.

Ví dụ: python scripts/gen_synthetic.py --seed 42   (mặc định gọn: 60 ngày, 40 lịch/ngày, ~450 khách)

Đầu ra:
  data/synthetic/  patients.csv, appointments_all.csv, arrivals_all.csv, arrivals_<ngày>.xlsx,
                   his_export_<ngày>.xlsx (3 ngày tới), his_error_*.xlsx (3 file lỗi cố ý),
                   form_responses.csv (định dạng Microsoft Forms), vitals.csv
  data/synthetic/golden/  his_day_<ngày>.xlsx, expected_arrivals_<ngày>.csv,
                   worldsms_expected_{VN,NN}_<ngày>.xlsx, notes_answers.csv, notes_eval.csv,
                   notes_fewshot.yaml, form_expected.csv  (đáp án đi cùng lần sinh này – run_acceptance đọc)
  tests/redteam/   form_cases.yaml (chỉ tạo khi chưa có)

tests/golden/ là bộ cố định đã commit cho pytest – mặc định KHÔNG ghi vào (người clone chạy cài đặt không làm
bẩn Git, không bị chặn khi git pull). Người bảo trì muốn sinh lại bộ cố định:
  python scripts/gen_synthetic.py --seed 42 --golden tests/golden
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import string
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Bangkok")

DEPT = "Phòng khám Sản phụ khoa"
OTHER_DEPTS = ["Phòng khám Nội tổng quát", "Phòng khám Tai Mũi Họng"]
HIS_COLS = {
    "pid": "Mã BN", "full_name": "Họ tên", "phone": "Điện thoại", "nationality": "Quốc tịch",
    "appt_date": "Ngày hẹn", "start_time": "Giờ bắt đầu", "end_time": "Giờ kết thúc",
    "service": "Dịch vụ", "department": "Khoa/Phòng", "doctor": "Bác sĩ", "note": "Ghi chú",
    "booked_at": "Thời điểm đặt",
}

SURNAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi",
            "Đỗ", "Hồ", "Ngô", "Dương", "Lý", "Đinh", "Trịnh", "Mai", "Tạ"]
MIDDLES = ["Thị", "Thị", "Thị", "Ngọc", "Thu", "Minh", "Thanh", "Hồng", "Kim", "Bảo", "Phương", "Diệu"]
GIVENS = ["Lan", "Hoa", "Hương", "Mai", "Trang", "Linh", "Thảo", "Ngân", "Vy", "Nhung", "Yến",
          "Quyên", "Hạnh", "Huyền", "Dung", "Loan", "Tuyết", "Phượng", "Oanh", "Thủy", "Giang",
          "Duyên", "Châu", "Trâm", "Nhi", "My", "Uyên", "Xuân", "Thơ", "Ly"]
DOCTOR_NAMES = ["Phạm Văn Khải", "Lê Thị Bích", "Trần Quốc Tuấn", "Đỗ Minh Khang",
                "Vũ Thị Hằng", "Ngô Đức Thịnh", "Bùi Thanh Sơn", "Hoàng Thị Lụa"]
# Khoa: khoảng 8 bác sĩ, khám 07:00–17:00, mỗi ca 30 phút, một bác sĩ không có hai ca trùng giờ (12/11/2026).
APPT_MINUTES = 30
OPEN_TIME, CLOSE_TIME = time(7, 0), time(17, 0)
FOREIGN = ["Hàn Quốc", "Nhật Bản", "Trung Quốc", "Mỹ", "Pháp", "Úc", "Singapore"]
# Tên GIẢ theo quốc tịch (khách nước ngoài có tên nước ngoài – khoa xác nhận 10/11/2026).
# (họ, tên) – ghép ngẫu nhiên, không dùng tên người nổi tiếng.
FOREIGN_NAMES: dict[str, tuple[list[str], list[str], str]] = {
    "Hàn Quốc": (["Kim", "Park", "Choi", "Jung", "Kang", "Yoon"], ["Min Ji", "Seo Yeon", "Ji Woo", "Ha Eun", "Soo Ah"], "surname_first"),
    "Nhật Bản": (["Tanaka", "Suzuki", "Sato", "Yamada", "Kobayashi"], ["Yuki", "Haruka", "Aiko", "Mei", "Sakura"], "given_first"),
    "Trung Quốc": (["Wang", "Li", "Zhang", "Liu", "Chen"], ["Xiao Mei", "Li Na", "Yu Ting", "Jia Hui"], "surname_first"),
    "Mỹ": (["Smith", "Johnson", "Miller", "Davis", "Wilson"], ["Emily", "Sarah", "Jessica", "Hannah", "Olivia"], "given_first"),
    "Pháp": (["Martin", "Bernard", "Dubois", "Moreau", "Laurent"], ["Camille", "Chloé", "Léa", "Manon"], "given_first"),
    "Úc": (["Brown", "Taylor", "Walker", "Harris", "Young"], ["Charlotte", "Amelia", "Grace", "Ella"], "given_first"),
    "Singapore": (["Tan", "Lim", "Ng", "Goh", "Teo"], ["Hui Min", "Wei Ling", "Shu Fen", "Jia Yi"], "surname_first"),
}
SELF_BOOKING_PREFIX = "Autobooking"

SVC_EXAM = "Khám thai"
SVC_US2D = "Siêu âm 2D"
SVC_NT = "Siêu âm độ mờ da gáy (NT)"
SVC_4D = "Siêu âm hình thái 4D"
SVC_OGTT = "Nghiệm pháp dung nạp glucose (NPDN)"
SVC_GBS = "Xét nghiệm liên cầu B"
SVC_BLOOD = "Xét nghiệm máu"
SVC_POSTPARTUM = "Khám hậu sản"
GYN_SERVICES = ["Khám phụ khoa", "Siêu âm phụ khoa", "Xét nghiệm Pap", "Khám phụ khoa"]
FASTING_SERVICES = {SVC_BLOOD, SVC_OGTT}


def is_us(service: str) -> bool:
    return "Siêu âm" in service


# ---------------------------------------------------------------- ghi chú
@dataclass
class NoteT:
    text: str
    ga: bool = False          # ghi chú có tuần thai
    ga_rx: bool = True        # tuần thai viết theo dạng regex đọc được
    explicit: bool = False    # có giờ đến ghi rõ ({t})
    early: int | None = None  # dặn đến trước N phút (chỉ AI đọc)
    call: bool = False
    risk: list[str] = field(default_factory=list)
    fasting: bool = False
    needs: str | None = None  # điều kiện: HTN/GDM/TWINS/PREV_CSECTION/PRETERM/LATE/INSULIN/NEW (≤12 tuần)/GYN (không mang thai)
    weight: float = 1.0


NOTE_TEMPLATES: list[NoteT] = [
    # Kiểu ghi chú ngắn, viết tắt, không dấu như file thật (khoa cung cấp dạng mẫu 10/11/2026).
    NoteT("thai moi", needs="NEW", weight=4),
    NoteT("thai moi ycbs", needs="NEW", weight=2),
    NoteT("{w}w ycbs kham va sieu am", ga=True, weight=3),
    NoteT("{w}w kham + SA", ga=True, weight=2),
    NoteT("tk {w}w", ga=True, weight=2),
    NoteT("ycbs den {t}", explicit=True, weight=1.5),
    NoteT("TK sau dinh chi thai", needs="GYN", weight=2),
    NoteT("HPV duong tinh 16, ctc den {t}", explicit=True, needs="GYN", weight=1.5),
    NoteT("soi ctc den {t}", explicit=True, needs="GYN"),
    NoteT("kham phu khoa dk", needs="GYN", weight=2),
    NoteT("Thai {w}w", ga=True, weight=3),
    NoteT("Thai {w}w, SA 4D", ga=True, weight=2),
    NoteT("tt {w} tuan {d} ngay, lam NPDN nhịn ăn", ga=True, fasting=True),
    NoteT("Thai {w} tuần {d} ngày", ga=True, weight=2),
    NoteT("TT {w}+{d}", ga=True),
    NoteT("{w} tuan, kham dinh ky", ga=True),
    NoteT("Thai {w}w{d}d, khám định kỳ", ga=True),
    NoteT("{w}w, dặn vào viện nếu đau bụng", ga=True, needs="LATE"),
    NoteT("Thai {w} tuần, SA hình thái", ga=True),
    NoteT("thai {w}w ngôi đầu", ga=True),
    NoteT("tuổi thai {w} tuần", ga=True),
    NoteT("Thai {w} w, tái khám", ga=True),
    NoteT("Thai {w}tuần {d}ngày", ga=True),
    NoteT("song thai {w}w", ga=True, risk=["TWINS"], needs="TWINS"),
    NoteT("thai {w}w, VMC cũ", ga=True, risk=["PREV_CSECTION"], needs="PREV_CSECTION"),
    NoteT("thai {wt} tuần, khám định kỳ", ga=True, ga_rx=False),
    NoteT("BS hẹn đến {t} lấy máu", explicit=True, fasting=True, weight=2),
    NoteT("Đến {t} để làm xét nghiệm", explicit=True),
    NoteT("den {t} nhin an lay mau", explicit=True, fasting=True),
    NoteT("có mặt lúc {t}", explicit=True),
    NoteT("BS dặn đến lúc {t}", explicit=True),
    NoteT("Hẹn đến {t} làm NPDN", explicit=True, fasting=True),
    NoteT("đến {t}, mang theo sổ khám", explicit=True),
    NoteT("co mat luc {t} de SA", explicit=True),
    NoteT("đến trước 30p làm XN", early=30, weight=0.6),
    NoteT("đến sớm 30 phút làm xét nghiệm", early=30, weight=0.6),
    NoteT("den truoc 20p", early=20, weight=0.6),
    NoteT("đến trước 1 tiếng để nhịn ăn lấy máu", early=60, fasting=True, weight=0.6),
    NoteT("đến sớm 45p", early=45, weight=0.6),
    NoteT("tới trước 15 phút", early=15, weight=0.6),
    NoteT("Chị {given} xin đến sớm 30p", early=30, weight=0.6),
    NoteT("gọi xác nhận lại giờ", call=True, weight=0.5),
    NoteT("?", call=True, weight=0.5),
    NoteT("BN xin đổi lịch, gọi lại", call=True, weight=0.5),
    NoteT("chưa rõ giờ, hỏi lại BS", call=True, weight=0.5),
    NoteT("goi xac nhan truoc 1 ngay", call=True, weight=0.5),
    NoteT("Khách muốn dời lịch", call=True, weight=0.5),
    NoteT("xác nhận lại dịch vụ", call=True, weight=0.5),
    NoteT("Thai {w}w, chưa rõ có làm 4D không", ga=True, call=True, weight=0.5),
    NoteT("THA thai kỳ, theo dõi HA sáng tối", risk=["HTN"], needs="HTN", weight=2),
    NoteT("ĐTĐTK, kiểm soát chế độ ăn", risk=["GDM"], needs="GDM", weight=2),
    NoteT("DTDTK tiem insulin", risk=["GDM"], needs="INSULIN"),
    NoteT("Tiền sản giật nhẹ, theo dõi HA", risk=["PREECLAMPSIA"], needs="HTN"),
    NoteT("TSG, dặn đo HA hằng ngày", risk=["PREECLAMPSIA"], needs="HTN"),
    NoteT("HA cao, thai {w}w", ga=True, risk=["HTN"], needs="HTN"),
    NoteT("đái tháo đường thai kỳ, thai {w} tuần", ga=True, risk=["GDM"], needs="GDM"),
    NoteT("dọa sinh non, nghỉ ngơi", risk=["PRETERM_RISK"], needs="PRETERM"),
    NoteT("VMC cũ 1 lần", risk=["PREV_CSECTION"], needs="PREV_CSECTION"),
    NoteT("Song thai, theo dõi sát", risk=["TWINS"], needs="TWINS"),
    NoteT("cổ tử cung ngắn, dọa sinh non", risk=["PRETERM_RISK"], needs="PRETERM"),
    NoteT("SA 4D", weight=2),
    NoteT("Khám định kỳ", weight=2),
    NoteT("mang theo kết quả XN lần trước"),
    NoteT("kham thai dinh ky"),
    NoteT("Tái khám theo hẹn", weight=2),
    NoteT("BN đi cùng chồng"),
    NoteT("Đã đóng gói thai sản"),
    NoteT("lam XN mau"),
    NoteT("SA hinh thai"),
    NoteT("uống đủ nước trước SA"),
    NoteT("nhắc mang sổ khám thai"),
    NoteT("Xét nghiệm nước tiểu"),
    NoteT("kq double test binh thuong"),
    NoteT("lịch SA doppler"),
    NoteT("BS {bs} dặn tái khám sau SA"),
    NoteT("liên hệ người nhà {phone2}"),
]
assert len(NOTE_TEMPLATES) >= 60

NUM_WORDS = {20: "hai mươi", 21: "hai mốt", 22: "hai hai", 24: "hai tư", 25: "hai lăm",
             26: "hai sáu", 28: "hai tám", 30: "ba mươi", 32: "ba hai", 34: "ba tư"}


def fmt_hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def add_min(t: time, minutes: int) -> time:
    dt = datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)
    return dt.time()


def time_variants(t: time, rng: random.Random) -> str:
    h, m = t.hour, t.minute
    if m == 0:
        return rng.choice([f"{h}h", f"{h} giờ", f"{h}h00", f"{h:02d}:00"])
    return rng.choice([f"{h}h{m:02d}", f"{h}:{m:02d}", f"{h:02d}h{m:02d}", f"{h}g{m:02d}"])


# ---------------------------------------------------------------- bệnh nhân
@dataclass
class Pat:
    pid: str
    full_name: str
    given: str
    phone: str
    nationality: str
    pregnant: bool
    edd: date | None = None
    delivery: date | None = None
    risks: list[str] = field(default_factory=list)
    insulin: bool = False
    habitual: bool = False


def make_name(rng: random.Random, nationality: str = "Việt Nam") -> tuple[str, str]:
    """Trả (họ tên, tên gọi). Khách nước ngoài → tên theo quốc tịch."""
    if nationality in FOREIGN_NAMES:
        surnames, givens, order = FOREIGN_NAMES[nationality]
        sur, given = rng.choice(surnames), rng.choice(givens)
        return (f"{sur} {given}" if order == "surname_first" else f"{given} {sur}"), given
    given = rng.choice(GIVENS)
    return f"{rng.choice(SURNAMES)} {rng.choice(MIDDLES)} {given}", given


def make_patients(rng: random.Random, n_preg: int, n_gyn: int, start: date) -> list[Pat]:
    used_phones: set[str] = set()
    pats: list[Pat] = []
    for i in range(n_preg + n_gyn):
        name, given = make_name(rng)
        while True:
            phone = "0900" + f"{rng.randint(100000, 999999)}"
            if phone not in used_phones:
                used_phones.add(phone)
                break
        nat = "Việt Nam" if rng.random() > 0.15 else rng.choice(FOREIGN)
        if nat != "Việt Nam":
            name, given = make_name(rng, nat)
        p = Pat(pid=f"{26000000 + i * 7 + 13}", full_name=name, given=given, phone=phone,
                nationality=nat, pregnant=i < n_preg, habitual=rng.random() < 0.10)
        if p.pregnant:
            ga_at_start = rng.randint(-8 * 7, 40 * 7)
            p.edd = start + timedelta(days=280 - ga_at_start)
            p.delivery = p.edd + timedelta(days=rng.randint(-21, 7))
            r = rng.random()
            if r < 0.12:
                p.risks.append("HTN")
            elif r < 0.22:
                p.risks.append("GDM")
                p.insulin = rng.random() < 0.3
            if rng.random() < 0.08:
                p.risks.append("PREV_CSECTION")
            if rng.random() < 0.02:
                p.risks.append("TWINS")
            if rng.random() < 0.04:
                p.risks.append("PRETERM")
        pats.append(p)
    return pats


def ga_on(p: Pat, d: date) -> tuple[int, int] | None:
    if not p.edd:
        return None
    total = 280 - (p.edd - d).days
    return total // 7, total % 7


# ---------------------------------------------------------------- lịch hẹn
SLOTS = [time(h, m) for h in range(7, 17) for m in (0, 15, 30, 45)][:-1]
SLOT_W = [3 if s.hour < 12 else 1.6 for s in SLOTS]


def main_service(ga_w: int, rng: random.Random) -> str:
    if 11 <= ga_w <= 13:
        return SVC_NT
    if 20 <= ga_w <= 22:
        return SVC_4D
    if 24 <= ga_w <= 27:
        return SVC_OGTT
    if 35 <= ga_w <= 36:
        return SVC_GBS
    return rng.choices([SVC_EXAM, SVC_US2D, SVC_BLOOD], [55, 30, 15])[0]


def interval_days(ga_w: int, rng: random.Random) -> int:
    if ga_w < 28:
        return rng.randint(28, 42)
    if ga_w < 36:
        return 14
    return 7


@dataclass
class Visit:
    pat: Pat
    day: date
    rows: list[dict]
    first_visit: bool
    note_t: NoteT | None = None
    note: str = ""


def simulate_visits(rng: random.Random, pats: list[Pat], start: date, end: date, per_day: int) -> list[Visit]:
    visits: list[Visit] = []
    seen: set[str] = set()
    doctors = [f"BS. {n}" for n in DOCTOR_NAMES]
    preg_by_day: dict[date, int] = {}

    def _end_30(st: time, rng: random.Random) -> time:
        """Mọi ca khám: giờ kết thúc = giờ bắt đầu + 30 phút (khoa 12/11/2026).
        Vẫn rút một số ngẫu nhiên như trước để các phần dữ liệu khác sinh ra giống hệt (cùng seed)."""
        rng.choice([15, 30])
        return add_min(st, 30)

    def new_row(p: Pat, d: date, service: str, st: time, dept: str, prev: date | None) -> dict:
        if prev is not None and rng.random() < 0.7:
            src, booked = "Bác sĩ hẹn", prev
        else:
            src = "MyVinmec" if rng.random() < 0.55 else "Bác sĩ hẹn"
            booked = d - timedelta(days=rng.randint(1, 35))
        booked_at = datetime.combine(booked, time(rng.randint(7, 20), rng.randint(0, 59)))
        return {"pid": p.pid, "full_name": p.full_name, "phone": p.phone, "nationality": p.nationality,
                "appt_date": d, "start_time": st, "end_time": _end_30(st, rng),
                "service": service, "department": dept, "doctor": rng.choice(doctors), "note": "",
                "booking_source": src, "booked_at": booked_at}

    for p in pats:
        if not p.pregnant:
            continue
        assert p.edd and p.delivery
        first_d = p.edd - timedelta(days=280 - 8 * 7)
        d = max(start + timedelta(days=rng.randint(0, 20)), first_d)
        prev: date | None = None
        while d < end and d < p.delivery:
            ga = ga_on(p, d)
            assert ga
            svc = main_service(ga[0], rng)
            st = rng.choices(SLOTS, SLOT_W)[0]
            rows = [new_row(p, d, svc, st, DEPT, prev)]
            if rng.random() < 0.045 and st <= time(14, 0):
                kind = rng.random()
                if kind < 0.6:
                    rows.append(new_row(p, d, SVC_US2D if svc != SVC_US2D else SVC_EXAM,
                                        add_min(st, rng.choice([30, 45, 60, 90, 120])), DEPT, prev))
                elif kind < 0.8:
                    rows.append(new_row(p, d, SVC_BLOOD, add_min(st, rng.choice([30, 60, 90])), DEPT, prev))
                else:
                    rows.append(new_row(p, d, SVC_US2D, add_min(st, rng.choice([195, 210, 240])), DEPT, prev))
            visits.append(Visit(pat=p, day=d, rows=rows, first_visit=p.pid not in seen))
            seen.add(p.pid)
            preg_by_day[d] = preg_by_day.get(d, 0) + 1
            prev = d
            d = d + timedelta(days=interval_days(ga[0], rng))
        pp = p.delivery + timedelta(days=42)
        if start <= pp < end:
            st = rng.choices(SLOTS, SLOT_W)[0]
            visits.append(Visit(pat=p, day=pp, rows=[new_row(p, pp, SVC_POSTPARTUM, st, DEPT, p.delivery)],
                                first_visit=False))
            preg_by_day[pp] = preg_by_day.get(pp, 0) + 1

    gyn = [p for p in pats if not p.pregnant]
    d = start
    while d < end:
        n = max(0, per_day + rng.randint(-8, 8) - preg_by_day.get(d, 0))
        for p in rng.sample(gyn, min(n, len(gyn))):
            dept = DEPT if rng.random() > 0.06 else rng.choice(OTHER_DEPTS)
            st = rng.choices(SLOTS, SLOT_W)[0]
            visits.append(Visit(pat=p, day=d, rows=[new_row(p, d, rng.choice(GYN_SERVICES), st, dept, None)],
                                first_visit=p.pid not in seen))
            seen.add(p.pid)
        d += timedelta(days=1)
    visits.sort(key=lambda v: (v.day, v.rows[0]["start_time"], v.pat.pid))
    # một khách khám phụ khoa có thể bị chọn trùng ngày với lượt khác → bỏ trùng (pid, ngày)
    uniq: dict[tuple[str, date], Visit] = {}
    for v in visits:
        uniq.setdefault((v.pat.pid, v.day), v)
    return list(uniq.values())


def assign_doctors(visits: list[Visit], seed: int) -> tuple[int, int]:
    """Xếp 8 bác sĩ để không ai có hai ca chồng giờ; mọi ca nằm trong giờ khám OPEN_TIME–CLOSE_TIME.

    Theo từng ngày, xét các ca theo giờ khách đặt; chọn khung 15 phút GẦN giờ đặt nhất (sớm hoặc muộn) còn bác sĩ rảnh
    đủ APPT_MINUTES phút, rồi chọn ngẫu nhiên một bác sĩ rảnh. Dùng RNG riêng → các dữ liệu khác không đổi.
    Trả (số ca phải dời giờ, số ca không xếp được – vẫn giữ giờ cũ, cần tăng bác sĩ / giảm --per-day).
    """
    rng = random.Random(seed * 7919 + 1)
    pool = [f"BS. {n}" for n in DOCTOR_NAMES]
    ticks_per_appt = APPT_MINUTES // 15
    first = OPEN_TIME.hour * 4 + OPEN_TIME.minute // 15
    last_start = CLOSE_TIME.hour * 4 + CLOSE_TIME.minute // 15 - ticks_per_appt  # 16:30 → kết thúc 17:00
    by_day: dict[date, list[dict]] = {}
    for v in visits:
        for r in v.rows:
            by_day.setdefault(v.day, []).append(r)
    shifted = failed = 0
    for rows in by_day.values():
        occ: dict[str, set[int]] = {d: set() for d in pool}
        for r in sorted(rows, key=lambda r: (r["start_time"], r["pid"])):
            want = r["start_time"].hour * 4 + r["start_time"].minute // 15
            want = min(max(want, first), last_start)
            order = sorted(range(first, last_start + 1), key=lambda k: (abs(k - want), k < want))
            chosen: tuple[int, str] | None = None
            for k in order:
                free = [d for d in pool if not occ[d] & set(range(k, k + ticks_per_appt))]
                if free:
                    chosen = (k, rng.choice(free))
                    break
            if chosen is None:
                failed += 1
                continue
            k, doc = chosen
            occ[doc] |= set(range(k, k + ticks_per_appt))
            st = time(k // 4, (k % 4) * 15)
            if st != r["start_time"]:
                shifted += 1
            r["start_time"], r["end_time"], r["doctor"] = st, add_min(st, APPT_MINUTES), doc
    for v in visits:
        v.rows.sort(key=lambda r: r["start_time"])
    return shifted, failed


def pick_note(rng: random.Random, v: Visit, start_merged: time, note_rate: float = 0.45) -> None:
    """note_rate: tỉ lệ lịch có ghi chú. File HIS thật: lịch nào cũng có ghi chú (khoa xác nhận 12/11/2026) → 1.0.
    Mặc định 0.45 giữ nguyên bộ đáp án test / benchmark đã dựng."""
    p = v.pat
    if note_rate < 1.0 and rng.random() > note_rate:
        return
    ga = ga_on(p, v.day) if p.pregnant and (p.delivery and v.day < p.delivery) else None
    cands: list[NoteT] = []
    for t in NOTE_TEMPLATES:
        if t.ga and not ga:
            continue
        if t.ga and ga and ga[0] < 5:
            continue
        if "{wt}" in t.text and (not ga or ga[0] not in NUM_WORDS):
            continue
        if t.needs == "LATE" and (not ga or ga[0] < 36):
            continue
        if t.needs == "NEW" and (not ga or ga[0] > 12):
            continue
        if t.needs == "GYN" and p.pregnant:
            continue
        if t.needs == "HTN" and "HTN" not in p.risks:
            continue
        if t.needs == "GDM" and "GDM" not in p.risks:
            continue
        if t.needs == "INSULIN" and not p.insulin:
            continue
        if t.needs == "TWINS" and "TWINS" not in p.risks:
            continue
        if t.needs == "PREV_CSECTION" and "PREV_CSECTION" not in p.risks:
            continue
        if t.needs == "PRETERM" and "PRETERM" not in p.risks:
            continue
        if t.risk and not p.pregnant:
            continue
        cands.append(t)
    if not cands:
        if note_rate < 1.0:
            return
        cands = [t for t in NOTE_TEMPLATES if t.text in ("Khám định kỳ", "Tái khám theo hẹn")]  # luôn có ghi chú
    t = rng.choices(cands, [c.weight for c in cands])[0]
    text = t.text
    if ga:
        text = text.replace("{wt}", NUM_WORDS.get(ga[0], str(ga[0])))
        text = text.replace("{w}", str(ga[0])).replace("{d}", str(ga[1]))
    if t.explicit:
        offsets = [o for o in (15, 30, 45, 60) if add_min(start_merged, -o) >= time(6, 30)]
        if not offsets:
            return
        v.explicit_time = add_min(start_merged, -rng.choice(offsets))  # type: ignore[attr-defined]
        text = text.replace("{t}", time_variants(v.explicit_time, rng))  # type: ignore[attr-defined]
    text = text.replace("{given}", p.given)
    text = text.replace("{bs}", v.rows[0]["doctor"].split()[-1])
    text = text.replace("{phone2}", "09" + "".join(rng.choice(string.digits) for _ in range(8)))
    v.note_t, v.note = t, text
    v.rows[0]["note"] = text
    if note_rate >= 1.0:  # file HIS thật: mỗi dòng lịch đều có ghi chú
        for r in v.rows[1:]:
            r["note"] = r.get("note") or text


# ---------------------------------------------------------------- đáp án lịch hẹn
def merged_info(v: Visit) -> dict:
    rows = sorted(v.rows, key=lambda r: r["start_time"])
    first = rows[0]
    services = [r["service"] for r in rows]
    conflict = False
    if len(rows) > 1:
        span = (datetime.combine(v.day, rows[-1]["start_time"]) - datetime.combine(v.day, first["start_time"]))
        if span.total_seconds() / 60 > 180:
            conflict = True
        if any(r["service"] in FASTING_SERVICES and r["start_time"] > first["start_time"] for r in rows[1:]):
            conflict = True
    return {"start": first["start_time"], "services": services, "conflict": conflict}


def _waiting_cfg() -> dict | None:
    with open(ROOT / "config" / "rules.yaml", encoding="utf-8") as f:
        return (yaml.safe_load(f)["arrival"] or {}).get("waiting_slot")


WAITING = _waiting_cfg()


def is_waiting(t: time) -> bool:
    """Lịch "khám chờ" theo rules.yaml → arrival.waiting_slot (khung [from, to))."""
    return bool(WAITING) and time.fromisoformat(WAITING["from"]) <= t < time.fromisoformat(WAITING["to"])


# Đáp án độc lập với code app: xét nghiệm 30', siêu âm 20', khám / khác 15' (khoa xác nhận 12/11/2026).
_LAB_NOTE_RX = re.compile(r"(?<!\w)(xn|xet nghiem|lay mau|nhin an|npdn|nghiem phap|duong huyet)(?!\w)")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def row_minutes_early(service: str, note: str) -> int:
    if "Xét nghiệm" in service or "Nghiệm pháp" in service or _LAB_NOTE_RX.search(_plain(note or "")):
        return 30
    return 20 if is_us(service) else 15


def rule_time_for(v: Visit) -> time:
    """Mỗi lịch: giờ hẹn − số phút của lịch đó; lấy giờ sớm nhất."""
    rows = [r for r in v.rows if not is_waiting(r["start_time"])] or v.rows  # bỏ lịch khám chờ nếu còn lịch thật
    return min(add_min(r["start_time"], -row_minutes_early(r["service"], r.get("note", ""))) for r in rows)


def _valid_arrival(a: time, start: time) -> bool:
    gap = (datetime.combine(date(2000, 1, 1), start) - datetime.combine(date(2000, 1, 1), a)).total_seconds() / 60
    return a >= time(6, 30) and 0 <= gap <= 90


def expected_for(v: Visit) -> dict:
    m = merged_info(v)
    start: time = m["start"]
    rule_time = rule_time_for(v)
    t = v.note_t
    explicit = getattr(v, "explicit_time", None)
    ga = ga_on(v.pat, v.day) if (t and t.ga) else None
    bad_phone = not (len(v.rows[0]["phone"]) == 10 and v.rows[0]["phone"].isdigit())

    # Bác sĩ dặn (giờ ghi rõ hoặc "đến trước N phút/tiếng") – regex đọc được, không cần AI.
    doctor = explicit or (add_min(start, -t.early) if t and t.early is not None else None)
    reasons_rule: list[str] = []
    if doctor is not None and not _valid_arrival(doctor, start):
        arrival_rule = rule_time
        reasons_rule.append("NO_RULE")
    else:
        arrival_rule = min(doctor, rule_time) if doctor else rule_time  # bác sĩ dặn vs quy tắc: lấy sớm hơn
    if m["conflict"]:
        reasons_rule.append("CONFLICT")
    if t and t.call:
        reasons_rule.append("CALL_NOTE")
    if ga and t and t.ga_rx and ga[0] >= 37:
        reasons_rule.append("LATE_GA")
    if bad_phone:
        reasons_rule.append("BAD_PHONE")

    # chế độ có AI hoàn hảo: AI đọc giống regex → cùng giờ đến
    arrival_true = arrival_rule
    reasons_ai = [r for r in reasons_rule if r != "LATE_GA"]
    if ga and ga[0] >= 37:
        reasons_ai.append("LATE_GA")
    arrival_rule_s, arrival_true_s = fmt_hhmm(arrival_rule), fmt_hhmm(arrival_true)
    if is_waiting(start):
        # Khám chờ: không có giờ đến; ghi chú có giờ / dặn đến sớm → người xác nhận (giữ khám chờ).
        arrival_rule_s = arrival_true_s = WAITING["label"]
        reasons_ai = [r for r in reasons_ai if r != "AI_RULE_DIFF"]
        reasons_rule = [r for r in reasons_rule if r != "NO_RULE"]
        reasons_ai = [r for r in reasons_ai if r != "NO_RULE"]
        if doctor is not None:
            reasons_rule.append("WAITING_NOTE_TIME")
            reasons_ai.append("WAITING_NOTE_TIME")
        limit = add_min(time.fromisoformat(WAITING["to"]), int(WAITING.get("mixed_review_minutes", 30)))
        if any(not is_waiting(r["start_time"]) and r["start_time"] > limit for r in v.rows):
            reasons_rule.append("WAITING_MIXED")
            reasons_ai.append("WAITING_MIXED")
    order = ["LOW_CONF", "CALL_NOTE", "LATE_GA", "CONFLICT", "AI_RULE_DIFF", "WAITING_NOTE_TIME", "WAITING_MIXED", "BAD_PHONE",
             "PII_LEFT", "NO_RULE"]
    reasons_rule = sorted(set(reasons_rule), key=order.index)
    reasons_ai = sorted(set(reasons_ai), key=order.index)
    return {
        "pid": v.pat.pid,
        "appt_date": v.day.isoformat(),
        "start_time": fmt_hhmm(start),
        "services": "; ".join(sorted(set(m["services"]))),
        "merged_rows": len(v.rows),
        "note": v.note,
        "arrival_true": arrival_true_s,
        "arrival_rule": arrival_rule_s,
        "status_rule": "needs_review" if reasons_rule else "auto",
        "reasons_rule": ",".join(reasons_rule),
        "status_ai": "needs_review" if reasons_ai else "auto",
        "reasons_ai": ",".join(reasons_ai),
        "ga_weeks": ga[0] if ga else "",
        "ga_days": ga[1] if ga else "",
        "risk_flags": ",".join(t.risk) if t else "",
        "nationality": v.pat.nationality,
    }


def note_answer(v: Visit) -> dict:
    t = v.note_t
    assert t
    ga = ga_on(v.pat, v.day) if t.ga else None
    explicit = getattr(v, "explicit_time", None)
    has_days = ga is not None and "{d}" in t.text
    return {
        "explicit_arrival_time": fmt_hhmm(explicit) if explicit else None,
        "minutes_early": t.early,
        "needs_fasting": t.fasting,
        "ga_weeks": ga[0] if ga else None,
        "ga_days": ga[1] if has_days else None,
        "risk_flags": [r if r != "PRETERM" else "PRETERM_RISK" for r in t.risk],
        "asks_confirmation": t.call,
        "evidence": v.note[:200],
    }


# ---------------------------------------------------------------- bỏ hẹn
def noshow_logit(v: Visit, prior_ns: int) -> float:
    r = v.rows[0]
    lead = (v.day - r["booked_at"].date()).days
    x = 0.0
    x += 1.1 if lead > 21 else 0.0
    x += 0.9 if (v.day.weekday() == 2 and r["start_time"] >= time(12, 0)) else 0.0
    x += 1.4 if v.pat.habitual else 0.0
    x += 0.5 * min(prior_ns, 3)
    x += 0.8 if v.first_visit else 0.0
    x += 0.3 if v.pat.nationality != "Việt Nam" else 0.0
    x += 0.35 if r["booking_source"] == "MyVinmec" else 0.0
    return x


# ---------------------------------------------------------------- xuất HIS
def his_note(r: dict) -> str:
    """Ghi chú như trong file HIS thật: khách tự đặt (MyVinmec) → bắt đầu bằng "Autobooking"."""
    if r["booking_source"] == "MyVinmec":
        return f"{SELF_BOOKING_PREFIX} {r['note']}".strip()
    return r["note"]


def to_his_frame(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        out.append({
            HIS_COLS["pid"]: r["pid"], HIS_COLS["full_name"]: r["full_name"], HIS_COLS["phone"]: r["phone"],
            HIS_COLS["nationality"]: r["nationality"], HIS_COLS["appt_date"]: r["appt_date"].strftime("%d/%m/%Y"),
            HIS_COLS["start_time"]: fmt_hhmm(r["start_time"]), HIS_COLS["end_time"]: fmt_hhmm(r["end_time"]),
            HIS_COLS["service"]: r["service"], HIS_COLS["department"]: r["department"],
            HIS_COLS["doctor"]: r["doctor"], HIS_COLS["note"]: his_note(r),
            # File HIS thật không có cột nguồn đặt; khách tự đặt nhận qua tiền tố "Autobooking" trong ghi chú.
            HIS_COLS["booked_at"]: r["booked_at"].strftime("%d/%m/%Y %H:%M"),
        })
    return pd.DataFrame(out)


# File OH thật (17/11/2026): 6 cột, không có Dịch vụ / Khoa → dịch vụ chỉ còn trong ghi chú, viết tắt như bác sĩ.
OH_COLS = ["HN", "Patient Name", "Appointment Start Date Time", "Appointment Resource", "Appointment Comment", "Mobile"]
OH_SERVICE_NOTE = {
    SVC_EXAM: "khám thai", SVC_US2D: "SA 2D", SVC_NT: "SA NT", SVC_4D: "SA 4D", SVC_OGTT: "NPDN",
    SVC_GBS: "XN GBS", SVC_BLOOD: "XN máu", SVC_POSTPARTUM: "tkss", "Khám phụ khoa": "khám phụ khoa",
    "Siêu âm phụ khoa": "SA phụ khoa", "Xét nghiệm Pap": "Pap",
}


def to_oh_frame(rows: list[dict]) -> pd.DataFrame:
    """Lịch của khoa theo định dạng file OH: dịch vụ ghép vào đầu ghi chú."""
    out = []
    for r in rows:
        if r["department"] != DEPT:
            continue
        hint = OH_SERVICE_NOTE.get(r["service"], r["service"])
        note = his_note(r)
        out.append({
            "HN": r["pid"], "Patient Name": r["full_name"],
            "Appointment Start Date Time": datetime.combine(r["appt_date"], r["start_time"]),
            "Appointment Resource": r["doctor"],
            "Appointment Comment": f"{hint}, {note}".strip(" ,") if note else hint,
            "Mobile": r["phone"],
        })
    return pd.DataFrame(out, columns=OH_COLS)


def write_xlsx(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.astype(str).replace({"nan": "", "None": ""}).to_excel(path, index=False)


# ---------------------------------------------------------------- form
@dataclass
class TextCase:
    text: str
    level: str
    concepts: list[str]
    group: str


FREE_TEXTS: list[TextCase] = [
    # đỏ
    TextCase("chiều nay hoa mắt, mặt hơi sưng", "red", ["VISUAL", "FACE_EDEMA"], "cumulative"),
    TextCase("hơi hoa mắt, mặt sưng", "red", ["VISUAL", "FACE_EDEMA"], "cumulative"),
    TextCase("sáng nay ra máu đỏ tươi", "red", ["BLEEDING"], "plain"),
    TextCase("bé ít đạp từ hôm qua đến giờ", "red", ["REDUCED_FM"], "plain"),
    TextCase("be it dap tu sang", "red", ["REDUCED_FM"], "no_diacritics"),
    TextCase("ra mau it", "red", ["BLEEDING"], "no_diacritics"),
    TextCase("hoa mat chong mat", "red", ["VISUAL"], "no_diacritics"),
    TextCase("đau đầu dữ dội, nhìn mờ", "red", ["HEADACHE", "VISUAL"], "plain"),
    TextCase("ra nước ướt quần liên tục", "red", ["FLUID"], "plain"),
    TextCase("khó thở khi nằm", "red", ["DYSPNEA"], "plain"),
    TextCase("bắp chân trái sưng đau", "red", ["CALF"], "plain"),
    TextCase("đau dưới ức từ tối qua", "red", ["EPIGASTRIC"], "plain"),
    TextCase("em mệt quá, chỉ muốn biến mất", "red", ["SELF_HARM"], "self_harm"),
    TextCase("nhiều lúc thấy mọi người sẽ tốt hơn nếu không có em", "red", ["SELF_HARM"], "self_harm"),
    TextCase("dau bung nhieu qua", "red", ["ABD_PAIN"], "no_diacritics"),
    TextCase("không ra máu nhưng bé ít đạp", "red", ["REDUCED_FM"], "negation"),
    TextCase("con nằm im cả buổi sáng", "red", ["REDUCED_FM"], "plain"),
    TextCase("thấy đom đóm mắt khi đứng dậy", "red", ["VISUAL"], "plain"),
    TextCase("rỉ ối từ đêm qua đến giờ vẫn còn", "red", ["FLUID"], "plain"),
    TextCase("ngày nào cũng khóc, không muốn sống nữa", "red", ["SELF_HARM"], "self_harm"),
    # vàng
    TextCase("hơi sốt nhẹ", "yellow", ["FEVER"], "plain"),
    TextCase("tiểu buốt 2 hôm nay", "yellow", ["DYSURIA"], "plain"),
    TextCase("ngứa tay chân về đêm", "yellow", ["ITCH"], "plain"),
    TextCase("mặt sưng nhẹ buổi sáng", "yellow", ["FACE_EDEMA"], "plain"),
    TextCase("nôn nhiều không ăn uống được", "yellow", ["HYPEREMESIS"], "plain"),
    TextCase("hơi đau bụng", "yellow", ["ABD_PAIN"], "plain"),
    TextCase("hôm qua ra máu ít giờ hết rồi", "yellow", ["BLEEDING"], "past"),
    TextCase("nhan chat, tay hoi sung", "yellow", ["FACE_EDEMA"], "no_diacritics"),
    TextCase("tieu rat", "yellow", ["DYSURIA"], "no_diacritics"),
    # Thời điểm quá khứ không chứng minh đã hết: giữ mối lo chưa rõ hiện trạng (khớp tests/redteam/form_cases.yaml).
    TextCase("tuần trước có sốt", "yellow", ["FEVER"], "past"),
    # xanh
    TextCase("không ra máu, bé đạp đều", "green", [], "negation"),
    TextCase("chưa thấy đau gì", "green", [], "negation"),
    TextCase("không còn ra huyết nữa", "green", [], "negation"),
    TextCase("hôm qua đau đầu giờ hết", "green", [], "past"),
    TextCase("mẹ chồng bị cao huyết áp", "green", [], "other_person"),
    TextCase("con lớn bị sốt", "green", [], "other_person"),
    TextCase("chồng em bị sốt mấy hôm", "green", [], "other_person"),
    TextCase("em khỏe, bé đạp nhiều", "green", [], "plain"),
    TextCase("cảm ơn bác sĩ", "green", [], "off_topic"),
    TextCase("hôm nay trời mưa to quá", "green", [], "off_topic"),
    TextCase("be dap nhieu, khong ra mau", "green", [], "no_diacritics"),
    TextCase("", "green", [], "empty"),
]


def make_redteam() -> list[dict]:
    extra = [
        ("không ra máu", "green", [], "negation"),
        ("chưa thấy đau", "green", [], "negation"),
        ("khong sot, khong dau bung", "green", [], "negation"),
        ("hết ra máu rồi, chỉ hơi mệt", "green", [], "negation"),
        ("tuan truoc co sot, gio khoe", "green", [], "past"),
        ("mẹ em bị tiểu đường", "green", [], "other_person"),
        ("chị gái em bị ra máu khi mang thai", "green", [], "other_person"),
        ("em hay suy nghĩ, thấy cuộc sống chẳng thiết sống", "red", ["SELF_HARM"], "self_harm"),
        ("ước gì ngủ luôn không dậy nữa", "red", ["SELF_HARM"], "self_harm"),
        ("muon chet cho xong", "red", ["SELF_HARM"], "self_harm"),
        ("Sáng nay em đi chợ, về thấy mệt. Chiều thì bụng gò cứng liên tục, cứ 10 phút một lần.",
         "red", ["CONTRACTIONS"], "long"),
        ("em đọc trên mạng thấy nói về tiền sản giật nên hơi lo, em vẫn bình thường", "green", [], "off_topic"),
        ("nhin mo, dau dau", "red", ["VISUAL", "HEADACHE"], "no_diacritics"),
        ("thấy hồng hồng khi đi vệ sinh", "red", ["BLEEDING"], "plain"),
    ]
    cases = []
    for c in FREE_TEXTS:
        cases.append({"text": c.text, "ga_weeks": 30, "expected_level": c.level,
                      "expected_concepts": c.concepts, "group": c.group})
    for text, lvl, concepts, group in extra:
        cases.append({"text": text, "ga_weeks": 30, "expected_level": lvl,
                      "expected_concepts": concepts, "group": group})
    cases.append({"text": "bụng gò cứng từng cơn", "ga_weeks": 38, "expected_level": "yellow",
                  "expected_concepts": ["CONTRACTIONS"], "group": "term"})
    cases.append({"text": "bụng gò cứng từng cơn", "ga_weeks": 33, "expected_level": "red",
                  "expected_concepts": ["CONTRACTIONS"], "group": "preterm"})
    return cases


STRUCT_RED = ["C1", "C2", "C3", "C6", "C8", "DYSPNEA", "CALF"]
STRUCT_YELLOW = ["C4", "C5", "C7", "ITCH", "NAUSEA", "MISSED_MED"]


RISK_MODULES = {"HTN": "M-THA", "PREECLAMPSIA": "M-THA", "GDM": "M-DTD"}


def eligible_forms(p: Pat, d: date, sched: dict, anchor: date) -> tuple[list[str], tuple[int, int] | None]:
    """Form hợp lệ với giai đoạn của thai phụ tại ngày d (mốc lấy từ rules.yaml → forms)."""
    if p.delivery and d >= p.delivery:
        if p.delivery >= anchor:  # patients.csv chỉ ghi ngày sinh < anchor → bỏ ca biên
            return [], None
        pp_days = (d - p.delivery).days
        marks = sorted(sched["postpartum_days"].items(), key=lambda kv: kv[1])
        out = []
        for i, (code, day) in enumerate(marks):
            nxt = marks[i + 1][1] if i + 1 < len(marks) else day + 14
            if day <= pp_days < nxt:
                out.append(code)
        return out, None
    ga = ga_on(p, d)
    if ga is None or ga[0] < 0:
        return [], None
    out = [c for c, w in sched["windows"].items() if tuple(w["from"]) <= ga <= tuple(w["to"])]
    out += sorted({RISK_MODULES[r] for r in p.risks if r in RISK_MODULES})
    return out, ga


def make_form_responses(rng: random.Random, pats: list[Pat], forms_cfg: dict, anchor: date, n: int,
                        sched: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    preg = [p for p in pats if p.pregnant]
    rows, labels = [], []
    while len(rows) < n:
        p = rng.choice(preg)
        submitted = datetime.combine(anchor - timedelta(days=rng.randint(0, 29)),
                                     time(rng.randint(6, 22), rng.randint(0, 59)))
        codes, ga = eligible_forms(p, submitted.date(), sched, anchor)
        if not codes:
            continue
        code = rng.choice(codes)
        qs = forms_cfg["forms"][code]["questions"]
        answers: dict[str, object] = {}
        for q in qs:
            qt = forms_cfg["questions"][q]["type"]
            answers[q] = "Không" if qt == "yesno" else ""
        scenario = rng.choices(["green", "yellow", "red"], [60, 25, 15])[0]
        level = "green"
        group = "structured"
        # chỉ số
        if "BP_AM_SYS" in qs:
            sys_v, dia_v = rng.randint(105, 134), rng.randint(65, 84)
            if scenario == "yellow" and rng.random() < 0.6:
                sys_v, dia_v = rng.randint(140, 155), rng.randint(90, 105)
                level = "yellow"
            elif scenario == "red" and rng.random() < 0.6:
                sys_v, dia_v = rng.randint(160, 185), rng.randint(100, 120)
                level = "red"
            answers.update({"BP_AM_SYS": sys_v, "BP_AM_DIA": dia_v,
                            "BP_PM_SYS": max(90, sys_v - rng.randint(0, 6)), "BP_PM_DIA": max(55, dia_v - rng.randint(0, 4))})
        if "GLU_FAST" in qs:
            g = (round(rng.uniform(4.0, 5.1), 1), round(rng.uniform(5.5, 7.5), 1), round(rng.uniform(5.0, 6.5), 1))
            if scenario in ("yellow", "red") and rng.random() < 0.6:
                g = (round(rng.uniform(5.4, 7.0), 1), g[1], g[2])
                level = max(level, "yellow", key=["green", "yellow", "red"].index)
            answers.update({"GLU_FAST": g[0], "GLU_1H": g[1], "GLU_2H": g[2]})
        if "WEIGHT" in qs:
            answers["WEIGHT"] = round(rng.uniform(55, 75), 1)
        # câu có/không
        if level == "green" or rng.random() < 0.5:
            if scenario == "red":
                cand = [q for q in qs if q in STRUCT_RED]
                if cand:
                    answers[rng.choice(cand)] = "Có"
                    level = "red"
            elif scenario == "yellow":
                cand = [q for q in qs if q in STRUCT_YELLOW]
                if cand:
                    answers[rng.choice(cand)] = "Có"
                    level = max(level, "yellow", key=["green", "yellow", "red"].index)
        if forms_cfg["forms"][code].get("epds"):
            items = {f"E{i}": rng.choice([0, 0, 1]) for i in range(1, 11)}
            items["E10"] = 0
            if scenario == "red" and level != "red":
                items["E10"] = rng.choice([1, 2])
                level = "red"
            elif scenario == "yellow" and level == "green":
                for k in list(items)[:9]:
                    items[k] = rng.choice([2, 3])
                level = "yellow"
            answers.update(items)
        # phần tự viết
        text = ""
        if rng.random() < 0.6:
            pool = [c for c in FREE_TEXTS if (c.level == scenario or (c.level == "green" and scenario != "green"))]
            if level == scenario:
                pool = [c for c in FREE_TEXTS if c.level in ("green",) or c.level == scenario]
            c = rng.choice(pool)
            text = c.text
            if c.level != "green":
                level = max(level, c.level, key=["green", "yellow", "red"].index)
            group = c.group
        token = "".join(rng.choice(string.ascii_letters + string.digits + "-_") for _ in range(22))
        rows.append({"token": token, "form_code": code, "submitted_at": submitted.strftime("%Y-%m-%d %H:%M"),
                     **answers, "free_text": text})
        labels.append({"token": token, "pid": p.pid, "form_code": code, "expected_level": level,
                       "group": group, "ga_weeks": ga[0] if ga else ""})
    return pd.DataFrame(rows), pd.DataFrame(labels)


def make_vitals(rng: random.Random, pats: list[Pat], anchor: date) -> pd.DataFrame:
    rows = []
    for p in pats:
        if not p.pregnant or p.delivery is None or p.delivery <= anchor:
            continue
        ga = ga_on(p, anchor)
        if not ga or ga[0] < 20:
            continue
        if "HTN" in p.risks:
            rising = rng.random() < 0.3
            base_s, base_d = rng.randint(118, 132), rng.randint(74, 84)
            days = 28
            for i in range(days * 2):
                d = anchor - timedelta(days=days) + timedelta(days=i // 2)
                ts = datetime.combine(d, time(7, 0) if i % 2 == 0 else time(20, 0))
                drift = (i / (days * 2)) * rng.uniform(22, 30) if rising and i > days else 0.0
                rows.append({"pid": p.pid, "measured_at": ts, "kind": "bp_sys",
                             "value": round(base_s + drift + rng.gauss(0, 3)), "pattern": "rising" if rising else "stable"})
                rows.append({"pid": p.pid, "measured_at": ts, "kind": "bp_dia",
                             "value": round(base_d + drift * 0.5 + rng.gauss(0, 2)), "pattern": "rising" if rising else "stable"})
        if "GDM" in p.risks:
            for i in range(10):
                d = anchor - timedelta(days=30 - i * 3)
                ts = datetime.combine(d, time(6, 30))
                rows.append({"pid": p.pid, "measured_at": ts, "kind": "glu_fast",
                             "value": round(rng.uniform(4.3, 5.6), 1), "pattern": "gdm"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="số ngày lịch sử (có danh sách khách đến)")
    ap.add_argument("--per-day", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--future-days", type=int, default=42)
    ap.add_argument("--patients", type=int, default=300, help="số thai phụ giả")
    ap.add_argument("--gyn-patients", type=int, default=150, help="số khách khám phụ khoa giả")
    ap.add_argument("--forms", type=int, default=400)
    ap.add_argument("--anchor", type=str, default=None, help="ngày 'hôm nay' (YYYY-MM-DD)")
    ap.add_argument("--bad-phone-rate", type=float, default=0.01,
                    help="tỉ lệ khách có SĐT lỗi (để thử cảnh báo E06); 0 = mọi khách đều có SĐT hợp lệ")
    ap.add_argument("--note-rate", type=float, default=0.45,
                    help="tỉ lệ lịch có ghi chú; 1.0 = như file HIS thật (mọi lịch đều có ghi chú)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "synthetic")
    ap.add_argument("--golden", type=Path, default=None,
                    help="thư mục đáp án (mặc định <out>/golden; tests/golden chỉ khi sinh lại bộ cố định cho test)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    anchor = date.fromisoformat(args.anchor) if args.anchor else datetime.now(TZ).date()
    start = anchor - timedelta(days=args.days)
    end = anchor + timedelta(days=args.future_days)
    out = args.out
    golden = args.golden or out / "golden"
    out.mkdir(parents=True, exist_ok=True)
    golden.mkdir(parents=True, exist_ok=True)
    (ROOT / "tests" / "redteam").mkdir(parents=True, exist_ok=True)
    # Xóa file theo ngày của lần sinh trước để không lẫn hai thế hệ dữ liệu.
    for folder, patterns in ((out, ["his_export_*.xlsx", "his_oh_*.xlsx", "arrivals_*.xlsx"]),
                             (golden, ["his_day_*.xlsx", "expected_arrivals_*.csv", "worldsms_expected_*.xlsx"])):
        for pat in patterns:
            for f in folder.glob(pat):
                f.unlink()

    pats = make_patients(rng, args.patients, args.gyn_patients, start)
    # ~1% số điện thoại lỗi để thử BAD_PHONE (--bad-phone-rate 0 → mọi khách đều có SĐT hợp lệ)
    for p in rng.sample(pats, len(pats) // 100):
        bad = rng.choice(["09123", "0900 12", "không có", "1234567890123"])
        if args.bad_phone_rate > 0:
            p.phone = bad
    visits = simulate_visits(rng, pats, start, end, args.per_day)
    n_shift, n_fail = assign_doctors(visits, args.seed)  # 8 bác sĩ, 07:00–17:00, không trùng giờ
    print(f"Xếp {len(DOCTOR_NAMES)} bác sĩ không trùng giờ (07:00–17:00): dời {n_shift} ca"
          + (f", {n_fail} ca KHÔNG xếp được – giảm --per-day" if n_fail else ""))
    for v in visits:
        pick_note(rng, v, merged_info(v)["start"], args.note_rate)

    # bỏ hẹn
    ns_count: dict[str, int] = {}
    logits = []
    for v in visits:
        logits.append(noshow_logit(v, ns_count.get(v.pat.pid, 0)))
    b0 = -3.2
    for _ in range(40):
        mean_p = sum(1 / (1 + math.exp(-(b0 + x))) for x in logits) / len(logits)
        b0 += 0.5 * (math.log(0.08 / 0.92) - math.log(mean_p / (1 - mean_p)))
    arrivals = []
    all_rows = []
    for v in visits:
        prior = ns_count.get(v.pat.pid, 0)
        prob = 1 / (1 + math.exp(-(b0 + noshow_logit(v, prior))))
        noshow = rng.random() < prob if v.day < anchor else None
        if noshow:
            ns_count[v.pat.pid] = prior + 1
        elif noshow is False:
            ci = add_min(min(r["start_time"] for r in v.rows), rng.randint(-25, 15))
            arrivals.append({"pid": v.pat.pid, "visit_date": v.day.isoformat(), "checkin_time": fmt_hhmm(ci)})
        for r in v.rows:
            all_rows.append({**r, "noshow": "" if noshow is None else int(noshow), "first_visit": int(v.first_visit)})

    # patients.csv
    pd.DataFrame([{
        "pid": p.pid, "full_name": p.full_name, "phone": p.phone, "nationality": p.nationality,
        "pregnant": int(p.pregnant), "edd": p.edd.isoformat() if p.edd else "",
        "delivery_date": p.delivery.isoformat() if (p.delivery and p.delivery < anchor) else "",
        "risk_flags": ",".join(p.risks), "insulin": int(p.insulin),
    } for p in pats]).to_csv(out / "patients.csv", index=False, encoding="utf-8")

    appts_df = pd.DataFrame(all_rows)
    appts_df["appt_date"] = appts_df["appt_date"].map(lambda d: d.isoformat())
    appts_df["start_time"] = appts_df["start_time"].map(fmt_hhmm)
    appts_df["end_time"] = appts_df["end_time"].map(fmt_hhmm)
    appts_df["booked_at"] = appts_df["booked_at"].map(lambda d: d.strftime("%Y-%m-%d %H:%M"))
    appts_df.to_csv(out / "appointments_all.csv", index=False, encoding="utf-8")
    arr_df = pd.DataFrame(arrivals)
    arr_df.to_csv(out / "arrivals_all.csv", index=False, encoding="utf-8")
    yesterday = anchor - timedelta(days=1)
    write_xlsx(arr_df[arr_df["visit_date"] == yesterday.isoformat()], out / f"arrivals_{yesterday.isoformat()}.xlsx")

    # file HIS 3 ngày tới + golden 2 ngày
    by_day: dict[date, list[Visit]] = {}
    for v in visits:
        by_day.setdefault(v.day, []).append(v)
    fewshot_pool: list[tuple[str, dict]] = []
    answers: dict[str, dict] = {}
    for v in visits:
        if v.note_t is None:
            continue
        ans = note_answer(v)
        answers.setdefault(v.note, ans)
        masked = v.note.replace(v.pat.given, "[TÊN]") if "{given}" in v.note_t.text else None
        if masked:
            answers.setdefault(masked, ans)
        fewshot_pool.append((v.note, ans))

    for k in range(1, 4):
        d = anchor + timedelta(days=k)
        vs = by_day.get(d, [])
        rows = [r for v in vs for r in v.rows]
        rng.shuffle(rows)
        his = to_his_frame(rows)
        write_xlsx(his, out / f"his_export_{d.isoformat()}.xlsx")
        oh = to_oh_frame(rows)
        out.joinpath(f"his_oh_{d.isoformat()}.xlsx").parent.mkdir(parents=True, exist_ok=True)
        oh.to_excel(out / f"his_oh_{d.isoformat()}.xlsx", index=False)  # giữ kiểu ngày-giờ như file OH
        if k <= 2:
            write_xlsx(his, golden / f"his_day_{d.isoformat()}.xlsx")
            exp = [expected_for(v) for v in vs if v.rows[0]["department"] == DEPT]
            exp_df = pd.DataFrame(exp).sort_values(["start_time", "pid"])
            exp_df.to_csv(golden / f"expected_arrivals_{d.isoformat()}.csv", index=False, encoding="utf-8")
            # file WorldSMS mong đợi (chế độ quy tắc; ca cần xác nhận được điều dưỡng xác nhận = giờ đúng)
            for nat_key, sel in (("VN", exp_df["nationality"] == "Việt Nam"), ("NN", exp_df["nationality"] != "Việt Nam")):
                sub = exp_df[sel]
                pmap = {p.pid: p for p in pats}
                ws = []
                for _, e in sub.iterrows():
                    p = pmap[e["pid"]]
                    if not (len(p.phone) == 10 and p.phone.isdigit()):
                        continue  # số lỗi không xuất
                    arrival = e["arrival_rule"] if e["status_rule"] == "auto" else e["arrival_true"]
                    ws.append({"Số điện thoại": p.phone, "P": p.full_name, "P1": p.pid, "P2": e["start_time"],
                               "P3": f"{d.day:02d}", "P4": f"{d.month:02d}", "P5": f"{d.year}", "P6": arrival})
                write_xlsx(pd.DataFrame(ws, columns=["Số điện thoại", "P", "P1", "P2", "P3", "P4", "P5", "P6"]),
                           golden / f"worldsms_expected_{nat_key}_{d.isoformat()}.xlsx")

    # 3 file lỗi cố ý
    d1 = anchor + timedelta(days=1)
    base = to_his_frame([r for v in by_day.get(d1, []) for r in v.rows][:80])
    swapped = base.copy()
    swapped[HIS_COLS["start_time"]] = swapped[HIS_COLS["end_time"]]
    write_xlsx(swapped, out / "his_error_swapped_cols.xlsx")
    write_xlsx(base.drop(columns=[HIS_COLS["phone"]]), out / "his_error_missing_col.xlsx")
    bad = base.copy()
    bad.loc[0:4, HIS_COLS["start_time"]] = "05:00"
    bad.loc[5:8, HIS_COLS["start_time"]] = "22:30"
    bad.loc[5:8, HIS_COLS["end_time"]] = "22:45"
    bad.loc[9:12, HIS_COLS["end_time"]] = "07:00"
    bad.loc[9:12, HIS_COLS["start_time"]] = "09:00"
    write_xlsx(bad, out / "his_error_bad_time.xlsx")
    for name in ("his_error_swapped_cols", "his_error_missing_col", "his_error_bad_time"):
        (golden / f"{name}.xlsx").write_bytes((out / f"{name}.xlsx").read_bytes())
    (golden / "error_files_date.txt").write_text(d1.isoformat(), encoding="utf-8")

    # đáp án ghi chú
    pd.DataFrame([{"note": k, "answer": json.dumps(v, ensure_ascii=False)} for k, v in answers.items()]).to_csv(
        golden / "notes_answers.csv", index=False, encoding="utf-8")
    eval_rows = []
    for v in visits:
        if v.day > anchor or v.rows[0]["department"] != DEPT:
            continue
        e = expected_for(v)
        e["phone"] = v.rows[0]["phone"]
        e["full_name"] = v.pat.full_name
        e["doctor"] = v.rows[0]["doctor"]
        eval_rows.append(e)
    ev = pd.DataFrame(eval_rows)
    # mẫu theo phân bố tự nhiên (tỷ lệ có ghi chú như dữ liệu thật giả lập)
    sample = ev.sample(min(600, len(ev)), random_state=args.seed)
    sample.drop(columns=["full_name", "phone"]).to_csv(golden / "notes_eval.csv", index=False, encoding="utf-8")

    # few-shot: 18 ví dụ đa dạng (một ví dụ mỗi mẫu); KHÔNG trùng ghi chú trong bộ đánh giá (tránh rò rỉ).
    eval_notes = set(sample["note"].dropna())
    shots, seen_t = [], set()
    rng.shuffle(fewshot_pool)
    for text, ans in fewshot_pool:
        key = (ans["explicit_arrival_time"] is not None, ans["minutes_early"] is not None, ans["ga_weeks"] is not None,
               bool(ans["risk_flags"]), ans["asks_confirmation"], ans["needs_fasting"])
        if key in seen_t or "[" in text or text in eval_notes:
            continue
        seen_t.add(key)
        shots.append({"note": text, "output": ans})
        if len(shots) >= 18:
            break
    with open(golden / "notes_fewshot.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump({"examples": shots}, f, allow_unicode=True, sort_keys=False)

    # form
    with open(ROOT / "config" / "forms.yaml", encoding="utf-8") as f:
        forms_cfg = yaml.safe_load(f)
    with open(ROOT / "config" / "rules.yaml", encoding="utf-8") as f:
        sched = yaml.safe_load(f)["forms"]
    fr, fl = make_form_responses(rng, pats, forms_cfg, anchor, args.forms, sched)
    fr.to_csv(out / "form_responses.csv", index=False, encoding="utf-8")
    fl.to_csv(golden / "form_expected.csv", index=False, encoding="utf-8")
    fl.to_csv(out / "form_expected.csv", index=False, encoding="utf-8")  # seed_demo đọc cùng bộ với form_responses.csv
    redteam = ROOT / "tests" / "redteam" / "form_cases.yaml"
    if not redteam.exists():  # bộ red-team đã có thì đã được chỉnh tay (thêm ca, ghi chú) – không ghi đè
        with open(redteam, "w", encoding="utf-8") as f:
            yaml.safe_dump({"cases": make_redteam()}, f, allow_unicode=True, sort_keys=False)

    make_vitals(rng, pats, anchor).to_csv(out / "vitals.csv", index=False, encoding="utf-8")
    (out / "anchor.txt").write_text(anchor.isoformat(), encoding="utf-8")

    n_rows = len(appts_df)
    n_days = (end - start).days
    print(f"Mốc 'hôm nay': {anchor}  |  {len(pats)} bệnh nhân giả ({args.patients} thai phụ)")
    print(f"Lịch hẹn: {n_rows} dòng / {n_days} ngày (~{n_rows / n_days:.0f}/ngày); khách đến: {len(arr_df)}")
    hist = appts_df[appts_df["noshow"] != ""]
    print(f"Tỷ lệ bỏ hẹn lịch sử: {hist['noshow'].astype(int).mean():.1%}")
    print(f"Form: {len(fr)} câu trả lời; red-team: {len(make_redteam())} ca; mẫu ghi chú: {len(NOTE_TEMPLATES)}")
    print(f"Đầu ra: {out}  |  đáp án: {golden}")


if __name__ == "__main__":
    main()
