"""Tóm tắt lâm sàng gửi AI chọn câu: đã phân loại, không định danh (core/rules/ai_context.py)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from core.ai.masking import mask_text
from core.ai.question_selector import ai_input_preview, build_payload
from core.config import load_forms
from core.rules.ai_context import (
    clinical_summary,
    measurement_labels,
    medication_labels,
    obstetric_labels,
    visit_gap,
)
from core.rules.question_select import Context

TODAY = date(2026, 11, 14)


def _series(values: list[float], days_ago_start: int = 10) -> list[tuple[datetime, float]]:
    t0 = datetime.combine(TODAY - timedelta(days=days_ago_start), datetime.min.time())
    return [(t0 + timedelta(days=i), v) for i, v in enumerate(values)]


def test_bp_category_uses_thresholds(rules):
    th = rules["thresholds"]
    hi = measurement_labels({"bp_sys": _series([th["bp_yellow"]["sys"]]), "bp_dia": _series([80])}, TODAY, rules)
    assert hi and "cao (≥" in hi[0] and "rất cao" not in hi[0]
    vhi = measurement_labels({"bp_sys": _series([th["bp_red"]["sys"]]), "bp_dia": _series([80])}, TODAY, rules)
    assert "rất cao" in vhi[0]
    ok = measurement_labels({"bp_sys": _series([118]), "bp_dia": _series([75])}, TODAY, rules)
    assert "dưới ngưỡng" in ok[0] and "chưa đủ số đo" in ok[0]


def test_bp_trend_rising_detected(rules):
    sys_ = [112, 113, 112, 113, 120, 128, 136]
    dia = [72, 72, 73, 72, 78, 84, 88]
    out = measurement_labels({"bp_sys": _series(sys_), "bp_dia": _series(dia)}, TODAY, rules)
    assert "xu hướng tăng" in out[0]
    flat = measurement_labels({"bp_sys": _series([112] * 7), "bp_dia": _series([72] * 7)}, TODAY, rules)
    assert "ổn định" in flat[0]


def test_old_readings_ignored(rules):
    days = rules["personalization"]["ai_context"]["measurement_days"]
    out = measurement_labels({"bp_sys": _series([150], days + 5), "bp_dia": _series([95], days + 5)}, TODAY, rules)
    assert out == []


def test_glucose_label(rules):
    th = rules["thresholds"]["glucose_yellow"]["fasting"]
    out = measurement_labels({"glu_fast": _series([th - 0.5, th + 0.4])}, TODAY, rules)
    assert out == [f"Đường huyết lúc đói: cao hơn ngưỡng {th} (1/2 lần cao)"]


def test_medications(rules):
    names = rules["personalization"]["ai_context"]["medication_names"]
    out = medication_labels(True, ["Bổ sung sắt theo chỉ định, xét nghiệm lại công thức máu"], rules)
    assert out == [names["INSULIN"], names["IRON"]]
    assert medication_labels(False, ["Không dùng thuốc hạ áp"], rules) == []  # phủ định
    assert medication_labels(False, [], rules) == []


def test_obstetric_history_with_negation(rules):
    names = rules["personalization"]["ai_context"]["obstetric_names"]
    h = {"obstetric_history": ["2023: sẩy thai 9 tuần", "2020: sinh non 34 tuần", "Không tiền sản giật"]}
    out = obstetric_labels(h, rules)
    assert names["PREV_PRETERM"] in out and names["PREV_MISCARRIAGE"] in out
    assert names["PREV_PREECLAMPSIA"] not in out
    assert obstetric_labels({"obstetric_history": ["2022: sinh thường 39 tuần"]}, rules) == []
    assert obstetric_labels(None, rules) == []


def test_visit_gap():
    assert visit_gap(TODAY - timedelta(days=18), TODAY + timedelta(days=4), TODAY) == {
        "days_since_last_visit": 18, "days_to_next_visit": 4}
    assert visit_gap(None, None, TODAY) == {"days_since_last_visit": None, "days_to_next_visit": None}


def test_fields_switch_off(rules):
    cfg = rules["personalization"]["ai_context"]
    old = list(cfg["fields"])
    try:
        cfg["fields"] = ["visit_gap"]
        out = clinical_summary(readings={"bp_sys": _series([150]), "bp_dia": _series([95])}, on_insulin=True,
                               latest_plan=[], history=None, last_visit=None, next_visit=None, today=TODAY,
                               rules=rules)
        assert set(out) == {"days_since_last_visit", "days_to_next_visit"}
    finally:
        cfg["fields"] = old


def test_payload_has_no_identifiers_and_passes_masking(rules):
    clinical = clinical_summary(
        readings={"bp_sys": _series([145, 150, 152]), "bp_dia": _series([92, 95, 96])}, on_insulin=True,
        latest_plan=["Theo dõi huyết áp; xét nghiệm tiền sản giật theo chỉ định"],
        history={"obstetric_history": ["2020: sinh non 34 tuần"], "age": 31, "para": "0101"},
        last_visit=TODAY - timedelta(days=9), next_visit=TODAY + timedelta(days=5), today=TODAY, rules=rules)
    ctx = Context(ga=(33, 2), flags=["HTN"], confirmed=["M-THA"], clinical=clinical)
    forms_cfg = load_forms()
    payload = build_payload(["F5"], ctx, rules, forms_cfg)
    text = json.dumps(payload, ensure_ascii=False)
    # Không có số đo gốc, tuổi, PARA, năm sinh con – chỉ nhãn đã phân loại.
    for raw in ("152", "96", "0101", "2020", '"age"', '"para"'):
        assert raw not in text
    assert mask_text(text, ner=False).ok
    prev = ai_input_preview(["F5"], ctx, rules, forms_cfg)
    assert prev["mask_ok"] and "mandatory" not in prev["fields"] and prev["fields"]["medications"]
