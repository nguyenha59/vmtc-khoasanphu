"""Bộ đọc thứ hai theo NGHĨA (embedding cục bộ) – nhân chứng độc lập cho ca "chỉ AI thấy Đỏ" (15/11/2026).

So nghĩa của trích dẫn AI đưa ra với tên + ý nghĩa (`meaning`) của từng khái niệm trong rules.yaml – KHÔNG dùng danh
sách từ đồng nghĩa, nên bắt được cách nói mới ("giấy dính đỏ", "đom đóm bay trước mặt") mà danh mục chưa có.
Không tự quyết mức: chỉ xác nhận / không xác nhận khái niệm AI đã chọn (rules.yaml `semantic`).

An toàn: chỉ chạy Ollama cục bộ (chặn mô hình cloud, chặn địa chỉ ngoài mạng nội bộ); đầu vào là văn bản ĐÃ CHE.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from core.ai.llm_client import LlmError, OllamaClient, is_cloud_model, is_internal_host
from core.config import Rules
from core.textutil import deaccent


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Ollama /api/embed – chỉ máy cục bộ / mạng nội bộ, không mô hình cloud."""

    def __init__(self, model: str, url: str | None = None, timeout: float = 60) -> None:
        if is_cloud_model(model):
            raise LlmError(f"Mô hình '{model}' chạy trên đám mây Ollama – không được dùng.")
        self.model, self.name = model, f"ollama:{model}"
        self.url = (url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        if not is_internal_host(urlparse(self.url).hostname or ""):
            raise LlmError("OLLAMA_URL phải là máy cục bộ / mạng nội bộ.")
        self.timeout = timeout
        self._checker = OllamaClient(model, url=self.url, timeout=timeout)
        self._local: bool | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._local is None:
            self._local = self._checker.available()
        if not self._local:
            raise LlmError("Ollama: mô hình embedding không chạy cục bộ – chặn gửi nội dung.")
        try:
            r = requests.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts},
                              timeout=self.timeout, allow_redirects=False)
            if 300 <= r.status_code < 400:
                raise LlmError("Ollama embed chuyển hướng – chặn gửi nội dung sang địa chỉ khác.")
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise LlmError(f"Ollama embed lỗi: {e}") from e
        return _valid_vectors(data.get("embeddings") if isinstance(data, dict) else None, len(texts), "Ollama embed")


def _valid_vectors(vecs: Any, n: int, who: str) -> list[list[float]]:
    """Phản hồi nhà cung cấp sai cấu trúc (không phải danh sách n vectơ số hữu hạn, cùng số chiều) → LlmError, để
    tầng dự phòng (HybridEmbedder, giới hạn mức khi không xác nhận được) xử lý thay vì văng lỗi khác."""
    if not isinstance(vecs, list) or len(vecs) != n:
        raise LlmError(f"{who} trả sai cấu trúc / số lượng")
    dim = None
    for v in vecs:
        if not isinstance(v, list) or not v or not all(
                isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x) for x in v):
            raise LlmError(f"{who} trả vectơ không hợp lệ")
        if dim is not None and len(v) != dim:
            raise LlmError(f"{who} trả vectơ khác số chiều")
        dim = len(v)
    return vecs


OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_OPENAI_EMBED_MODEL = "text-embedding-3-small"
DEFAULT_EMBED_PRICE = 0.02  # USD / 1 triệu token (text-embedding-3-small) – KIỂM TRA LẠI; đổi bằng OPENAI_EMBED_PRICE


