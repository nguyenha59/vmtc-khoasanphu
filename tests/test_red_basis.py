"""Khi nào báo ĐỎ (rà soát 15/11/2026): mọi mức đỏ phải có căn cứ (nguồn y khoa) và giải thích được (dữ liệu nào).

Kèm các câu thai phụ hay viết mà trước đây bị đọc nhầm (docs/10 P12) và quyết định P01 / P03 / P04 / P09 của người dùng.
"""

from __future__ import annotations

import pytest

from core.ai.form_reader import read_free_text
from core.assess import assess_answers
from core.config import load_forms
from core.red_basis import basis_codes, explain
from core.rules.triage_rules import RED_ITEMS


def _level(text: str, rules, ga: int = 30) -> str:  # type: ignore[no-untyped-def]
    return read_free_text(text, ga, None, rules, ner=False).level.code


def test_every_red_rule_has_a_source(rules):
    for code in [*RED_ITEMS, "C9", "BP", "BP_AM", "BP_PM", "HYPO", "FEVER_PLUS", "EDEMA_PLUS", "EPDS_10"]:
        assert basis_codes("rule", code, rules), code


def test_every_red_concept_and_combo_has_a_source(rules):
    names = rules["red_basis"]["sources"]
    for code, c in rules["symptom_concepts"].items():
        if c["level"] == "red" or c.get("level_if_severe") == "red":
            codes = basis_codes("concept", code, rules)
            assert codes and all(x in names for x in codes), code
    for c in rules["text_combos"]:
        assert c["basis"] and all(x in names for x in c["basis"]), c["code"]
    assert explain("rule", "TXT_HEADACHE_BP", rules)["basis"]


@pytest.mark.parametrize("text", [
    "tối qua em bị chảy máu cam",               # mũi, không phải âm đạo
    "em bị cảm, ra nước mũi suốt",              # nước mũi
    "em mệt nên nằm im nghỉ cả ngày, bé vẫn đạp đều",  # mẹ nằm nghỉ, không phải thai nằm im
    "ngực em căng tức mấy hôm nay",            # vú căng khi mang thai, không phải đau ngực
    "em hay chảy máu chân răng",
])
def test_common_misreads_are_not_red(text, rules):
    assert _level(text, rules) != "red"


def test_bleeding_then_feeling_is_still_red(rules):
    # "máu cảm thấy…" không được hiểu là "máu cam"
    assert _level("em ra máu cảm thấy rất lo", rules) == "red"


def test_fetus_lying_still_stays_red(rules):
    assert _level("từ chiều tới giờ con nằm im", rules) == "red"


def test_information_read_online_is_yellow_not_green(rules):
    """Chỉ là thông tin đọc được → không đỏ, nhưng vẫn Vàng để gọi xác minh (không bỏ sót)."""
    assert _level("em đọc trên mạng thấy ra máu khi mang thai là nguy hiểm", rules) == "yellow"


@pytest.mark.parametrize("text, level", [
    ("hơi nhức đầu vì thiếu ngủ", "yellow"),
    ("chiều nay em đau đầu nhẹ", "yellow"),
    ("em đau đầu từ hôm qua", "yellow"),         # "qua" không phải từ chỉ mức nặng
    ("đau đầu dữ dội, uống thuốc không đỡ", "red"),
    ("em đau đầu, uống thuốc không đỡ", "red"),  # mức nặng ở mệnh đề kế tiếp
    ("dau dau qua uong thuoc khong do", "red"),
    ("em đau đầu, nhìn mờ", "red"),
])
def test_headache_p01(text, level, rules):
    assert _level(text, rules) == level


def test_headache_with_high_bp_is_red_with_explanation(rules):
    res = assess_answers("M-THA", {"BP_PM_SYS": 145, "BP_PM_DIA": 92}, "chiều nay em đau đầu", 32, rules,
                         load_forms(), client=None, history={}, ner=False)
    assert res.level.code == "red"
    combo = [f for f in res.rule.findings if f.code == "TXT_HEADACHE_BP"]
    assert combo and combo[0].level == "red" and "140/90" in combo[0].reason


def test_edema_plus_headache_in_free_text_is_red(rules):
    res = assess_answers("F5", {}, "mặt em sưng, đau đầu", 30, rules, load_forms(), client=None, history={}, ner=False)
    assert any(f.code == "TXT_EDEMA_HEADACHE" for f in res.rule.findings) and res.level.code == "red"


@pytest.mark.parametrize("text, level", [
    ("sáng nay em bị co giật tay chân một lúc", "red"),
    ("em bị ngất xỉu lúc đứng nấu cơm", "red"),
    ("tức ngực, tim đập nhanh", "red"),
    ("đứng dậy nhanh thì hơi chóng mặt", "yellow"),
    ("đau đầu chút xiu thôi", "yellow"),          # "xiu" (xíu) không được hiểu là "xỉu"
])
def test_new_danger_signs(text, level, rules):
    assert _level(text, rules) == level


def test_bp_reason_names_the_value_that_crossed(rules):
    res = assess_answers("M-THA", {"BP_AM_SYS": 138, "BP_AM_DIA": 90}, "", 32, rules, load_forms(),
                         client=None, history={}, ner=False)
    assert any("tâm trương ≥ 90" in f.reason for f in res.rule.findings)


def _client(concept: str, evidence: str):  # type: ignore[no-untyped-def]
    from core.ai.llm_client import MockClient

    return MockClient(lambda *_: {"findings": [{"concept": concept, "present": True, "experiencer": "patient",
                                                "temporality": "current", "severity": "unknown",
                                                "evidence": evidence}], "unmapped_concerns": []})


def test_ai_only_red_is_capped_yellow(rules):
    """Người dùng chọn 15/11/2026: Đỏ chỉ do AI (không có từ khóa) → Vàng + gọi xác minh."""
    r = read_free_text("mắt em hơi mỏi do nhìn điện thoại", 30, _client("VISUAL", "mắt em hơi mỏi"), rules, ner=False)
    assert r.level.code == "yellow"
    assert any("chỉ AI thấy" in c for c in r.conflicts)


def test_ai_red_with_keyword_support_stays_red(rules):
    r = read_free_text("em nhìn mờ", 30, _client("VISUAL", "em nhìn mờ"), rules, ner=False)
    assert r.level.code == "red"


def test_locked_self_harm_from_ai_only_stays_red(rules):
    text = "dạo này em thấy sống chẳng còn ý nghĩa gì"
    r = read_free_text(text, 30, _client("SELF_HARM", text), rules, ner=False)
    assert r.level.code == "red"
