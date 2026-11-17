"""Chọn câu cho form gửi hôm nay: gọi AI song song (OpenAI trên dữ liệu giả) – kết quả như chạy lần lượt, model THẬT
ghi đúng theo từng lần gọi (luật 5), phần lùi về Ollama vẫn đi lần lượt. Không mạng ngoài (client giả).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from conftest import _block_outbound_network as _block_outbound_network  # noqa: F401
from conftest import db as db  # noqa: F401
from test_flow_audit_fixes import _schedule
from test_security_audit_review import review as review  # noqa: F401

from core.ai.llm_client import HybridClient, MockClient, _PerThreadProvider, result_provider
from core.forms import ai_workers


class _FakeOpenAI(_PerThreadProvider):
    """Giống OpenAIClient: name bắt đầu 'openai:', đặt last_provider sau mỗi lần trả lời."""

    def __init__(self, delay: float = 0.0, barrier: threading.Barrier | None = None) -> None:
        self.name = "openai:fake"
        self.last_provider = None
        self.delay, self.barrier = delay, barrier
        self._mock = MockClient()
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            self.last_provider = f"openai:fake-{threading.get_ident()}"
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            time.sleep(self.delay)
            return self._mock.generate_json(prompt, schema, temperature, seed)
        finally:
            with self._lock:
                self.active -= 1


def test_workers_only_parallel_for_external(monkeypatch):
    monkeypatch.delenv("AI_PARALLEL_CALLS", raising=False)
    assert ai_workers(None, 10) == 1
    assert ai_workers(MockClient(), 10) == 1  # mock / Ollama: lần lượt
    assert ai_workers(_FakeOpenAI(), 1) == 1
    assert ai_workers(_FakeOpenAI(), 3) == 3
    assert ai_workers(_FakeOpenAI(), 27) == 6
    monkeypatch.setenv("AI_PARALLEL_CALLS", "1")
    assert ai_workers(_FakeOpenAI(), 27) == 1
    monkeypatch.setenv("AI_PARALLEL_CALLS", "100")
    assert ai_workers(_FakeOpenAI(), 27) == 16
    monkeypatch.setenv("AI_PARALLEL_CALLS", "abc")
    assert ai_workers(_FakeOpenAI(), 27) == 6


def test_last_provider_is_per_thread():
    """Hai luồng cùng client: mỗi luồng đọc lại đúng model của lần gọi của mình, không bị luồng kia ghi đè."""
    client = _FakeOpenAI(barrier=threading.Barrier(2))
    seen: dict[int, tuple[str, str]] = {}

    def run() -> None:
        client.last_provider = f"openai:fake-{threading.get_ident()}"
        client.barrier.wait(timeout=5)  # cả hai đã ghi xong trước khi đọc
        seen[threading.get_ident()] = (
            f"openai:fake-{threading.get_ident()}",
            result_provider(client),
        )

    ts = [threading.Thread(target=run) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert len(seen) == 2 and all(want == got for want, got in seen.values())


def test_hybrid_local_fallback_is_serialized():
    """Hybrid lùi về Ollama: các lần gọi cục bộ đi lần lượt (Ollama xử lý từng yêu cầu), không chồng nhau."""
    local = _FakeOpenAI(delay=0.05)
    local.name = "ollama:fake"
    hybrid = HybridClient(local=local, primary=None)
    ts = [
        threading.Thread(
            target=lambda: hybrid.generate_json(
                '<<<TASK:question_selector>>>\n<<<INPUT>>>{"mandatory": [], "candidates": []}<<<END>>>',
                {"type": "object"},
                0.3,
                1,
            )
        )
        for _ in range(4)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert local.peak == 1


@pytest.mark.parametrize("parallel", ["1", "6"])
def test_precompute_parallel_matches_sequential(review, monkeypatch, parallel):
    """Cùng dữ liệu: chạy song song cho đúng bộ câu như chạy lần lượt; model ghi lại là model thật đã trả lời."""
    from core.config import load_forms, load_rules
    from core.forms import precompute_selections

    _schedule(review, "F5")
    _schedule(review, "M-THA")
    rules, forms_cfg = load_rules(), load_forms()

    def run(n: str) -> dict:
        monkeypatch.setenv("AI_PARALLEL_CALLS", n)
        sel = _FakeOpenAI(delay=0.02)
        with review.db.session_scope() as s:
            out = precompute_selections(s, review.today, rules, forms_cfg, sel)
        return out, sel

    base, _ = run("1")
    got, sel = run(parallel)
    assert len(base) == 2 and base.keys() == got.keys()
    for k in base:
        assert [c.code for c in base[k][1]] == [c.code for c in got[k][1]]
        assert base[k][0].ok == got[k][0].ok
        models = got[k][0].raw.get("models") or []
        assert models and all(m.startswith("openai:fake-") for m in models)
        assert (
            len(set(models)) == 1
        )  # mọi lần chấm của 1 form do cùng 1 luồng → cùng tên, không lẫn luồng khác
    if parallel == "6":
        assert sel.peak >= 2