class OpenAIEmbedder:
    """OpenAI embeddings – CHỈ dữ liệu giả (luật 5): kiểm tra lại quyền trước MỖI lần gọi (SYNTHETIC_DATA_ONLY=1,
    không production); tính chung ngân sách ngày OPENAI_BUDGET_USD. Văn bản phải đã qua masking (nơi gọi bảo đảm)."""

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 30) -> None:
        from core.ai.llm_client import _env_float

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise LlmError("Thiếu OPENAI_API_KEY trong .env.")
        self.model = model or DEFAULT_OPENAI_EMBED_MODEL
        self.name = f"openai:{self.model}"
        self.timeout = timeout
        self.budget = _env_float("OPENAI_BUDGET_USD", 1.0)
        self.price = _env_float("OPENAI_EMBED_PRICE", DEFAULT_EMBED_PRICE)

    def embed(self, texts: list[str]) -> list[list[float]]:
        from core.ai import spend_store
        from core.ai.llm_client import SPEND_FILE, BudgetExceeded, _today
        from core.config import check_provider

        try:
            check_provider("openai")
        except ValueError as e:
            raise LlmError(str(e)) from e
        est = (sum(len(t.encode("utf-8")) for t in texts) + 64) * self.price / 1e6
        try:
            reservation = spend_store.reserve(SPEND_FILE, _today(), est, self.budget)
        except (OSError, ValueError, TypeError, TimeoutError) as e:
            raise LlmError("Không giữ chỗ được ngân sách OpenAI – tạm chặn gọi") from e
        if reservation is None:
            raise BudgetExceeded(f"Đã chạm ngân sách OpenAI hôm nay {self.budget:.2f} USD – dừng gọi.")
        usage_cost, charged = None, True
        try:
            r = requests.post(OPENAI_EMBED_URL, json={"model": self.model, "input": texts}, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code >= 400:
                charged = False
                raise LlmError(f"OpenAI embeddings lỗi HTTP {r.status_code}")
            data = r.json()
            tokens = int((data.get("usage") or {}).get("total_tokens", 0))
            usage_cost = (tokens * self.price / 1e6, tokens, 0)
            vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError) as e:
            raise LlmError(f"OpenAI embeddings trả phản hồi không dùng được ({type(e).__name__})") from e
        finally:
            spend_store.settle(SPEND_FILE, reservation, usage=usage_cost, charged=charged)
        return _valid_vectors(vecs, len(texts), "OpenAI embeddings")


class HybridEmbedder:
    """Thử OpenAI (khi được phép), lỗi / hết ngân sách / không được phép → Ollama cho phần còn lại.
    `name` là bộ đang dùng – chỉ mục mẫu nghĩa tính riêng theo tên nên không trộn vectơ hai mô hình."""

    def __init__(self, primary: Embedder | None, local: Embedder) -> None:
        self.primary, self.local = primary, local
        self._failed = primary is None

    @property
    def name(self) -> str:
        return self.local.name if self._failed else self.primary.name  # type: ignore[union-attr]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._failed:
            try:
                return self.primary.embed(texts)  # type: ignore[union-attr]
            except LlmError:
                self._failed = True
        return self.local.embed(texts)


