from __future__ import annotations

import pytest
from sqlalchemy import inspect, select

from core.config import load_rules
from core.security import (
    PermissionDenied,
    authenticate,
    can,
    decrypt,
    encrypt,
    is_valid_phone,
    mask_phone,
    normalize_phone,
    require,
)

EXPECTED_TABLES = {
    "user", "patient", "import_batch", "appointment", "note_extraction", "arrival_plan", "form_schedule",
    "form_token", "form_response", "assessment", "alert", "vital_reading", "noshow_prediction",
    "rule_change", "message_log", "audit_log", "eval_run",
}


def test_create_db_has_all_tables(db):
    tables = set(inspect(db.get_engine()).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_encrypt_roundtrip():
    token = encrypt("Nguyễn Thị Lan")
    assert token and "Lan" not in token
    assert decrypt(token) == "Nguyễn Thị Lan"


def test_mask_phone():
    assert mask_phone("0912345412") == "09•• ••• 412"
    assert mask_phone("+84 912 345 412") == "09•• ••• 412"
    assert "345" not in mask_phone("0912345412")


def test_normalize_phone():
    assert normalize_phone("912345412") == "0912345412"
    assert normalize_phone("0912.345.412") == "0912345412"
    assert normalize_phone(912345412.0) == "0912345412"
    assert is_valid_phone("0912345412")
    assert not is_valid_phone("09123")


def test_config_has_version():
    r = load_rules()
    assert r.rules_version
    assert r.get("thresholds.bp_red.sys") == 160


def test_llm_provider_only_local(monkeypatch, rules):
    from core.config import llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ValueError):
        llm_provider(rules)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert llm_provider(rules) == "mock"


def test_openai_only_with_synthetic_flag_and_not_production(monkeypatch, rules):
    from core.config import llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("SYNTHETIC_DATA_ONLY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValueError, match="dữ liệu GIẢ"):
        llm_provider(rules)
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", "1")
    assert llm_provider(rules) == "openai"
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="production"):
        llm_provider(rules)


def test_openai_client_parses_and_validates(monkeypatch, tmp_path):
    import json as _json

    from core.ai import llm_client

    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "spend.json")  # không đụng chi phí thật

    sent: list[dict] = []

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": _json.dumps({"x": 1})}}]}

    def fake_post(url, json, timeout, headers):  # noqa: A002
        sent.append(json)
        assert headers["Authorization"] == "Bearer sk-test"
        return Resp()

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    c = llm_client.OpenAIClient(model="m", api_key="sk-test")
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    assert c.generate_json("p", schema, 0.2, 7) == {"x": 1}
    assert sent[0]["response_format"] == {"type": "json_object"} and sent[0]["seed"] == 7
    with pytest.raises(llm_client.LlmError):
        c.generate_json("p", {"type": "object", "required": ["y"]}, 0.2, None)


def test_login_bcrypt():
    u = authenticate("nurse", "nurse123")
    assert u and u.role == "nurse"
    assert authenticate("nurse", "sai") is None
    assert authenticate("khong_co", "x") is None


def test_nurse_cannot_approve_rules():
    assert not can("nurse", "approve_rule")
    assert can("doctor", "approve_rule")
    with pytest.raises(PermissionDenied):
        require("nurse", "approve_rule")
    assert not can("doctor", "change_channel")
    assert can("admin", "change_channel")


def test_audit_log_written(db):
    from core.audit import log
    from core.models import AuditLog

    log("login", "user", 1, {"x": 1}, user_id=1)
    with db.session_scope() as s:
        rows = s.scalars(select(AuditLog)).all()
    assert len(rows) == 1 and rows[0].action == "login"


def test_openai_budget_guard(monkeypatch, tmp_path):
    from core.ai import llm_client

    monkeypatch.setattr(llm_client, "SPEND_FILE", tmp_path / "spend.json")
    monkeypatch.setenv("OPENAI_BUDGET_USD", "0.007")

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": '{"x": 1}'}}],
                    "usage": {"prompt_tokens": 10000, "completion_tokens": 1000}}

    calls: list[int] = []
    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **k: calls.append(1) or Resp())
    c = llm_client.OpenAIClient(model="m", api_key="sk-test")
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}
    c.generate_json("p", schema, 0.2, None)  # 10k*0.4 + 1k*1.6 = 0.0056 USD
    assert abs(llm_client.openai_spent() - 0.0056) < 1e-9
    with pytest.raises(llm_client.BudgetExceeded):
        c.generate_json("p", schema, 0.2, None)  # 0.0056 + ước lượng ~0.0016 > 0.007 → chặn, không gọi
    assert len(calls) == 1 and c.budget_hit
