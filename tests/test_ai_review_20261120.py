"""Rà soát phần AI 20/11/2026: kết quả AI lưu tên model THẬT đã trả lời (CLAUDE.md), không lưu tên cấu hình hybrid."""

from __future__ import annotations

from datetime import time

from core.ai.llm_client import MockClient, real_model
from core.ai.note_extractor import extract
from core.ai.question_selector import ai_scores
from core.config import load_forms, load_rules
from core.rules.question_select import Context

RULES = load_rules()
REAL = "ollama:qwen2.5:7b-instruct"


class HybridLike(MockClient):
    """Giống HybridClient: name là cấu hình, last_provider là model thật vừa trả lời."""

    def __init__(self, responder=None) -> None:  # noqa: ANN001
        super().__init__(responder, model="hybrid:openai:gpt-x|ollama:qwen2.5:7b-instruct")
        self.last_provider = None

    def generate_json(self, prompt, schema, temperature, seed):  # noqa: ANN001, ANN201
        out = super().generate_json(prompt, schema, temperature, seed)
        self.last_provider = REAL
        return out


def test_real_model_helper() -> None:
    c = MockClient(model="hybrid:a|b")
    assert real_model([REAL, REAL], c) == REAL
    assert real_model(["openai:gpt-x", REAL], c) == "openai:gpt-x+" + REAL
    assert real_model([], c) == "hybrid:a|b"
    assert len(real_model(["x" * 80], c)) == 64


def test_note_extraction_stores_real_model_not_hybrid_name() -> None:
    ex = extract("31w3d, SA 4D, đến 7h30", ["Khám thai"], time(8, 0), HybridLike(), RULES)
    assert ex.attempted and ex.model == REAL


def test_question_selection_stores_real_model_not_hybrid_name() -> None:
    client = HybridLike(lambda p, s, d: {"scores": [{"code": "C8", "score": 0.9, "reason": "x"}], "confidence": 0.9})
    ctx = Context(ga=(30, 0), flags=["TWINS"], confirmed=[], flagged_answers={"C1": "Có"})
    r = ai_scores(["F5"], ctx, RULES, load_forms(), client)
    assert r.ok and r.model == REAL


def test_embedders_refuse_unmasked_identifiers_before_network(monkeypatch) -> None:  # noqa: ANN001
    import pytest

    import core.ai.reader.semantic as sem
    from core.ai.llm_client import LlmError

    monkeypatch.setattr(sem.requests, "post", lambda *a, **k: pytest.fail("không được gửi"))
    with pytest.raises(LlmError, match="Chặn gửi"):
        sem.OllamaEmbedder("synthetic-embed").embed(["ra máu, gọi 0912345678"])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("core.config.check_provider", lambda p: p)
    with pytest.raises(LlmError, match="Chặn gửi"):
        sem.OpenAIEmbedder().embed(["email an@vi.du"])
