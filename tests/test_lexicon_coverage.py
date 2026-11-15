"""Lớp danh mục (chạy cả khi AI tắt) phải bắt các cách nói thường gặp – kiểm tra toàn bộ phần AI 14/11/2026."""

from __future__ import annotations

import pytest

from core.ai.form_reader import lexicon_findings


def _present(text: str, rules) -> set[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return {(f["concept"], f["experiencer"]) for f in lexicon_findings(text, rules=rules)
            if f["present"] and f["temporality"] != "past_resolved"}


@pytest.mark.parametrize("text", [
    "em ra ít máu", "em có ra chút máu", "em ra một chút máu", "em ra tí máu", "em bị ra nhiều máu",
    "quần lót dính máu", "em thấy máu ở quần lót", "em ra dịch hồng", "em ra mot chut mau",
])
def test_bleeding_with_quantity_words_is_caught(text, rules):
    assert ("BLEEDING", "patient") in _present(text, rules)


@pytest.mark.parametrize("text, concept", [
    ("mặt em sưng", "FACE_EDEMA"), ("em ra một ít nước", "FLUID"), ("em nhìn hơi mờ", "VISUAL"),
])
def test_other_symptoms_with_fillers(text, concept, rules):
    assert (concept, "patient") in _present(text, rules)


@pytest.mark.parametrize("text", [
    "em không ra máu", "em không thấy ra máu", "em đi xét nghiệm máu", "nhóm máu em là O", "con đạp nhiều lắm",
    "em không bị đau đầu",
])
def test_fillers_do_not_create_false_findings(text, rules):
    assert not _present(text, rules)


def test_other_person_still_other(rules):
    assert _present("chồng em bị ra máu", rules) == {("BLEEDING", "other")}
    assert not _present("chồng em bị ra máu cam", rules)  # máu cam = mũi, lại là người khác


def test_resolved_bleeding_still_counts_yellow(rules):
    from core.ai.form_reader import finding_level

    fs = [f for f in lexicon_findings("hôm qua có ra chút máu hồng, sáng nay hết", rules=rules)
          if f["concept"] == "BLEEDING"]
    assert fs and fs[0]["temporality"] == "past_resolved"
    assert finding_level(fs[0], 30, rules).code == "yellow"  # past_counts_yellow
