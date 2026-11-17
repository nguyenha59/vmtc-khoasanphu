"""AI được hạ mức phần tự viết (rules.yaml `ai_override`, người dùng chốt 15/11/2026) + lưới dự phòng người thân
(rules.yaml `experiencer`).

Luật bảo vệ: Vàng → Xanh được, Đỏ → tối đa Vàng (không bao giờ Xanh); không hạ khi thiếu phiếu, khi thai phụ cũng bị,
khái niệm khóa, khái niệm thai nhi; không bao giờ hạ dưới mức câu trả lời có cấu trúc (luật khóa combine).
"""

from __future__ import annotations

import pytest

from core.ai.combine import combine
from core.ai.form_reader import lexicon_findings, read_free_text
from core.ai.llm_client import MockClient
from core.config import load_rules
from core.levels import Level

RULES = load_rules()


def _llm(concept: str, evidence: str, *, experiencer: str = "other", temporality: str = "current",
         present: bool = True, inactive_runs: int = 3) -> MockClient:
    """LLM giả: `inactive_runs` lần đầu đọc theo tham số, các lần sau đọc là thai phụ đang bị."""
    calls = {"n": 0}

    def responder(prompt, schema, seed):
        calls["n"] += 1
        mine = calls["n"] > inactive_runs
        return {"findings": [{"concept": concept, "present": True if mine else present,
                              "temporality": "current" if mine else temporality,
                              "experiencer": "patient" if mine else experiencer,
                              "severity": "unknown", "evidence": evidence}],
                "unmapped_concerns": []}

    return MockClient(responder)


def _read(text: str, client: MockClient | None, rules=RULES):
    return read_free_text(text, 30, client, rules, ner=False)


def _final(reading, rule_level: str = "green") -> Level:
    return combine(rule_level, "green", reading.level, ai_ok=reading.ok, ai_confidence=reading.confidence,
                   min_confidence=RULES["arrival"]["ai_confidence_min"]).level


# ---------------------------------------------------------------- lưới dự phòng: người thân theo cấu trúc câu
@pytest.mark.parametrize("text", [
    "bố tôi bị đau bụng", "mẹ tôi bị đau bụng", "ba em bị đau bụng", "má em bị đau bụng", "anh tôi bị đau bụng",
    "chị tôi bị đau bụng", "dì em bị đau bụng", "đồng nghiệp em bị đau bụng", "bạn của tôi bị đau bụng",
    "Ban cua toi bi dau bung", "bạn thân em bị đau bụng", "chị ruột của mình bị đau bụng", "thằng em trai bị đau bụng",
    "bo toi bi dau bung",
])
def test_relatives_are_other(text):
    assert lexicon_findings(text, rules=RULES)[0]["experiencer"] == "other"


@pytest.mark.parametrize("text", [
    "em bị đau bụng", "mẹ tôi bảo tôi bị đau bụng", "chồng em thấy em đau bụng",
    "còn em bị đau bụng",   # còn ≠ con
    "mà em bị đau bụng",    # mà ≠ má
    "chỉ em bị đau bụng",   # chỉ ≠ chị
    "ma em bi dau bung",    # không dấu: ma có thể là "mà" → không đoán
    "chi em bi dau bung",
    "chị bị đau bụng",      # "chị" đứng một mình có thể là thai phụ tự xưng
    "bạn bị đau bụng", "anh bị sốt", "bỏ em bị đau bụng",
])
def test_ambiguous_or_self_stays_patient(text):
    assert lexicon_findings(text, rules=RULES)[0]["experiencer"] == "patient"


# ---------------------------------------------------------------- AI hạ mức
def test_ai_lowers_yellow_to_green():
    r = _read("đứa cháu em bị đau bụng", _llm("ABD_PAIN", "đứa cháu em bị đau bụng"))
    assert r.level == Level.GREEN and _final(r) == Level.GREEN
    assert r.raw["ai_overrides"][0] | {"from": "yellow", "to": "green", "reason": "other_person"} == r.raw["ai_overrides"][0]


