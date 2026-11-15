"""Phương án A (form_cap): hạ ĐỎ → VÀNG khi lớp danh mục chắc chắn là người khác / đã hết / bé đạp bình thường.

Luật bảo vệ: không bao giờ về XANH, không hạ khái niệm locked, không hạ khi thai phụ cũng bị / được kể lại,
không đụng luật khóa combine (mức quy tắc form).
"""

from __future__ import annotations

import pytest

from core.ai.form_reader import _vote_unmapped, read_free_text
from core.ai.llm_client import MockClient
from core.assess import combine
from core.levels import Level


def _llm(
    concept: str,
    evidence: str,
    experiencer: str = "patient",
    temporality: str = "current",
    unmapped: list[str] | None = None,
) -> MockClient:
    """LLM giả: luôn (3/3 lần) báo khái niệm của thai phụ, đang có."""

    def responder(prompt, schema, seed):
        return {
            "findings": [
                {
                    "concept": concept,
                    "present": True,
                    "temporality": temporality,
                    "experiencer": experiencer,
                    "severity": "unknown",
                    "evidence": evidence,
                }
            ],
            "unmapped_concerns": unmapped or [],
        }

    return MockClient(responder)


def _finding(reading, concept):
    return next(f for f in reading.findings if f.concept == concept)


# ---------------------------------------------------------------- được hạ (đỏ → vàng, không xanh)
@pytest.mark.parametrize(
    "text,concept,evidence",
    [
        ("chồng em khó thở do hen", "DYSPNEA", "chồng em khó thở do hen"),
        ("mẹ em hay bị hoa mắt", "VISUAL", "mẹ em hay bị hoa mắt"),
        ("chị gái em bị ra máu nhiều", "BLEEDING", "chị gái em bị ra máu nhiều"),
        ("hôm qua ra máu ít giờ hết rồi", "BLEEDING", "hôm qua ra máu ít"),
        ("em khỏe, bé đạp nhiều", "CONTRACTIONS", "bé đạp nhiều"),
        ("be dap nhieu, khong ra mau", "REDUCED_FM", "be dap nhieu"),
        ("bé đạp mạnh lắm", "CONTRACTIONS", "bé đạp mạnh lắm"),
    ],
)
def test_capped_to_yellow_never_green(rules, text, concept, evidence):
    r = read_free_text(text, 30, _llm(concept, evidence), rules, ner=False)
    assert r.level == Level.YELLOW
    assert _finding(r, concept).level == "yellow"
    assert any("hạ red→yellow" in c for c in r.conflicts)


@pytest.mark.parametrize("text", [
    "em gái em mới sinh bị ra máu nhiều",  # có cụm chen giữa ("mới sinh") → không chắc là người khác
    "Chồng đưa đi khám vì ra máu",  # bỏ chủ ngữ – thai phụ bị (rà soát 11/11/2026)
])
def test_not_sure_other_person_is_not_capped(rules, text):
    """Không chắc là người khác → không hạ (sai về phía an toàn)."""
    r = read_free_text(text, 30, _llm("BLEEDING", text), rules, ner=False)
    assert r.level == Level.RED and not any("hạ red→yellow" in c for c in r.conflicts)


def test_cap_disabled_keeps_red(rules):
    data = {**rules.data, "form_cap": {**rules.data["form_cap"], "enabled": False}}
    off = type(rules)(data=data, path=rules.path)
    r = read_free_text(
        "chồng em khó thở do hen", 30, _llm("DYSPNEA", "chồng em khó thở do hen"), off, ner=False
    )
    assert r.level == Level.RED


