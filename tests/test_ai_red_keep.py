"""Giữ ĐỎ cho ca "chỉ AI thấy" khi hai bộ đọc độc lập cùng xác nhận (form_cap.ai_only_red_keep, 15/11/2026)
+ bộ đọc theo nghĩa (core/ai/reader/semantic.py).

Mặc định TẮT (chờ bác sĩ). Khi bật: AI mọi lần chạy cùng thấy + bộ đọc theo nghĩa xác nhận đúng chủ đề → giữ Đỏ;
thiếu một điều kiện / embedding lỗi → hạ Vàng như cũ. Đầu vào embedding là văn bản ĐÃ CHE, chỉ máy cục bộ.
"""

from __future__ import annotations

import pytest

from core.ai.form_reader import read_free_text
from core.ai.llm_client import LlmError, MockClient
from core.ai.reader import semantic
from core.config import load_rules
from core.levels import Level
from core.textutil import deaccent

TEXT = "trước mắt cứ lóe đom đóm"  # danh mục không có từ khóa → chỉ AI thấy


class TopicEmbedder:
    """Embedding giả: một chiều cho mỗi khái niệm; văn bản chứa tên khái niệm (hoặc "dom dom" → VISUAL) bật chiều đó."""

    name = "test:topic"

    def __init__(self, rules, fail: bool = False) -> None:
        self.codes = list(rules["symptom_concepts"])
        self.names = {c: deaccent(v["name"]).lower() for c, v in rules["symptom_concepts"].items()}
        self.fail, self.inputs = fail, []

    def embed(self, texts):
        if self.fail:
            raise LlmError("embed lỗi")
        self.inputs += texts
        out = []
        for t in texts:
            p = deaccent(t).lower()
            v = [1.0 if (self.names[c] in p or (c == "VISUAL" and "dom dom" in p)) else 0.0 for c in self.codes]
            out.append(v if any(v) else [0.01] * len(self.codes))
        return out


def _rules(enabled: bool = True):
    r = load_rules()
    r.data["form_cap"]["ai_only_red_keep"]["enabled"] = enabled
    r.data["semantic"]["threshold"] = 0.5
    semantic._INDEX.clear()
    return r


def _llm(concept: str = "VISUAL", evidence: str = TEXT, agree_runs: int = 3) -> MockClient:
    calls = {"n": 0}

    def responder(prompt, schema, seed):
        calls["n"] += 1
        found = calls["n"] <= agree_runs
        return {"findings": [{"concept": concept, "present": True, "temporality": "current", "experiencer": "patient",
                              "severity": "unknown", "evidence": evidence}] if found else [],
                "unmapped_concerns": []}

    return MockClient(responder)


def _read(rules, client, embedder, text=TEXT):
    return read_free_text(text, 32, client, rules, ner=False, embedder=embedder)


def test_disabled_by_default_keeps_old_behavior():
    rules = load_rules()
    assert rules["form_cap"]["ai_only_red_keep"]["enabled"] is False
    r = _read(rules, _llm(), TopicEmbedder(rules))
    assert r.level == Level.YELLOW and "ai_red_checks" not in r.raw


def test_two_independent_readers_keep_red():
    rules = _rules()
    r = _read(rules, _llm(), TopicEmbedder(rules))
    assert r.level == Level.RED
    f = next(x for x in r.findings if x.concept == "VISUAL")
    assert "confirmed" in f.source
    assert r.raw["ai_red_checks"][0]["kept"] is True


def test_not_unanimous_is_capped():
    rules = _rules()
    r = _read(rules, _llm(agree_runs=2), TopicEmbedder(rules))
    assert r.level <= Level.YELLOW
    assert all(not c["kept"] for c in r.raw.get("ai_red_checks", []))


def test_semantic_disagrees_is_capped():
    """AI gán nhầm chủ đề: bộ đọc theo nghĩa thấy đoạn trích không nói về khó thở → hạ vàng."""
    rules = _rules()
    r = _read(rules, _llm(concept="DYSPNEA"), TopicEmbedder(rules))
    assert r.level == Level.YELLOW
    assert r.raw["ai_red_checks"][0]["why"] == "nghĩa không xác nhận"


@pytest.mark.parametrize("embedder_kind", ["none", "error"])
def test_no_witness_is_capped(embedder_kind, monkeypatch):
    rules = _rules()
    monkeypatch.setattr(semantic, "make_embedder_safe", lambda r: (None, "tắt"))
    emb = None if embedder_kind == "none" else TopicEmbedder(rules, fail=True)
    r = _read(rules, _llm(), emb)
    assert r.level == Level.YELLOW and r.raw["ai_red_checks"][0]["kept"] is False


def test_concept_not_in_list_is_capped():
    rules = _rules()
    rules.data["form_cap"]["ai_only_red_keep"]["concepts"] = ["BLEEDING"]
    r = _read(rules, _llm(), TopicEmbedder(rules))
    assert r.level == Level.YELLOW


def test_embedding_input_is_masked():
    rules = _rules()
    emb = TopicEmbedder(rules)
    text = "gọi 0912345678 nhé, trước mắt cứ lóe đom đóm"
    _read(rules, _llm(evidence="trước mắt cứ lóe đom đóm"), emb, text=text)
    assert emb.inputs and not any("0912345678" in t for t in emb.inputs)


def test_embedder_is_local_only():
    with pytest.raises(LlmError):
        semantic.OllamaEmbedder("bge-m3:cloud")
    with pytest.raises(LlmError):
        semantic.OllamaEmbedder("bge-m3", url="https://api.example.com")


def test_prototypes_use_meaning_not_synonyms():
    rules = load_rules()
    protos = semantic.prototypes(rules)
    syns = set(rules["symptom_concepts"]["BLEEDING"]["synonyms"])
    assert not syns & set(protos["BLEEDING"])
    assert rules["symptom_concepts"]["BLEEDING"]["name"] in protos["BLEEDING"]


# ---------------------------------------------------------------- OpenAI embedding: chỉ dữ liệu giả (luật 5)
def test_openai_embedder_blocked_without_synthetic_flag(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("SYNTHETIC_DATA_ONLY", raising=False)
    monkeypatch.setattr(semantic.requests, "post", lambda *a, **k: pytest.fail("không được gọi API"))
    with pytest.raises(LlmError):
        semantic.OpenAIEmbedder().embed(["đã che"])


def test_openai_embedder_blocked_in_production(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", "1")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(semantic.requests, "post", lambda *a, **k: pytest.fail("không được gọi API"))
    with pytest.raises(LlmError):
        semantic.OpenAIEmbedder().embed(["đã che"])


def test_hybrid_not_allowed_uses_local_only(monkeypatch):
    rules = load_rules()
    rules.data["semantic"]["provider"] = "hybrid"
    monkeypatch.delenv("SEMANTIC_PROVIDER", raising=False)
    monkeypatch.delenv("SYNTHETIC_DATA_ONLY", raising=False)
    emb, _why = semantic.make_embedder_safe(rules)
    assert isinstance(emb, semantic.HybridEmbedder) and emb.name.startswith("ollama:")


def test_hybrid_falls_back_without_mixing_models():
    rules = _rules()

    class Failing:
        name = "openai:test"

        def embed(self, texts):
            raise LlmError("hết ngân sách")

    emb = semantic.HybridEmbedder(Failing(), TopicEmbedder(rules))
    sc = semantic.scores("trước mắt cứ lóe đom đóm", emb, rules)
    assert emb.name == "test:topic" and max(sc, key=lambda c: sc[c]) == "VISUAL"