def test_ai_lowers_red_only_to_yellow():
    r = _read("đứa cháu em bị khó thở", _llm("DYSPNEA", "đứa cháu em bị khó thở"))
    assert r.level == Level.YELLOW and _final(r) == Level.YELLOW
    f = next(x for x in r.findings if x.concept == "DYSPNEA")
    assert f.level == "yellow" and "override" in f.source


def test_ai_past_resolved_lowers():
    r = _read("hồi tháng 3 em bị khó thở", _llm("DYSPNEA", "hồi tháng 3 em bị khó thở", experiencer="patient",
                                                temporality="past_resolved"))
    assert r.raw["ai_overrides"][0]["reason"] == "past_resolved" and r.level == Level.YELLOW


@pytest.mark.parametrize("text", [
    "Hôm qua em nhìn mờ",
    "Hôm qua em nhìn mờ, sáng nay hết thuốc",
    "Hôm qua em nhìn mờ, giờ hết thuốc",
    "Hôm qua em nhìn mờ, đã hết thuốc rồi",
    "Hôm qua em nhìn mờ, đã đi khám rồi",
    "Hôm qua em nhìn mờ, chưa hết",
    "Hom qua em nhin mo, sang nay het thuoc",
    "Từ hôm qua đến giờ vẫn nhìn mờ",
    "Hồi tháng 3 bắt đầu nhìn mờ đến giờ vẫn nhìn mờ",
])
def test_unanimous_ai_cannot_invent_recovery_from_time_or_medicine(text):
    evidence = text.split(",")[0]
    r = _read(text, _llm("VISUAL", evidence, experiencer="patient", temporality="past_resolved"))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


@pytest.mark.parametrize("text", [
    "Em vẫn chưa hết khó thở",
    "Em vẫn không hết khó thở",
    "Em không ăn được vì khó thở",
    "Em không đi lại được vì khó thở",
])
def test_unanimous_ai_cannot_negate_persistence_or_functional_difficulty(text):
    r = _read(text, _llm("DYSPNEA", "khó thở", experiencer="patient", present=False))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_unanimous_ai_can_support_a_coordinated_sensory_denial():
    text = "chả thấy chấm sáng hay nhìn đôi gì"
    r = _read(text, _llm("VISUAL", text, experiencer="patient", present=False))
    assert r.level == Level.YELLOW and r.raw["ai_overrides"][0]["reason"] == "negated"


def test_ai_negation_lowers_when_text_negates():
    """Danh mục không hiểu "đâu có" là phủ định; AI hiểu và câu có từ phủ định → được hạ (đỏ → vàng)."""
    r = _read("em đâu có khó thở", _llm("DYSPNEA", "em đâu có khó thở", experiencer="patient", present=False))
    assert r.raw["ai_overrides"][0]["reason"] == "negated" and r.level == Level.YELLOW


@pytest.mark.parametrize("text,kwargs", [
    ("sáng nay em bị khó thở", {"experiencer": "patient", "present": False}),             # không có từ phủ định
    ("sáng nay em bị khó thở", {"experiencer": "patient", "temporality": "past_resolved"}),  # không có dấu hiệu đã qua
    ("sáng nay em bị khó thở", {"experiencer": "other"}),                                 # không có chủ ngữ khác
    ("em không sốt, khó thở", {"experiencer": "patient", "present": False}),              # phủ định ở mệnh đề khác
])
def test_ai_reading_without_textual_support_is_ignored(text, kwargs):
    evidence = text.split(", ")[-1]
    r = _read(text, _llm("DYSPNEA", evidence, **kwargs))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_needs_every_vote():
    r = _read("đứa cháu em bị khó thở", _llm("DYSPNEA", "đứa cháu em bị khó thở", inactive_runs=2))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_patient_also_affected_keeps_level():
    r = _read("đứa cháu em bị khó thở, em cũng thấy khó thở", _llm("DYSPNEA", "đứa cháu em bị khó thở"))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_locked_concept_never_lowered():
    r = _read("đứa cháu em nói muốn chết", _llm("SELF_HARM", "muốn chết"))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_fetal_concepts_excluded():
    assert "REDUCED_FM" in RULES["ai_override"]["exclude_concepts"]
    r = _read("bé đạp ít hơn hôm qua", _llm("REDUCED_FM", "bé đạp ít hơn hôm qua"))
    assert "ai_overrides" not in r.raw


