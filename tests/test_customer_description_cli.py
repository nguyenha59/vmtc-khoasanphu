"""An API-only evaluation must never silently become an Ollama or mock run."""

import json
import sys
from types import SimpleNamespace

import pytest

from core.ai.llm_client import LlmError
from scripts import eval_customer_descriptions as cli


@pytest.mark.parametrize("api_failure", [False, True])
def test_openai_cli_is_explicit_and_never_falls_back(monkeypatch, tmp_path, api_failure):
    out = tmp_path / "api.json"
    calls = []

    def generate(*_args, **_kwargs):
        calls.append("openai")
        if api_failure:
            raise LlmError("OpenAI HTTP 429")
        return {"findings": [], "unmapped_concerns": []}

    client = SimpleNamespace(name="openai:synthetic", budget=1.0, generate_json=generate,
                             last_provider="openai:synthetic-snapshot")

    def factory(_rules, provider):
        assert provider == "openai"
        return client

    def forbidden(*_args, **_kwargs):
        pytest.fail("API-only evaluation attempted Ollama or mock")

    monkeypatch.setattr(cli, "make_client_for", factory)
    monkeypatch.setattr(cli, "OllamaClient", forbidden)
    monkeypatch.setattr(cli, "MockClient", forbidden)
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "openai_spent", lambda: 0.0)
    monkeypatch.setattr(sys, "argv", ["eval_customer_descriptions.py", "--provider", "openai",
                                    "--ids", "normal_01,normal_02", "--out", str(out)])
    if api_failure:
        with pytest.raises(SystemExit, match="OpenAI"):
            cli.main()
    else:
        cli.main()
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["provider"] == "openai" and result["fallback_enabled"] is False
    assert result["planned_cases"] == 2
    assert set(calls) == {"openai"}
    if api_failure:
        assert result["status"] == "stopped_api_error" and len(result["cases"]) == 1
        assert result["summary"]["model_errors"] == 3 and not result["summary"]["models"]
    else:
        assert result["status"] == "completed" and len(result["cases"]) == 2
        assert result["summary"]["models"] == {"openai:synthetic-snapshot": 6}


def test_openai_cli_cannot_bypass_synthetic_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", "0")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(sys, "argv", ["eval_customer_descriptions.py", "--provider", "openai",
                                    "--out", str(tmp_path / "api.json")])
    with pytest.raises(ValueError, match="SYNTHETIC_DATA_ONLY"):
        cli.main()
