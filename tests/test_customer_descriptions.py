"""Synthetic customer wording checked against the configured alert policy."""

from pathlib import Path

import pytest
import yaml

from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms

CASES = yaml.safe_load((Path(__file__).parent / "golden" / "customer_descriptions.yaml").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_customer_description_level(case, rules):
    result = assess_answers("F5", case.get("answers", {}), case["text"], case.get("ga_weeks", 30),
                            rules, load_forms(), client=MockClient(), history={}, ner=False)
    assert result.level.code == case["expected"], (case["text"], result.reasons, result.reading.raw)
    if case.get("needs_human"):
        assert result.combined.needs_human