class HashEmbedder:
    """Embedding giả cho test: túi từ (bỏ dấu) băm vào 256 chiều. Không dùng để đo chất lượng."""

    name = "mock:hash"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * 256
            for w in re.findall(r"\w+", deaccent(t).lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
            out.append(v)
        return out


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def prototypes(rules: Rules) -> dict[str, list[str]]:
    """Mẫu nghĩa của từng khái niệm: tên + ý nghĩa (bỏ mã nguồn "(S1, S2)") + mẫu bác sĩ thêm (`semantic.prototypes`)."""
    extra = (rules.get("semantic") or {}).get("prototypes") or {}
    out = {}
    for code, c in rules["symptom_concepts"].items():
        items = [c["name"]]
        if c.get("meaning"):
            items.append(re.sub(r"\s*[(]S[0-9, S]+[)]\s*$", "", c["meaning"]))
        items += list(extra.get(code, []))
        if (rules.get("semantic") or {}).get("plain_prototypes", True):  # khách hay viết không dấu
            items += [deaccent(t) for t in items]
        out[code] = items
    return out


_INDEX: dict[tuple[str, str], dict[str, list[list[float]]]] = {}


def _index(embedder: Embedder, rules: Rules) -> dict[str, list[list[float]]]:
    protos = prototypes(rules)
    key = (embedder.name, hashlib.sha256(repr(sorted(protos.items())).encode()).hexdigest())
    if key not in _INDEX:
        flat = [(code, t) for code, items in protos.items() for t in items]
        vecs = embedder.embed([t for _c, t in flat])
        idx: dict[str, list[list[float]]] = {}
        for (code, _t), v in zip(flat, vecs, strict=True):
            idx.setdefault(code, []).append(v)
        _INDEX[key] = idx
    return _INDEX[key]


@dataclass(frozen=True)
class Witness:
    concept: str
    score: float  # độ giống nghĩa với khái niệm AI chọn
    best: str  # khái niệm giống nhất
    best_score: float
    confirmed: bool

    def record(self) -> dict[str, Any]:
        return {"concept": self.concept, "score": round(self.score, 3), "best": self.best,
                "best_score": round(self.best_score, 3), "confirmed": self.confirmed}


def scores(fragment: str, embedder: Embedder, rules: Rules) -> dict[str, float]:
    """Độ giống nghĩa của một đoạn văn bản (đã che) với từng khái niệm."""
    name = embedder.name
    v = embedder.embed([fragment])[0]
    idx = _index(embedder, rules)
    if embedder.name != name:  # hybrid vừa lùi về Ollama → tính lại vectơ câu bằng cùng mô hình với chỉ mục
        v = embedder.embed([fragment])[0]
    if any(len(p) != len(v) for vecs in idx.values() for p in vecs):
        raise LlmError("Vectơ câu và mẫu nghĩa khác mô hình")
    return {code: max(_cos(v, p) for p in vecs) for code, vecs in idx.items()}


def witness(concept: str, fragment: str, embedder: Embedder, rules: Rules) -> Witness:
    """Xác nhận khi: độ giống ≥ `semantic.threshold` VÀ khái niệm AI chọn nằm trong top `semantic.top_k`."""
    cfg = rules.get("semantic") or {}
    sc = scores(fragment, embedder, rules)
    ranked = sorted(sc, key=lambda c: sc[c], reverse=True)
    top = ranked[: int(cfg.get("top_k", 1))]
    s = sc.get(concept, 0.0)
    return Witness(concept, s, ranked[0], sc[ranked[0]], s >= threshold_for(embedder, rules) and concept in top)


def threshold_for(embedder: Embedder, rules: Rules) -> float:
    """Ngưỡng theo mô hình đang dùng (`semantic.thresholds`, khóa = tên mô hình không tiền tố); thiếu → `threshold`."""
    cfg = rules.get("semantic") or {}
    model = embedder.name.split(":", 1)[-1]
    per = cfg.get("thresholds") or {}
    return float(per.get(model, per.get(model.removesuffix(":latest"), cfg.get("threshold", 1.0))))


def make_embedder_safe(rules: Rules) -> tuple[Embedder | None, str | None]:
    """Bộ embedding theo cấu hình; tắt / lỗi → (None, lý do) – khi đó không có nhân chứng (giữ cách cũ, an toàn).

    Nhà cung cấp: SEMANTIC_PROVIDER (.env) ưu tiên, rồi rules.yaml `semantic.provider`:
    ollama (mặc định, cục bộ) | hybrid (OpenAI khi được phép, lùi Ollama) | openai | mock (chỉ test).
    """
    cfg = rules.get("semantic") or {}
    if not cfg.get("enabled"):
        return None, "semantic tắt"
    provider = (os.environ.get("SEMANTIC_PROVIDER", "").strip() or cfg.get("provider", "ollama")).lower()
    try:
        if provider == "mock":
            return HashEmbedder(), None
        local = OllamaEmbedder(os.environ.get("SEMANTIC_MODEL", "").strip() or cfg["model"])
        if provider == "ollama":
            return local, None
        from core.config import check_provider

        try:
            check_provider("openai")
            primary: Embedder | None = OpenAIEmbedder(cfg.get("openai_model"))
        except (ValueError, LlmError) as e:
            if provider == "openai":
                return None, str(e)
            primary = None  # hybrid không được phép gửi ra ngoài → chỉ Ollama
        if provider == "openai":
            return primary, None
        if provider == "hybrid":
            return HybridEmbedder(primary, local), None
        return None, f"semantic.provider '{provider}' không hợp lệ"
    except LlmError as e:
        return None, str(e)
