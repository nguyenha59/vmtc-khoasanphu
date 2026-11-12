"""Kết quả khám thai: gắn cờ xét nghiệm và sinh DỮ LIỆU GIẢ hợp lý theo tuổi thai, nhóm nguy cơ.

Hàm thuần (đầu vào → đầu ra), không đọc CSDL, để test được. Bảng tham chiếu ở config/clinical_reference.yaml.
Cấu trúc một lần khám theo phiếu khám thai (QĐ 4128/QĐ-BYT): khám toàn thân → khám sản → xét nghiệm → siêu âm
→ chẩn đoán ICD-10 → xử trí. Dữ liệu sinh ra CHỈ để chạy thử, không dùng cho chẩn đoán.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

Reference = dict[str, Any]


def lab_flag(value: float | str, ref: list[Any]) -> str:
    """Cờ như LIS: "H" cao, "L" thấp, "A" bất thường (định tính), "" bình thường."""
    if isinstance(value, str):
        return "" if value in ref else "A"
    lo, hi = ref[0], ref[1]
    if hi is not None and value > hi:
        return "H"
    if lo is not None and value < lo:
        return "L"
    return ""


def _interp(table: dict[int, float], weeks: float) -> float:
    """Trung vị theo tuần (nội suy tuyến tính, chặn hai đầu)."""
    ks = sorted(int(k) for k in table)
    if weeks <= ks[0]:
        return float(table[ks[0]])
    if weeks >= ks[-1]:
        return float(table[ks[-1]])
    lo = max(k for k in ks if k <= weeks)
    hi = min(k for k in ks if k >= weeks)
    if lo == hi:
        return float(table[lo])
    return float(table[lo]) + (float(table[hi]) - float(table[lo])) * (weeks - lo) / (hi - lo)


@dataclass
class Baseline:
    """Đặc điểm ổn định của một thai phụ qua các lần khám (sinh một lần cho mỗi người)."""

    pre_weight: float
    bp_sys: float
    bp_dia: float
    pulse: float
    hb: float
    anemic: bool
    uti_risk: float
    placenta: str
    breech: bool


def make_baseline(rng: random.Random, *, anemic: bool | None = None, uti_risk: float | None = None,
                  pre_weight: float | None = None, breech: bool | None = None) -> Baseline:
    """Đặc điểm nền của một thai phụ. Tham số khác None = ca soạn sẵn ép đặc điểm (thiếu máu, nhiễm khuẩn niệu…)."""
    anemic = rng.random() < 0.15 if anemic is None else anemic
    return Baseline(
        pre_weight=round(rng.uniform(44, 66), 1) if pre_weight is None else pre_weight,
        bp_sys=rng.gauss(108, 7), bp_dia=rng.gauss(68, 5), pulse=rng.gauss(80, 6),
        hb=rng.uniform(100, 107) if anemic else rng.gauss(124, 5), anemic=anemic,
        uti_risk=0.06 if uti_risk is None else uti_risk, placenta=rng.choice(["mặt trước", "mặt sau", "đáy tử cung"]),
        breech=(rng.random() < 0.05) if breech is None else breech,
    )


@dataclass
class History:
    """Những gì đã làm ở các lần khám trước (để không lặp nghiệm pháp / siêu âm cùng mốc)."""

    visits: int = 0
    done: set[str] = field(default_factory=set)
    gdm_confirmed: bool = False


def _lab(ref: Reference, code: str, value: float | str) -> dict[str, Any]:
    d = ref["labs"][code]
    v = round(value, 1) if isinstance(value, float) else value
    return {"code": code, "name": d["name"], "value": v, "unit": d["unit"], "loinc": d["loinc"],
            "ref": d["ref"], "flag": lab_flag(v, d["ref"])}


def _ref_text(ref_range: list[Any]) -> str:
    if isinstance(ref_range[0], str):
        return ref_range[0]
    lo, hi = ref_range[0], ref_range[1]
    return f"< {hi}" if lo is None else f"≥ {lo}" if hi is None else f"{lo}–{hi}"


def ref_text(ref_range: list[Any]) -> str:
    """Khoảng tham chiếu dạng chữ để hiển thị."""
    return _ref_text(ref_range)


def make_visit_result(rng: random.Random, *, ga_days: int | None, postpartum_days: int | None, risks: set[str],
                      base: Baseline, hist: History, ref: Reference) -> dict[str, Any]:
    """Một lần khám. ga_days: tuổi thai (ngày) – None nếu không mang thai; postpartum_days: số ngày sau sinh."""
    weeks = ga_days / 7 if ga_days is not None else None
    twins = "TWINS" in risks
    htn_active = "HTN" in risks and weeks is not None and weeks >= 20
    out: dict[str, Any] = {"labs": [], "ultrasound": None, "obstetric": None, "diagnoses": [], "plan": []}

    # ---- Khám toàn thân
    gain = 0.0
    if weeks is not None:
        gain = min(weeks, 13) / 13 * rng.uniform(0.5, 1.5) + max(weeks - 13, 0) * (0.65 if twins else 0.45)
    elif postpartum_days is not None:
        gain = max(0.0, 8 - postpartum_days * 0.1)
    rise = (min(weeks - 20, 16) / 16) * rng.uniform(18, 32) if htn_active and weeks is not None else 0.0
    sys_ = round(base.bp_sys + rise + rng.gauss(0, 5))
    dia = round(base.bp_dia + rise * 0.65 + rng.gauss(0, 4))
    edema = "Phù nhẹ hai chi dưới" if (htn_active and weeks is not None and weeks >= 28 and rng.random() < 0.5) else "Không phù"
    out["vitals"] = {"bp": f"{sys_}/{dia}", "bp_sys": sys_, "bp_dia": dia,
                     "pulse": round(base.pulse + (5 if weeks and weeks >= 28 else 0) + rng.gauss(0, 4)),
                     "weight": round(base.pre_weight + gain + rng.gauss(0, 0.3), 1), "edema": edema}

    # ---- Khám sản khoa
    if weeks is not None and weeks >= 12:
        ob: dict[str, Any] = {"fhr": f"{round(rng.gauss(145, 7))} lần/phút" + (" · 2 tim thai" if twins else "")}
        cfg = ref["obstetric"]
        if weeks >= cfg["fundal_height_from_week"]:
            tol = cfg["fundal_height_tolerance_cm"]
            ob["fundal_height_cm"] = round(int(weeks) + (3 if twins else 0) + rng.uniform(-tol, tol))
        if weeks >= cfg["presentation_from_week"]:
            ob["presentation"] = "Ngôi mông" if base.breech and weeks >= 34 else ("Ngôi đầu" if weeks >= 34 or rng.random() < 0.8 else "Ngôi mông")
        out["obstetric"] = ob

    # ---- Xét nghiệm (theo lịch config)
    sch = ref["lab_schedule"]
    codes: list[str] = []
    if hist.visits == 0 and weeks is not None:
        codes += sch["first_visit"]
    elif weeks is not None:
        codes += sch["every_visit"]
    if htn_active:
        codes += [c for c in ["PLT", "ALT", "AST", "CREA", "U_PRO"] if c not in codes]
    lo_w, hi_w = sch["ogtt_weeks"]
    if weeks is not None and lo_w <= weeks <= hi_w and "OGTT" not in hist.done:
        codes += ["OGTT_0", "OGTT_1", "OGTT_2"]
        hist.done.add("OGTT")
    hb_lo, hb_hi = sch["hb_recheck_weeks"]
    if weeks is not None and hb_lo <= weeks <= hb_hi and "HB2" not in hist.done:
        codes += [c for c in ["HGB", "PLT"] if c not in codes]
        hist.done.add("HB2")
    c_lo, c_hi = sch["coag_weeks"]
    if weeks is not None and c_lo <= weeks <= c_hi and "COAG" not in hist.done:
        codes += ["PT", "APTT", "FIB"]
        hist.done.add("COAG")
    uti = rng.random() < base.uti_risk
    gdm = "GDM" in risks
    for c in dict.fromkeys(codes):
        v: float | str
        if c == "HGB":
            v = base.hb + rng.gauss(0, 3)
        elif c == "PLT":
            v = rng.gauss(160, 25) if htn_active and weeks is not None and weeks >= 32 else rng.gauss(240, 40)
        elif c == "WBC":
            v = rng.uniform(6, 12)
        elif c == "FERRITIN":
            v = rng.uniform(6, 14) if base.anemic else rng.uniform(25, 90)
        elif c in ("GLU_FAST", "OGTT_0"):
            v = rng.uniform(5.2, 5.8) if gdm else rng.uniform(4.0, 4.8)
        elif c == "OGTT_1":
            v = rng.uniform(9.0, 11.5) if gdm else rng.uniform(6.0, 8.8)
        elif c == "OGTT_2":
            v = rng.uniform(8.6, 10.0) if gdm else rng.uniform(5.0, 7.5)
        elif c == "UREA":
            v = rng.uniform(2.5, 4.5)
        elif c == "CREA":
            v = rng.uniform(45, 70)
        elif c in ("ALT", "AST"):
            v = rng.uniform(10, 30)
        elif c == "PT":
            v = rng.uniform(11, 13)
        elif c == "APTT":
            v = rng.uniform(26, 33)
        elif c == "FIB":
            v = rng.uniform(3.5, 5.5)
        elif c == "U_PRO":
            v = "1+" if htn_active and weeks is not None and weeks >= 28 and rng.random() < 0.35 else "Âm tính"
        elif c == "U_GLU":
            v = "Vết" if gdm and rng.random() < 0.2 else "Âm tính"
        elif c in ("U_LEU", "U_NIT"):
            v = "Dương tính" if uti else "Âm tính"
        else:
            continue
        out["labs"].append(_lab(ref, c, v))
    if any(x["code"].startswith("OGTT") and x["flag"] for x in out["labs"]):
        hist.gdm_confirmed = True

    # ---- Siêu âm (mỗi mốc một lần)
    us = ref["ultrasound"]
    if weeks is not None:
        for scan in us["scans"]:
            if scan["from"] <= weeks <= scan["to"] and scan["kind"] not in hist.done:
                hist.done.add(scan["kind"])
                out["ultrasound"] = _ultrasound(rng, scan["kind"], weeks, ga_days or 0, twins, gdm, base, us)
                break

    # ---- Chẩn đoán ICD-10
    icd = ref["icd10"]
    dx: list[dict[str, str]] = []
    if weeks is not None:
        if htn_active:
            dx.append(icd["HTN"])
        if gdm and hist.gdm_confirmed:
            dx.append(icd["GDM"])
        if twins:
            dx.append(icd["TWINS"])
        if "PREV_CSECTION" in risks:
            dx.append(icd["PREV_CSECTION"])
        if "PRETERM" in risks and 22 <= weeks < 37:
            dx.append(icd["PRETERM"])
        if any(x["code"] == "HGB" and x["flag"] == "L" for x in out["labs"]):
            dx.append(icd["ANEMIA"])
        if uti and any(x["code"] == "U_NIT" for x in out["labs"]):
            dx.append(icd["UTI"])
        # Quy ước ICD-10: Z34 chỉ cho thai BÌNH THƯỜNG; đã có mã chương O (biến chứng / tình trạng thai) thì mã O là
        # chẩn đoán chính và không ghi kèm Z34 / Z35.
        if not dx:
            dx.append(icd["normal_other"])
    elif postpartum_days is not None:
        dx.append(icd["postpartum"])
    else:
        dx.append(icd["gyn"])
    out["diagnoses"] = [{**d, "primary": i == 0} for i, d in enumerate(dx)]

    # ---- Xử trí (chữ cố định, không có liều thuốc)
    plan = ["Tư vấn dấu hiệu nguy hiểm cần đến viện ngay"]
    if any(d["code"] == icd["HTN"]["code"] for d in dx):
        plan.append("Theo dõi huyết áp; xét nghiệm tiền sản giật theo chỉ định")
    if any(d["code"] == icd["GDM"]["code"] for d in dx):
        plan.append("Tư vấn dinh dưỡng, theo dõi đường huyết; phối hợp khám nội tiết")
    if any(d["code"] == icd["ANEMIA"]["code"] for d in dx):
        plan.append("Bổ sung sắt theo chỉ định, xét nghiệm lại công thức máu")
    if any(d["code"] == icd["UTI"]["code"] for d in dx):
        plan.append("Cấy nước tiểu, điều trị theo kết quả")
    out["plan"] = plan
    hist.visits += 1
    return out


def _ultrasound(rng: random.Random, kind: str, weeks: float, ga_days: int, twins: bool, gdm: bool, base: Baseline,
                us: Reference) -> dict[str, Any]:
    noise = lambda: rng.uniform(0.95, 1.05)  # noqa: E731
    meas: list[dict[str, Any]] = []
    if weeks < 14:
        meas.append({"name": "Chiều dài đầu mông (CRL)", "value": round(_interp(us["crl_mm"], weeks) * noise()), "unit": "mm"})
        meas.append({"name": "Độ mờ da gáy (NT)", "value": round(rng.uniform(1.0, 2.4), 1), "unit": "mm"})
    else:
        big = 1.08 if gdm and weeks >= 28 else 1.0
        small = 0.93 if twins and weeks >= 28 else 1.0
        meas += [
            {"name": "Đường kính lưỡng đỉnh (BPD)", "value": round(_interp(us["bpd_mm"], weeks) * noise()), "unit": "mm"},
            {"name": "Chu vi bụng (AC)", "value": round(_interp(us["ac_mm"], weeks) * noise() * big * small), "unit": "mm"},
            {"name": "Chiều dài xương đùi (FL)", "value": round(_interp(us["fl_mm"], weeks) * noise()), "unit": "mm"},
            {"name": "Cân nặng thai ước tính", "value": round(_interp(us["efw_g"], weeks) * rng.uniform(0.92, 1.08) * big * small),
             "unit": "g"},
        ]
        if weeks >= 18:
            meas.append({"name": "Chỉ số ối (AFI)", "value": round(rng.uniform(9, 18), 1), "unit": "cm"})
    w, d = divmod(ga_days, 7)
    count = "Hai thai sống trong tử cung" if twins else "Một thai sống trong tử cung"
    concl = f"{count}, tim thai đều; kích thước tương ứng {w} tuần {d} ngày."
    if weeks >= 18 and weeks < 23:
        concl += " Chưa phát hiện bất thường hình thái trên siêu âm."
    if gdm and weeks >= 28:
        concl += " Thai có xu hướng lớn so với tuổi thai – theo dõi."
    return {"kind": kind, "measurements": meas, "fhr": f"{round(rng.gauss(145, 7))} lần/phút",
            "placenta": f"Bánh rau {base.placenta}" + (", hai bánh rau" if twins else ""), "conclusion": concl}