def test_evidence_must_be_in_text():
    r = _read("đứa cháu em bị khó thở", _llm("DYSPNEA", "cháu hàng xóm khó thở"))
    assert "ai_overrides" not in r.raw and r.level == Level.RED


def test_no_ai_keeps_lexicon_level():
    r = _read("đứa cháu em bị đau bụng", None)
    assert r.level == Level.YELLOW and _final(r) >= Level.YELLOW


def test_disabled_by_config():
    rules = load_rules()
    rules.data["ai_override"]["enabled"] = False
    r = _read("đứa cháu em bị đau bụng", _llm("ABD_PAIN", "đứa cháu em bị đau bụng"), rules)
    assert r.level == Level.YELLOW and "ai_overrides" not in r.raw


def test_structured_answer_level_still_locked():
    """AI hạ phần tự viết nhưng mức câu trả lời có cấu trúc (quy tắc) vẫn giữ: luật khóa combine."""
    r = _read("đứa cháu em bị đau bụng", _llm("ABD_PAIN", "đứa cháu em bị đau bụng"))
    assert _final(r, rule_level="yellow") == Level.YELLOW
    assert _final(r, rule_level="red") == Level.RED


def test_config_is_doctor_approved():
    meta = RULES["rule_meta"]
    for key in ("ai_override.enabled", "ai_override.floor_if_red", "experiencer.relatives_standalone",
                "experiencer.relatives_need_owner"):
        assert meta[key]["requires_doctor"] is True
    assert RULES["ai_override"]["floor_if_red"] != "green"


# ---------------------------------------------------------------- sửa sau rà soát code (15/11/2026)
def test_support_words_match_with_correct_diacritics():
    """"đâu" ≠ "đau", "đỡ" ≠ "đỏ", "hồi" ≠ "hơi": dấu hiệu để AI hạ mức phải đúng dấu."""
    from core.ai.reader.text import has_words

    sup = RULES["ai_override"]["support"]
    assert not has_words("em đau bụng ra máu", sup["negated"], sup["plain_ambiguous"])
    assert not has_words("em ra máu đỏ tươi", sup["past_resolved"], sup["plain_ambiguous"])
    assert not has_words("em hơi ra máu", sup["past_resolved"], sup["plain_ambiguous"])
    assert not has_words("em dau bung ra mau", sup["negated"], sup["plain_ambiguous"])  # không dấu: bỏ "đâu"
    assert has_words("em đâu có khó thở", sup["negated"], sup["plain_ambiguous"])
    assert has_words("hom qua kho tho, gio het roi", sup["past_resolved"], sup["plain_ambiguous"])


def test_ai_misread_negation_with_dau_bung_keeps_red():
    r = _read("em đau bụng, khó thở", _llm("DYSPNEA", "khó thở", experiencer="patient", present=False))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_both_affected_is_patient():
    assert lexicon_findings("chồng cả em bị ra máu", rules=RULES)[0]["experiencer"] == "patient"


def test_red_override_goes_to_human_review():
    r = _read("đứa cháu em bị khó thở", _llm("DYSPNEA", "đứa cháu em bị khó thở"))
    c = combine("green", "green", r.level, ai_ok=r.ok, ai_confidence=r.confidence,
                min_confidence=RULES["arrival"]["ai_confidence_min"])
    assert c.level == Level.YELLOW and c.needs_human
    assert any("gọi xác minh" in x for x in r.conflicts)


def test_floor_cannot_be_configured_to_green():
    rules = load_rules()
    rules.data["ai_override"]["floor_if_red"] = "green"
    r = _read("đứa cháu em bị khó thở", _llm("DYSPNEA", "đứa cháu em bị khó thở"), rules)
    assert r.level == Level.YELLOW


