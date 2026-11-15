"""Hybrid provider policy, fallback isolation and per-result provenance; no network or paid API."""

from __future__ import annotations

from datetime import time

import pytest

from core.ai import llm_client
from core.ai.consistency import run_votes
from core.ai.llm_client import (
    BudgetExceeded,
    HybridClient,
    LlmError,
    MockClient,
    can_send_external,
    local_only_client,
    make_client,
    make_client_safe,
    result_provider,
)
from core.ai.masking import MaskResult
from core.ai.question_selector import ai_scores, selector_client
from core.config import llm_provider, load_forms, load_rules
from core.db import now
from core.rules.merge import MergedAppt
from core.rules.question_select import Context

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
PROMPT = "synthetic prompt for provider routing"


@pytest.fixture(autouse=True)
def isolated_provider_env(monkeypatch):
    # conftest pins both providers to mock; .env may specify the developer's local 3B.
    # None of those machine preferences may change this public-default policy test.
    for name in ("LLM_PROVIDER", "QUESTION_SELECTOR_PROVIDER", "OLLAMA_MODEL", "OPENAI_MODEL",
                 "APP_ENV", "SYNTHETIC_DATA_ONLY", "OLLAMA_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")


def allow_synthetic_primary(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key-not-a-real-credential")


def simple_client(name, responder=None):
    return MockClient(responder or (lambda *_args: {"ok": True}), model=name)


@pytest.fixture()
def fake_factories(monkeypatch):
    class Local(MockClient):
        def __init__(self, model, **kwargs):
            super().__init__(lambda *_args: {"ok": True}, model=f"ollama:{model}")
            self.model = model
            self.allow_cloud_eval = bool(kwargs.get("allow_cloud_eval", False))

        def available(self):
            return True

    class Primary(MockClient):
        def __init__(self, model=None, **_kwargs):
            super().__init__(lambda *_args: {"ok": True}, model=f"openai:{model or 'test'}")

        def available(self):
            return True

    monkeypatch.setattr(llm_client, "OllamaClient", Local)
    monkeypatch.setattr(llm_client, "OpenAIClient", Primary)
    return Local, Primary


def test_public_clone_defaults_to_local_7b(fake_factories):
    local_type, _primary_type = fake_factories
    client = make_client(load_rules())
    assert isinstance(client, local_type)
    assert client.model == "qwen2.5:7b-instruct"
    assert not client.allow_cloud_eval and not can_send_external(client)


def test_local_default_does_not_upload_merely_because_a_key_exists(monkeypatch, fake_factories):
    allow_synthetic_primary(monkeypatch)
    local_type, _primary_type = fake_factories
    client = make_client(load_rules())
    assert isinstance(client, local_type) and not can_send_external(client)
    assert client.generate_json(PROMPT, SCHEMA, 0, 1) == {"ok": True}
    assert client.calls == 1 and result_provider(client).startswith("ollama:")


def test_provider_without_rules_or_env_defaults_to_local():
    assert llm_provider() == "ollama"


@pytest.mark.parametrize("key,synthetic,environment,uses_primary", [
    ("synthetic-key", "1", "development", True),
    ("", "1", "development", False),
    ("synthetic-key", "", "development", False),
    ("synthetic-key", "0", "development", False),
    ("synthetic-key", "1", "production", False),
    ("synthetic-key", "1", " Production ", False),
])
def test_hybrid_factory_cloud_eligibility_matrix(monkeypatch, fake_factories, key, synthetic,
                                                environment, uses_primary):
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", synthetic)
    monkeypatch.setenv("APP_ENV", environment)
    client = make_client(load_rules())
    assert isinstance(client, HybridClient)
    assert can_send_external(client) is uses_primary
    assert client.generate_json(PROMPT, SCHEMA, 0, 1) == {"ok": True}
    primary_calls = client.primary.calls if client.primary is not None else 0
    assert primary_calls == int(uses_primary)
    assert client.local.calls == int(not uses_primary)
    assert result_provider(client).startswith("openai:" if uses_primary else "ollama:")


@pytest.mark.parametrize("policy_change", [{"APP_ENV": "production"}, {"SYNTHETIC_DATA_ONLY": "0"}])
def test_hybrid_rechecks_privacy_policy_before_every_call(monkeypatch, policy_change):
    allow_synthetic_primary(monkeypatch)
    primary, local = simple_client("openai:test"), simple_client("ollama:test")
    client = HybridClient(local=local, primary=primary)
    client.generate_json(PROMPT, SCHEMA, 0, 1)
    for key, value in policy_change.items():
        monkeypatch.setenv(key, value)
    client.generate_json(PROMPT, SCHEMA, 0, 2)
    assert primary.calls == 1 and local.calls == 1
    assert not can_send_external(client) and result_provider(client) == "ollama:test"


@pytest.mark.parametrize("failure", [BudgetExceeded("synthetic budget"), LlmError("429 insufficient_quota"),
                                     LlmError("401 invalid_api_key"), LlmError("403 forbidden")])
def test_primary_failure_is_not_retried_by_later_votes(monkeypatch, failure):
    allow_synthetic_primary(monkeypatch)

    def fail(*_args):
        raise failure

    primary, local = simple_client("openai:test", fail), simple_client("ollama:test")
    client = HybridClient(local=local, primary=primary)
    votes = run_votes(client, PROMPT, SCHEMA, 5, 0)
    assert primary.calls == 1 and local.calls == 5
    assert len(votes.outputs) == 5 and not votes.errors
    assert votes.models == ["ollama:test"] * 5
    assert not can_send_external(client) and local_only_client(client) is local


def test_mixed_provider_votes_keep_per_result_provenance(monkeypatch):
    allow_synthetic_primary(monkeypatch)
    successful = 0

    def primary_response(*_args):
        nonlocal successful
        successful += 1
        if successful > 2:
            raise LlmError("429 insufficient_quota")
        return {"ok": True}

    primary = simple_client("openai:test", primary_response)
    local = simple_client("ollama:test")
    votes = run_votes(HybridClient(local=local, primary=primary), PROMPT, SCHEMA, 5, 0)
    assert primary.calls == 3 and local.calls == 3
    assert votes.models == ["openai:test", "openai:test", "ollama:test", "ollama:test", "ollama:test"]
    assert len(votes.outputs) == len(votes.models) == 5 and not votes.errors


def test_both_providers_unavailable_returns_vote_errors_without_retrying_primary(monkeypatch):
    allow_synthetic_primary(monkeypatch)

    def fail(*_args):
        raise LlmError("synthetic unavailable")

    primary, local = simple_client("openai:test", fail), simple_client("ollama:test", fail)
    votes = run_votes(HybridClient(local=local, primary=primary), PROMPT, SCHEMA, 3, 0)
    assert primary.calls == 1 and local.calls == 3
    assert not votes.outputs and not votes.models and len(votes.errors) == 3


def test_fallback_logs_do_not_echo_primary_error_secrets(monkeypatch, caplog):
    allow_synthetic_primary(monkeypatch)
    secret = "synthetic-api-secret-must-not-appear"
    private_prompt = "synthetic-private-text-must-not-appear"

    def fail(*_args):
        raise LlmError(f"401 invalid_api_key {secret}; request={private_prompt}")

    client = HybridClient(local=simple_client("ollama:test"), primary=simple_client("openai:test", fail))
    votes = run_votes(client, private_prompt, SCHEMA, 3, 0)
    assert secret not in caplog.text and private_prompt not in caplog.text
    assert not votes.errors and votes.models == ["ollama:test"] * 3


def appointment(note):
    return MergedAppt(pid="HYBRID-TEST", appt_date=now().date(), start_time=time(9), end_time=None,
                      services=["Khám thai"], note=note, full_name="Giả Lập", phone="0900000000",
                      phone_raw="", nationality="Việt Nam", department="", doctor="", booking_source="",
                      booked_at=None)


def test_sensitive_note_uses_local_without_trying_hybrid_primary(monkeypatch, rules):
    from core.pipeline import plan_appointment

    allow_synthetic_primary(monkeypatch)

    def forbidden(*_args):
        raise AssertionError("sensitive note reached primary")

    primary = simple_client("openai:test", forbidden)
    local = MockClient(model="ollama:test")
    client = HybridClient(local=local, primary=primary)
    plan = plan_appointment(appointment("song thai, HIV dương tính, giữ bí mật"), rules, client, [], ner=False)
    assert primary.calls == 0 and local.calls > 0
    assert plan.extraction is not None and plan.extraction.model == "ollama:test"
    assert set(plan.extraction.raw["models"]) == {"ollama:test"}
    assert any("API bên ngoài" in detail for detail in plan.arrival.details)


def test_name_only_legacy_external_client_is_still_protected(monkeypatch, rules):
    from core.pipeline import plan_appointment

    def forbidden(*_args):
        raise AssertionError("sensitive note reached legacy external client")

    primary = simple_client("openai:test", forbidden)
    assert can_send_external(primary) and local_only_client(primary) is None
    plan = plan_appointment(appointment("song thai, HIV dương tính, giữ bí mật"), rules, primary, [], ner=False)
    assert primary.calls == 0 and plan.extraction is None and plan.ai_status == "sensitive"


def test_mask_failure_blocks_both_hybrid_branches(monkeypatch, rules):
    from core import pipeline

    allow_synthetic_primary(monkeypatch)
    primary, local = simple_client("openai:test"), simple_client("ollama:test")
    monkeypatch.setattr(pipeline, "mask_text", lambda *_a, **_k: MaskResult(text="unverified", ok=False, leftovers=["NER"]))
    plan = pipeline.plan_appointment(appointment("song thai cần theo dõi"), rules,
                                     HybridClient(local=local, primary=primary), [], ner=False)
    assert primary.calls == local.calls == 0
    assert plan.ai_status == "mask_failed" and "PII_LEFT" in plan.decision.reasons


@pytest.mark.parametrize("consumer", ["form", "selector"])
def test_fallback_provider_is_preserved_in_task_raw_metadata(monkeypatch, rules, consumer):
    from core.ai.form_reader import read_free_text

    allow_synthetic_primary(monkeypatch)

    def fail(*_args):
        raise LlmError("429 insufficient_quota")

    primary, local = simple_client("openai:test", fail), MockClient(model="ollama:test")
    client = HybridClient(local=local, primary=primary)
    if consumer == "form":
        result = read_free_text("Em thấy khỏe", 30, client, rules, ner=False)
        assert result.ok
        expected_calls = rules["llm"]["consistency_runs_form"]
    else:
        result = ai_scores(["F5"], Context(ga=(30, 0), flags=["TWINS"], confirmed=[]),
                           rules, load_forms(), client, ner=False)
        assert result.ok
        expected_calls = rules.get("personalization.selection.ai_runs")
    assert primary.calls == 1 and local.calls == expected_calls
    assert result.raw["models"] == ["ollama:test"] * expected_calls


def test_selector_inherits_main_mode_but_respects_explicit_overrides(monkeypatch, fake_factories):
    allow_synthetic_primary(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    rules = load_rules()
    main, selector = make_client(rules), selector_client(rules)
    assert isinstance(main, HybridClient) and isinstance(selector, HybridClient)
    assert main.name == selector.name
    monkeypatch.setenv("QUESTION_SELECTOR_PROVIDER", "rules")
    assert selector_client(rules) is None
    monkeypatch.setenv("QUESTION_SELECTOR_PROVIDER", "mock")
    assert type(selector_client(rules)) is MockClient
    monkeypatch.setenv("QUESTION_SELECTOR_PROVIDER", "openai")
    monkeypatch.setenv("APP_ENV", "production")
    assert selector_client(rules) is None


def test_local_model_override_applies_to_main_fallback_and_inherited_selector(monkeypatch, fake_factories):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b-instruct")
    rules = load_rules()
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert make_client(rules).model == "qwen2.5:3b-instruct"
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    assert make_client(rules).local.model == "qwen2.5:3b-instruct"
    assert selector_client(rules).local.model == "qwen2.5:3b-instruct"


def test_safe_factory_allows_production_hybrid_as_local_only(monkeypatch, fake_factories):
    allow_synthetic_primary(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("APP_ENV", "production")
    client, why = make_client_safe(load_rules())
    assert why is None and isinstance(client, HybridClient)
    assert not can_send_external(client)
    assert client.generate_json(PROMPT, SCHEMA, 0, 1) == {"ok": True}
    assert client.local.calls == 1 and client.primary is None


def test_factory_created_hybrid_rechecks_production_after_creation(monkeypatch, fake_factories):
    allow_synthetic_primary(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    client = make_client(load_rules())
    assert isinstance(client, HybridClient)
    client.generate_json(PROMPT, SCHEMA, 0, 1)
    assert client.primary.calls == 1 and client.local.calls == 0
    monkeypatch.setenv("APP_ENV", "production")
    client.generate_json(PROMPT, SCHEMA, 0, 2)
    assert client.primary.calls == 1 and client.local.calls == 1
    assert result_provider(client).startswith("ollama:") and not can_send_external(client)


def test_service_free_text_is_masked_before_hybrid_primary(monkeypatch, rules):
    from core.pipeline import plan_appointment

    allow_synthetic_primary(monkeypatch)
    prompts = []

    def observe(prompt, *_args):
        prompts.append(prompt)
        from core.ai.note_extractor import heuristic_extract

        return heuristic_extract("song thai")

    appt = appointment("song thai cần theo dõi")
    appt.services = ["Khám thai Giả Lập, liên hệ 0900123456"]
    primary = simple_client("openai:test", observe)
    plan_appointment(appt, rules, HybridClient(local=MockClient(model="ollama:test"), primary=primary), [], ner=False)
    assert primary.calls > 0 and prompts
    assert all("0900123456" not in prompt and "Giả Lập" not in prompt for prompt in prompts)


def test_sensitive_service_routes_hybrid_to_local(monkeypatch, rules):
    from core.pipeline import plan_appointment

    allow_synthetic_primary(monkeypatch)
    appt = appointment("song thai cần theo dõi")
    appt.services = ["Xét nghiệm HIV"]
    primary, local = simple_client("openai:test"), MockClient(model="ollama:test")
    plan_appointment(appt, rules, HybridClient(local=local, primary=primary), [], ner=False)
    assert primary.calls == 0 and local.calls > 0


@pytest.mark.parametrize("invalid_local", [
    {"OLLAMA_MODEL": "gpt-oss:120b-cloud"},
    {"OLLAMA_URL": "https://external.invalid"},
])
def test_hybrid_factory_keeps_local_cloud_and_host_guards(monkeypatch, invalid_local):
    allow_synthetic_primary(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "hybrid")
    for key, value in invalid_local.items():
        monkeypatch.setenv(key, value)

    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("Invalid fallback configuration must be rejected before a network call")

    monkeypatch.setattr(llm_client.requests, "get", forbidden_network)
    monkeypatch.setattr(llm_client.requests, "post", forbidden_network)
    client, why = make_client_safe(load_rules())
    assert client is None and why and "quy tắc" in why
