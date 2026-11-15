"""Default regressions for provider failures, privacy and immutable clinical facts."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_audit_repros_20261114 import (  # noqa: F401
    test_ai02_ner_failure_must_prevent_sending_unmasked_names,
    test_ai02_uppercase_full_name_must_not_be_treated_as_medical_acronym,
    test_ai05_null_provider_content_must_fall_back_instead_of_raising,
    test_ai06_disagreeing_ga_must_not_overwrite_edd_with_confidence_one,
    test_ai09_summary_rewrite_must_not_swap_bp_or_change_alert_level,
    test_ai10_known_red_form_must_be_persisted_before_waiting_for_optional_ai,
    test_ai11_budget_estimate_must_bound_requested_output,
    test_ai11_parallel_spend_updates_must_not_lose_one_call,
)

from core.ai import llm_client
from core.ai.llm_client import LlmError, MockClient, OpenAIClient
from core.ai.masking import mask_text
from core.ai.summary import summarize, template_summary


def test_missing_default_ner_blocks_sending(monkeypatch):
    monkeypatch.setattr("core.ai.masking.default_ner", lambda: None)
    result = mask_text("Nhờ Trần Ngọc Mai gọi giúp")
    assert not result.ok and "NER_UNAVAILABLE" in result.leftovers


@pytest.mark.parametrize("bad", [None, "not-a-list", [None]])
def test_invalid_ner_result_is_a_privacy_failure(bad):
    assert not mask_text("Nhờ chị Mai liên hệ", ner=lambda _text: bad).ok


def test_medical_acronyms_survive_uppercase_ner():
    assert mask_text("ĐTĐTK và THA", ner=lambda _text: ["ĐTĐTK", "THA"]).text == "ĐTĐTK và THA"


def test_budget_reserved_before_parallel_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "ledger.json")
    monkeypatch.setenv("OPENAI_BUDGET_USD", "0.003")
    entered, release = threading.Event(), threading.Event()
    client = OpenAIClient(api_key="synthetic-only")
    calls = []

    def fake_post(_body):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(status_code=200, json=lambda: {
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            "choices": [{"message": {"content": '{"x": 1}'}}],
        })

    monkeypatch.setattr(client, "_post", fake_post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.generate_json, "x", {"type": "object"}, 0, 1)
        assert entered.wait(5)
        try:
            with pytest.raises(llm_client.BudgetExceeded):
                client.generate_json("x", {"type": "object"}, 0, 2)
        finally:
            release.set()
        assert first.result(timeout=5) == {"x": 1}
    assert len(calls) == 1 and not llm_client._read_spend().get("pending")


def test_spend_is_atomic_across_processes(tmp_path):
    ledger = str(tmp_path / "ledger.json")
    code = (
        "from pathlib import Path; from core.ai import llm_client as c; import sys; "
        "c.SPEND_FILE=Path(sys.argv[1]); "
        "[c._add_spend(0.01,1,1) for _ in range(10)]"
    )
    children = [subprocess.Popen([sys.executable, "-c", code, ledger], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(2)]
    for child in children:
        _stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stderr.decode(errors="replace")
    data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    today = data["days"][llm_client._today()]
    assert today["usd"] == pytest.approx(0.2) and today["calls"] == 20


def test_corrupt_spend_fails_closed(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"days": {"broken": {"usd": NaN}}}', encoding="utf-8")
    monkeypatch.setattr(llm_client, "SPEND_FILE", ledger)
    client = OpenAIClient(api_key="synthetic-only")
    monkeypatch.setattr(client, "_post", lambda _body: pytest.fail("Must not send when accounting is corrupt"))
    with pytest.raises(LlmError):
        client.generate_json("x", {"type": "object"}, 0, 1)


@pytest.mark.parametrize("choice", [
    {"finish_reason": "length", "message": {"content": '{"x":1}'}},
    {"message": {"content": None, "refusal": "synthetic refusal"}},
    {"message": []},
])
def test_refusal_incomplete_or_malformed_content_is_llm_error(monkeypatch, tmp_path, choice):
    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "ledger.json")
    client = OpenAIClient(api_key="synthetic-only")
    monkeypatch.setattr(client, "_post", lambda _body: SimpleNamespace(status_code=200,
                        json=lambda: {"choices": [choice]}))
    with pytest.raises(LlmError):
        client.generate_json("x", {"type": "object"}, 0, 1)


def test_summary_accepts_only_preserved_clinical_sentences(rules):
    values = copy.deepcopy(rules.data)
    values["llm"]["summary_llm_rewrite"] = True
    enabled = type(rules)(data=values, path=rules.path)
    data = {"ga": "30+0", "level": "red", "vitals": {"bp": ("160/90", "")}, "reasons": ["Huyết áp cao"]}
    base = template_summary(data)
    assert summarize(data, enabled, MockClient(lambda *_: {"text": base}), ner=False) == (base, "llm")
    assert summarize(data, enabled, MockClient(lambda *_: {"text": "Mức XANH."}), ner=False) == (base, "template")