def test_past_support_does_not_cross_sentences():
    text = "em khó thở nhiều. hôm qua đi khám rồi"
    r = _read(text, _llm("DYSPNEA", "em khó thở nhiều", experiencer="patient", temporality="past_resolved"))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_short_repeated_quote_is_not_same_place():
    text = "hôm qua khó thở. sáng nay lại khó thở, đã đi khám"
    r = _read(text, _llm("DYSPNEA", "khó thở", experiencer="patient", temporality="past_resolved"))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


def test_relatives_come_from_passed_rules():
    rules = load_rules()
    rules.data["experiencer"]["relatives_need_owner"] = [*rules["experiencer"]["relatives_need_owner"], "cháu"]
    assert lexicon_findings("cháu em bị đau bụng", rules=rules)[0]["experiencer"] == "other"
    assert lexicon_findings("cháu em bị đau bụng", rules=RULES)[0]["experiencer"] == "patient"


def test_negated_override_is_explained_on_alert():
    from api.routers.alerts import _why

    r = _read("em đâu có khó thở", _llm("DYSPNEA", "em đâu có khó thở", experiencer="patient", present=False))
    f = next(x for x in r.findings if x.concept == "DYSPNEA")
    assert f.level == "yellow" and not f.present
    rows = _why({"rule": [], "trend": [], "ai": [{**vars(f), "name": "Khó thở"}]}, {}, {}, RULES)
    assert rows and rows[0]["source"].startswith("AI hạ mức")


@pytest.mark.parametrize("before,ok", [
    ("đứa cháu em bị ", True), ("anh nhà ", True), ("ông xã em dạo này hay ", True), ("Ban cua toi bi ", True),
    ("cô giáo của con em bị ", True), ("chị họ em bị ", True),
    ("mẹ em lo vì em ", False),          # chủ ngữ của triệu chứng là "em"
    ("leo mấy bậc cầu thang là ", False),  # không có người
    ("không nằm được vì ", False), ("e bị ", False), ("em có ", False),  # "có" ≠ "cô"
    ("mẹ chồng đưa em đi khám vì em ", False), ("co em bi ", False),    # không dấu: "co" có thể là "có"
])
def test_other_person_support_needs_person_right_before(before, ok):
    from core.ai.reader.reconcile import _person_subject_before

    assert _person_subject_before(before, RULES["ai_override"]["support"]) is ok


def test_ai_cannot_move_patient_symptom_to_worried_relative():
    r = _read("mẹ em lo vì em khó thở", _llm("DYSPNEA", "mẹ em lo vì em khó thở"))
    assert r.level == Level.RED and "ai_overrides" not in r.raw


# ---------------------------------------------------------------- sửa lỗi đã phát hiện (15/11/2026, tối)
@pytest.mark.parametrize("text,exp", [
    ("ra máu là của chị em", "other"), ("đau đầu là của má em", "other"), ("ra mau la cua chong em", "other"),
    ("ra máu âm đạo là của chị gái em", "other"),
    ("ra máu cửa mình", "patient"), ("ra máu là của em", "patient"), ("em ra máu là của chị em", "patient"),
    ("ra máu là chuyện của chị em mình", "patient"),
])
def test_postposed_subject_with_diacritics(text, exp):
    assert lexicon_findings(text, rules=RULES)[0]["experiencer"] == exp


@pytest.mark.parametrize("concept,level", [("CONVULSION", "yellow"), ("SYNCOPE", "yellow"), ("BLEEDING", "yellow"),
                                           ("ABD_PAIN", "green"), ("FEVER", "green")])
def test_resolved_danger_sign_stays_yellow(concept, level):
    """P14: "hồi nhỏ bị động kinh, nay không lên cơn" – AI ghi không có + đã qua. Dấu hiệu nguy hiểm → vẫn Vàng."""
    from core.ai.form_reader import finding_level

    f = {"concept": concept, "present": False, "temporality": "past_resolved", "experiencer": "patient",
         "severity": "unknown"}
    assert finding_level(f, 30, RULES).code == level
