"""Cách nói thêm theo nguồn y khoa (docs/10 mục 4): dò được, có dạng không dấu, không đổi mức, không thêm khái niệm."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from core.ai.form_reader import lexicon_findings
from core.config import load_rules
from scripts.apply_lexicon_sources import ADDITIONS, patch

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "config" / "rules.yaml"


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    """Bản sao rules.yaml đã vá (file thật có thể đã vá sẵn – patch không thêm trùng)."""
    p = tmp_path_factory.mktemp("lex") / "rules.yaml"
    shutil.copy(RULES, p)
    raw = p.read_bytes()
    text, _ = patch(raw.decode("utf-8").replace("\r\n", "\n"), "0.5.0")
    p.write_text(text, encoding="utf-8", newline="\n")
    return load_rules(p)


def test_patch_only_adds_synonyms(patched):
    before = yaml.safe_load(RULES.read_text(encoding="utf-8"))["symptom_concepts"]
    after = patched["symptom_concepts"]
    assert set(after) == set(before)  # không thêm khái niệm
    for code, c in before.items():
        assert after[code]["level"] == c["level"]  # không đổi mức
        assert after[code]["synonyms"][: len(c["synonyms"])] == c["synonyms"]  # chỉ nối thêm ở cuối
        assert {k: v for k, v in after[code].items() if k != "synonyms"} == {k: v for k, v in c.items() if k != "synonyms"}
    assert patched.rules_version == "0.5.0" or patched.rules_version == load_rules(RULES).rules_version


def test_patch_is_idempotent(patched):
    text = Path(patched.path).read_text(encoding="utf-8")
    again, added = patch(text, "0.5.0")
    assert added == {} and again == text


def test_prompt_changes_only_where_expected(patched):
    """Prompt AI dùng 6 cách nói đầu. Khái niệm vốn < 6 cách nói (CALF) sẽ thấy cách nói mới trong prompt –
    truy vết được vì mọi kết quả AI lưu rules_version. Các khái niệm khác: prompt không đổi."""
    before = yaml.safe_load(RULES.read_text(encoding="utf-8"))["symptom_concepts"]
    changed = {code for code in ADDITIONS
               if patched["symptom_concepts"][code]["synonyms"][:6] != before[code]["synonyms"][:6]}
    assert changed <= {"CALF"}


CASES = [
    ("BLEEDING", "Sáng nay em thấy ra cục máu khá to"),
    ("BLEEDING", "em bi ri mau tu toi qua"),
    ("REDUCED_FM", "Từ chiều đến giờ con không đạp"),
    ("VISUAL", "Em bị nhìn nhòe từ sáng"),
    ("EPIGASTRIC", "Em đau dưới sườn bên phải nhiều"),
    ("FLUID", "Em bị rỉ nước liên tục"),
    ("DYSPNEA", "Em thở dốc khi nằm"),
    ("CALF", "Tối qua thấy chân sưng đỏ bên trái"),
    ("FACE_EDEMA", "Sáng dậy thấy sưng mí mắt"),
    ("HYPEREMESIS", "Mấy hôm nay em ói nhiều quá"),
]


@pytest.mark.parametrize("concept,text", CASES)
def test_new_phrases_detected(patched, concept, text):
    found = {f["concept"] for f in lexicon_findings(text, rules=patched) if f.get("present", True)}
    assert concept in found, (text, found)


def test_negated_new_phrase_not_present(patched):
    fs = [f for f in lexicon_findings("Em không bị rỉ nước gì cả", rules=patched) if f["concept"] == "FLUID"]
    assert not fs or all(not f["present"] for f in fs)
