"""Kết quả khám giả (core/clinical.py): hợp lý theo tuổi thai, nhóm nguy cơ; cờ xét nghiệm đúng; không có dữ liệu nhạy cảm."""

from __future__ import annotations

import random

import pytest

from core.clinical import History, lab_flag, make_baseline, make_visit_result
from core.config import load_clinical_reference

REF = load_clinical_reference()


def run(risks: set[str], weeks: list[int], seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    base, hist = make_baseline(rng), History()
    return [make_visit_result(rng, ga_days=w * 7, postpartum_days=None, risks=risks, base=base, hist=hist, ref=REF)
            for w in weeks]


def codes(v: dict) -> set[str]:
    return {x["code"] for x in v["labs"]}


@pytest.mark.parametrize("value, ref, flag", [
    (105.0, [110, 150], "L"), (160.0, [110, 150], "H"), (120.0, [110, 150], ""),
    (5.3, [None, 5.0], "H"), ("Âm tính", ["Âm tính"], ""), ("1+", ["Âm tính"], "A"),
])
def test_lab_flag(value, ref, flag):
    assert lab_flag(value, ref) == flag


def test_ogtt_thresholds_match_iadpsg():
    """Cờ NPDN phải khớp ngưỡng chẩn đoán ĐTĐ thai kỳ 5,1 / 10,0 / 8,5 mmol/L (IADPSG, QĐ 1470)."""
    labs = REF["labs"]
    assert lab_flag(5.1, labs["OGTT_0"]["ref"]) == "H" and lab_flag(5.0, labs["OGTT_0"]["ref"]) == ""
    assert lab_flag(10.0, labs["OGTT_1"]["ref"]) == "H" and lab_flag(9.9, labs["OGTT_1"]["ref"]) == ""
    assert lab_flag(8.5, labs["OGTT_2"]["ref"]) == "H" and lab_flag(8.4, labs["OGTT_2"]["ref"]) == ""


def test_first_visit_has_baseline_labs_and_no_infectious_tests():
    v = run(set(), [10])[0]
    assert {"HGB", "GLU_FAST", "U_PRO"} <= codes(v)
    names = " ".join(x["name"] for x in v["labs"]).lower()
    assert not any(k in names for k in ("hiv", "hbsag", "giang mai")), "nhóm xét nghiệm nhạy cảm không được sinh"


def test_ogtt_once_in_window_and_gdm_confirmed():
    visits = run({"GDM"}, [12, 20, 25, 27, 32])
    with_ogtt = [i for i, v in enumerate(visits) if "OGTT_2" in codes(v)]
    assert with_ogtt == [2], "NPDN đúng một lần, trong cửa sổ 24–28 tuần"
    assert any(x["flag"] for x in visits[2]["labs"] if x["code"].startswith("OGTT"))
    assert any(d["code"] == "O24.4" for d in visits[3]["diagnoses"])
    assert not any(d["code"] == "O24.4" for d in visits[1]["diagnoses"]), "chưa làm NPDN thì chưa chẩn đoán ĐTĐ thai kỳ"


def test_normal_pregnancy_codes_and_physiology():
    visits = run(set(), [10, 16, 22, 30, 36])
    for w, v in zip([10, 16, 22, 30, 36], visits, strict=True):
        dx = [d["code"] for d in v["diagnoses"]]
        assert v["diagnoses"][0]["primary"]
        if any(c.startswith("O") for c in dx):  # có biến chứng (thiếu máu, nhiễm khuẩn niệu…): mã O chính, không Z34/Z35
            assert dx[0].startswith("O") and not any(c.startswith(("Z34", "Z35")) for c in dx)
        else:
            assert dx == ["Z34.8"]
        if w >= 20:
            assert abs(v["obstetric"]["fundal_height_cm"] - w) <= REF["obstetric"]["fundal_height_tolerance_cm"] + 1
        if w >= 12:
            fhr = int(v["obstetric"]["fhr"].split()[0])
            assert 110 <= fhr <= 170
    w = [v["vitals"]["weight"] for v in visits]
    assert w == sorted(w) or w[-1] > w[0], "cân nặng tăng theo thai kỳ"


def test_htn_raises_bp_after_20_weeks_and_codes_o13():
    visits = run({"HTN"}, [12, 22, 30, 36], seed=3)
    assert visits[3]["vitals"]["bp_sys"] > visits[0]["vitals"]["bp_sys"]
    assert any(d["code"] == "O13" for d in visits[3]["diagnoses"])
    assert not any(d["code"] == "O13" for d in visits[0]["diagnoses"]), "trước 20 tuần không mã tăng huyết áp do thai"
    assert {"PLT", "ALT", "CREA"} <= codes(visits[2]), "nhóm tăng huyết áp: thêm xét nghiệm tiền sản giật"


def test_ultrasound_once_per_window_with_biometry():
    visits = run(set(), [12, 13, 20, 21, 30])
    kinds = [v["ultrasound"]["kind"] if v["ultrasound"] else None for v in visits]
    assert kinds[0] and kinds[1] is None, "siêu âm quý 1 chỉ một lần"
    assert kinds[2] and kinds[3] is None
    efw = next(m for m in visits[4]["ultrasound"]["measurements"] if "Cân nặng" in m["name"])["value"]
    assert 1300 <= efw <= 1900, "cân nặng thai 30 tuần quanh trung vị ~1.560 g"


def test_postpartum_and_gyn_codes():
    rng = random.Random(0)
    base = make_baseline(rng)
    pp = make_visit_result(rng, ga_days=None, postpartum_days=7, risks=set(), base=base, hist=History(), ref=REF)
    gyn = make_visit_result(rng, ga_days=None, postpartum_days=None, risks=set(), base=base, hist=History(), ref=REF)
    assert pp["diagnoses"][0]["code"] == "Z39.2" and gyn["diagnoses"][0]["code"] == "Z01.4"
    assert pp["labs"] == [] and pp["obstetric"] is None


def test_deterministic():
    assert run({"GDM"}, [10, 26], seed=7) == run({"GDM"}, [10, 26], seed=7)