# ---------------------------------------------------------------- KHÔNG được hạ
@pytest.mark.parametrize(
    "text,concept,evidence",
    [
        # thai phụ bị thật
        ("em khó thở", "DYSPNEA", "em khó thở"),
        ("bé ít đạp từ hôm qua đến giờ", "REDUCED_FM", "bé ít đạp"),
        # được người khác kể lại / thai phụ cũng bị
        ("mẹ em bảo em hay hoa mắt", "VISUAL", "hay hoa mắt"),
        ("mẹ em hay hoa mắt, em cũng bị vậy", "VISUAL", "em cũng bị vậy"),
        ("chồng em thấy em thở hụt hơi", "DYSPNEA", "thở hụt hơi"),
        # bé đạp nhiều rồi thay đổi → không phải bình thường
        ("bé đạp nhiều rồi nằm im luôn", "REDUCED_FM", "bé đạp nhiều rồi nằm im luôn"),
        (
            "bé đạp nhiều rồi tự nhiên bụng cứng",
            "CONTRACTIONS",
            "bé đạp nhiều rồi tự nhiên bụng cứng",
        ),
    ],
)
def test_not_capped(rules, text, concept, evidence):
    r = read_free_text(text, 30, _llm(concept, evidence), rules, ner=False)
    assert r.level == Level.RED, (text, r.conflicts)


def test_missing_evidence_requires_review_instead_of_fabricating_red(rules):
    """Unverified model output must not create a red finding; preserve review floor."""
    reading = read_free_text("em khỏe, bé đạp nhiều", 30, _llm("CONTRACTIONS", ""), rules, ner=False)
    result = combine(Level.GREEN, Level.GREEN, reading.level, ai_ok=reading.ok,
                     ai_confidence=reading.confidence, min_confidence=rules["arrival"]["ai_confidence_min"])
    assert reading.level < Level.RED and result.level == Level.YELLOW and result.needs_human


def test_locked_concept_never_capped(rules):
    r = read_free_text(
        "tháng trước có lúc muốn chết, giờ đỡ rồi",
        30,
        _llm("SELF_HARM", "tháng trước có lúc muốn chết"),
        rules,
        ner=False,
    )
    assert r.level == Level.RED


def test_rule_layer_lock_untouched(rules):
    """Form có câu trả lời đỏ (lớp quy tắc) → dù AI bị hạ, mức gộp vẫn đỏ."""
    r = read_free_text(
        "chồng em khó thở do hen", 30, _llm("DYSPNEA", "chồng em khó thở do hen"), rules, ner=False
    )
    got = combine(
        Level.RED,
        Level.GREEN,
        r.level,
        ai_ok=r.ok,
        ai_confidence=r.confidence,
        min_confidence=rules["arrival"]["ai_confidence_min"],
    )
    assert got.level == Level.RED


# ---------------------------------------------------------------- mối lo ngoài danh mục là câu bình thường
@pytest.mark.parametrize(
    "concern,text",
    [
        ("bé đạp nhiều", "em khỏe, bé đạp nhiều"),
        ("bé đạp đều", "khong ra mau, be dap deu"),
        ("con đạp liên tục", "con đạp liên tục, em vui"),
        ("giờ khỏi rồi", "hôm qua sốt, giờ khỏi rồi"),
        ("giờ đỡ rồi", "tháng trước đau lưng, giờ đỡ rồi"),
        (
            "tiền sản giật",
            "em đọc trên mạng thấy nói về tiền sản giật nên hơi lo, em vẫn bình thường",
        ),
        ("tiền sản giật", "chị dâu em bị tiền sản giật"),
    ],
)
def test_benign_unmapped_dropped(rules, concern, text):
    assert _vote_unmapped([[concern]] * 3, text, rules) == []


@pytest.mark.parametrize(
    "concern,text",
    [
        ("bé đạp nhiều rồi tự nhiên ít hẳn", "bé đạp nhiều rồi tự nhiên ít hẳn"),
        ("đau lưng nhiều", "em bị đau lưng nhiều, khó ngủ"),
    ],
)
def test_real_unmapped_kept(rules, concern, text):
    assert _vote_unmapped([[concern]] * 3, text, rules)
