"""Client Ollama cục bộ, OpenAI (chỉ dữ liệu giả), hybrid có dự phòng, hoặc mock."""

from __future__ import annotations

import csv
import ipaddress
import json
import logging
import math
import os
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import urlparse

import jsonschema
import requests

from core.ai import spend_store
from core.config import ROOT, Rules, check_provider, llm_provider, ollama_model

INPUT_RE = re.compile(r"<<<INPUT>>>\s*(.*?)\s*<<<END>>>", re.DOTALL)
TASK_RE = re.compile(r"<<<TASK:([\w-]+)>>>")
log = logging.getLogger(__name__)


class LlmError(Exception):
    pass


class LlmClient(Protocol):
    name: str

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]: ...


def validate(output: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    try:
        if not isinstance(output, dict):
            raise LlmError("Đầu ra AI phải là một đối tượng JSON")
        jsonschema.validate(output, schema)
        json.dumps(output, allow_nan=False)
    except jsonschema.ValidationError as e:
        raise LlmError("Đầu ra AI sai lược đồ JSON") from e
    except (ValueError, TypeError) as e:
        raise LlmError("Đầu ra AI có giá trị không hợp lệ") from e
    return output


def is_cloud_model(name: str, tag_info: dict[str, Any] | None = None) -> bool:
    """Mô hình 'cloud' của Ollama chạy trên ollama.com → dữ liệu rời máy, cấm."""
    n = name.lower()
    if n.endswith("-cloud") or ":cloud" in n or "-cloud:" in n:
        return True
    return bool(tag_info and (tag_info.get("remote_host") or tag_info.get("remote_model")))


def is_internal_host(host: str) -> bool:
    """Chỉ chấp nhận localhost hoặc địa chỉ IP nội bộ (RFC 1918 / loopback). Tên miền khác bị từ chối."""
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or (ip.is_private and not ip.is_link_local)


class OllamaClient:
    """Gọi Ollama /api/generate với format = JSON schema để ép đầu ra."""

    def __init__(
        self, model: str, url: str | None = None, timeout: float = 120, allow_cloud_eval: bool = False
    ) -> None:
        # allow_cloud_eval chỉ do scripts/run_eval.py bật, chạy trên dữ liệu GIẢ (tests/golden, data/synthetic).
        self.allow_cloud_eval = allow_cloud_eval
        if is_cloud_model(model) and not allow_cloud_eval:
            raise LlmError(f"Mô hình '{model}' chạy trên đám mây Ollama – không được dùng với dữ liệu bệnh nhân.")
        self.model = model
        self.name = f"ollama:{model}"
        self.url = (url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        host = urlparse(self.url).hostname or ""
        if not is_internal_host(host):
            raise LlmError(f"OLLAMA_URL phải là máy cục bộ / mạng nội bộ, không phải '{host}'.")
        self.timeout = timeout
        self._local: bool | None = None

    def _tags(self) -> dict[str, dict[str, Any]]:
        r = requests.get(f"{self.url}/api/tags", timeout=3)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            raise LlmError("Ollama trả danh sách mô hình sai cấu trúc")
        if not all(isinstance(m, dict) and isinstance(m.get("name"), str) for m in data["models"]):
            raise LlmError("Ollama trả mô hình sai cấu trúc")
        return {m["name"]: m for m in data["models"]}

    def _check_local(self) -> bool:
        try:
            info = self._tags().get(self.model)
        except (requests.RequestException, ValueError) as e:
            raise LlmError(f"Ollama lỗi: {e}") from e
        return info is not None and (self.allow_cloud_eval or not is_cloud_model(self.model, info))

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]:
        if self._local is None:
            self._local = self._check_local()
        if not self._local:
            raise LlmError(f"Mô hình '{self.model}' không chạy cục bộ (remote_host) – chặn gửi dữ liệu.")
        options: dict[str, Any] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        try:
            r = requests.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "prompt": prompt, "format": schema, "stream": False, "options": options},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or not isinstance(data.get("response"), str):
                raise LlmError("Ollama trả nội dung không hợp lệ")
            text = data["response"]
            out = json.loads(text)
        except (requests.RequestException, ValueError, TypeError, KeyError) as e:
            raise LlmError(f"Ollama lỗi: {e}") from e
        return validate(out, schema)

    def available(self) -> bool:
        try:
            info = self._tags().get(self.model)
        except (requests.RequestException, ValueError, LlmError, TypeError):
            return False
        self._local = info is not None and (self.allow_cloud_eval or not is_cloud_model(self.model, info))
        return self._local


# ---------------------------------------------------------------- OpenAI (chỉ dữ liệu giả)
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
# Giá USD / 1 triệu token (gpt-4.1-mini) – KIỂM TRA LẠI bảng giá OpenAI; đổi bằng OPENAI_PRICE_IN / OPENAI_PRICE_OUT.
DEFAULT_PRICE_IN, DEFAULT_PRICE_OUT = 0.40, 1.60
SPEND_FILE = ROOT / "data" / "openai_spend.json"  # chi phí theo ngày, cộng dồn qua mọi lần chạy (data/ không lên git)


class BudgetExceeded(LlmError):
    """Chạm ngân sách OPENAI_BUDGET_USD – không gọi thêm. Là LlmError nên app tự lùi về quy tắc (an toàn)."""


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name) or default)
        return value if math.isfinite(value) and value >= 0 else default
    except ValueError:
        return default


def _today() -> str:
    from datetime import datetime

    from core.config import TZ

    return datetime.now(TZ).date().isoformat()


def _read_spend() -> dict[str, Any]:
    try:
        return spend_store.read(SPEND_FILE)
    except (OSError, ValueError, TypeError) as e:
        raise LlmError("Không đọc được sổ chi phí OpenAI – tạm chặn gọi") from e


def openai_spent(day: str | None = None) -> float:
    """Chi phí OpenAI trong NGÀY (mặc định hôm nay, giờ Asia/Bangkok). Ngân sách OPENAI_BUDGET_USD tính theo ngày."""
    return float((_read_spend().get("days") or {}).get(day or _today(), {}).get("usd", 0.0))


def _add_spend(usd: float, tokens_in: int, tokens_out: int) -> float:
    with spend_store.locked(SPEND_FILE):
        data = _read_spend()
        total = spend_store.add(data, _today(), usd, tokens_in, tokens_out)
        spend_store.write(SPEND_FILE, data)
        return total


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Sinh schema đóng cho provider; validation nghiệp vụ vẫn dùng schema gốc."""
    result = json.loads(json.dumps(schema))

    def close(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                properties = node.setdefault("properties", {})
                node["required"] = list(properties)
                node["additionalProperties"] = False
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)

    close(result)
    return result


class OpenAIClient:
    """Gọi OpenAI Chat Completions, ép JSON rồi kiểm lược đồ tại chỗ.

    Chỉ tạo qua make_client khi llm_provider() cho phép (SYNTHETIC_DATA_ONLY=1, không production).
    Văn bản vẫn phải qua core/ai/masking.py trước khi tới đây.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 60) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise LlmError("Thiếu OPENAI_API_KEY trong .env.")
        self.model = os.environ.get("OPENAI_MODEL") or model or DEFAULT_OPENAI_MODEL
        self.name = f"openai:{self.model}"
        self.timeout = timeout
        self._no_temperature = False  # một số mô hình chỉ nhận temperature mặc định
        self.budget = _env_float("OPENAI_BUDGET_USD", 1.0)
        self.price_in = _env_float("OPENAI_PRICE_IN", DEFAULT_PRICE_IN)
        self.price_out = _env_float("OPENAI_PRICE_OUT", DEFAULT_PRICE_OUT)
        self.budget_hit = False

    def _post(self, body: dict[str, Any]) -> requests.Response:
        return requests.post(
            OPENAI_URL, json=body, timeout=self.timeout, headers={"Authorization": f"Bearer {self.api_key}"}
        )

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]:
        system = (
            "Bạn là trợ lý đọc văn bản y khoa tiếng Việt cho phòng khám sản. Chỉ trả về MỘT đối tượng JSON "
            "đúng lược đồ sau, không thêm chữ nào khác:\n" + json.dumps(schema, ensure_ascii=False)
        )
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "clinical_extraction", "strict": True, "schema": strict_schema(schema)}},
            "max_completion_tokens": 1000,
        }
        if seed is not None:
            body["seed"] = seed
        if not self._no_temperature:
            body["temperature"] = temperature
        # Rào ngân sách: ước lượng lần gọi này (≈ 3 ký tự / token, đầu ra ≤ 1000 token) – vượt thì không gọi.
        est = ((len((system + prompt).encode("utf-8")) + 128) * self.price_in + 1000 * self.price_out) / 1e6
        try:
            reservation = spend_store.reserve(SPEND_FILE, _today(), est, self.budget)
        except (OSError, ValueError, TypeError, TimeoutError) as e:
            raise LlmError("Không giữ chỗ được ngân sách OpenAI – tạm chặn gọi") from e
        if reservation is None:
            self.budget_hit = True
            raise BudgetExceeded(f"Đã chạm ngân sách OpenAI hôm nay {self.budget:.2f} USD – dừng gọi.")
        usage_cost: tuple[float, int, int] | None = None
        charged = True  # Lỗi truyền tải có thể xảy ra sau khi server đã nhận request.
        try:
            r = self._post(body)
            if r.status_code == 400 and "temperature" in r.text and "temperature" in body:
                self._no_temperature = True
                body.pop("temperature")
                r = self._post(body)
            if r.status_code >= 400:
                charged = False
                raise LlmError(f"OpenAI lỗi HTTP {r.status_code}")
            data = r.json()
            if not isinstance(data, dict):
                raise LlmError("OpenAI trả phản hồi sai cấu trúc")
            usage = data.get("usage") or {}
            t_in, t_out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            if t_in < 0 or t_out < 0:
                raise LlmError("OpenAI trả số token không hợp lệ")
            if usage:
                usage_cost = ((t_in * self.price_in + t_out * self.price_out) / 1e6, t_in, t_out)
            choice = data["choices"][0]
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") in ("length", "content_filter"):
                raise LlmError("AI từ chối hoặc chưa hoàn tất kết quả")
            if not isinstance(message.get("content"), str) or not message["content"].strip():
                raise LlmError("OpenAI trả nội dung trống hoặc sai kiểu")
            out = json.loads(message["content"])
        except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
            raise LlmError(f"OpenAI trả phản hồi không dùng được ({type(e).__name__})") from e
        finally:
            try:
                spend_store.settle(SPEND_FILE, reservation, usage=usage_cost, charged=charged)
            except (OSError, ValueError, TypeError, TimeoutError) as e:
                raise LlmError("Không đối soát được chi phí OpenAI – giữ chỗ ngân sách còn hiệu lực") from e
        return validate(out, schema)

    def available(self) -> bool:
        return bool(self.api_key)


class HybridClient:
    """Ưu tiên OpenAI được phép; lỗi một lần thì dùng local cho phần còn lại của client.

    Không thăm dò API trả phí. Quyền gửi ra ngoài được kiểm tra lại trước mỗi lần gọi.
    name mô tả cấu hình, last_provider ghi nguồn của kết quả vừa trả về.
    """

    def __init__(self, local: LlmClient, primary: LlmClient | None = None) -> None:
        self.local = local
        self.primary = primary
        self.name = f"hybrid:{primary.name if primary else 'local-only'}|{local.name}"
        self.last_provider: str | None = None
        self._primary_failed = False

    @property
    def may_send_external(self) -> bool:
        if self.primary is None or self._primary_failed:
            return False
        try:
            check_provider("openai")
        except ValueError:
            return False
        return True

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]:
        self.last_provider = None
        if self.may_send_external:
            try:
                result = self.primary.generate_json(prompt, schema, temperature, seed)
            except LlmError:
                self._primary_failed = True
                # Không ghi prompt, API key hoặc nội dung lỗi từ máy chủ vào log.
                log.warning("OpenAI không dùng được; chuyển sang Ollama cho client này.")
            else:
                self.last_provider = self.primary.name
                return result
        result = self.local.generate_json(prompt, schema, temperature, seed)
        self.last_provider = self.local.name
        return result

    def available(self) -> bool:
        return self.may_send_external or self.local.available()


def can_send_external(client: LlmClient | None) -> bool:
    """Bao gồm wrapper hybrid và client tương thích chỉ khai báo name."""
    if isinstance(client, HybridClient):
        return client.may_send_external
    return getattr(client, "name", "").startswith("openai:")


def local_only_client(client: LlmClient | None) -> LlmClient | None:
    """Ghi chú nhạy cảm chỉ đi nhánh local, không thử primary trước."""
    if isinstance(client, HybridClient):
        return client.local
    return None if can_send_external(client) else client


def result_provider(client: LlmClient) -> str:
    return getattr(client, "last_provider", None) or client.name


# ---------------------------------------------------------------- Mock
Responder = Callable[[str, dict[str, Any], int | None], dict[str, Any]]


@lru_cache(maxsize=1)
def _golden_note_answers() -> dict[str, dict[str, Any]]:
    path = ROOT / "tests" / "golden" / "notes_answers.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {row["note"]: json.loads(row["answer"]) for row in csv.DictReader(f)}


def _mock_note(inp: dict[str, Any], seed: int | None) -> dict[str, Any]:
    note = inp.get("note", "")
    ans = _golden_note_answers().get(note)
    if ans is not None:
        return dict(ans)
    from core.ai.note_extractor import heuristic_extract

    return heuristic_extract(note)


def _mock_form(inp: dict[str, Any], seed: int | None) -> dict[str, Any]:
    from core.ai.form_reader import lexicon_findings

    findings = lexicon_findings(inp.get("text", ""), apply_assertions=True)
    return {"findings": findings, "unmapped_concerns": []}


def _mock_summary(inp: dict[str, Any], seed: int | None) -> dict[str, Any]:
    return {"text": inp.get("template_summary", "")}


def _mock_question_selector(inp: dict[str, Any], seed: int | None) -> dict[str, Any]:
    """Mock: câu có từ khóa của tình trạng → điểm cao (đủ để test luồng; không phải đánh giá lâm sàng)."""
    conds = " ".join(inp.get("conditions", [])).lower()
    out = []
    for q in [*inp.get("mandatory", []), *inp.get("candidates", [])]:
        text = q["text"].lower()
        hit = ("song thai" in conds or "sinh non" in conds) and ("gò" in text or "ra nước" in text)
        out.append({"code": q["code"], "score": 0.9 if hit else 0.3, "reason": "Mock: liên quan tình trạng" if hit else ""})
    return {"scores": out, "confidence": 0.7}


DEFAULT_RESPONDERS: dict[str, Callable[[dict[str, Any], int | None], dict[str, Any]]] = {
    "note_extractor": _mock_note,
    "form_reader": _mock_form,
    "summary": _mock_summary,
    "question_selector": _mock_question_selector,
}


class MockClient:
    """Mock dùng cho test và chạy không có Ollama: trả đáp án từ tests/golden hoặc heuristic."""

    def __init__(self, responder: Responder | None = None, model: str = "mock") -> None:
        self.name = model
        self.responder = responder
        self.calls = 0

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]:
        self.calls += 1
        if self.responder is not None:
            return validate(self.responder(prompt, schema, seed), schema)
        task_m = TASK_RE.search(prompt)
        inp_m = INPUT_RE.search(prompt)
        if not task_m or not inp_m:
            raise LlmError("Mock: prompt thiếu <<<TASK>>> hoặc <<<INPUT>>>")
        fn = DEFAULT_RESPONDERS.get(task_m.group(1))
        if fn is None:
            raise LlmError(f"Mock: không có responder cho {task_m.group(1)}")
        return validate(fn(json.loads(inp_m.group(1)), seed), schema)


def make_client(rules: Rules) -> LlmClient:
    return make_client_for(rules, llm_provider(rules))



def make_client_safe(rules: Rules) -> tuple[LlmClient | None, str | None]:
    """Client cho luồng của app: cấu hình AI sai / thiếu khóa / Ollama không chạy → (None, lý do) để LÙI VỀ QUY TẮC.

    Không bao giờ ném lỗi: form thai phụ có dấu hiệu đỏ vẫn phải được nhận và cảnh báo dù AI hỏng.
    """
    try:
        client = make_client(rules)
    except (ValueError, LlmError) as e:
        log.warning("AI tắt, chỉ dùng quy tắc: %s", e)
        return None, f"AI chưa dùng được ({e}) – chỉ xử lý bằng quy tắc."
    if isinstance(client, (OllamaClient, HybridClient)) and not client.available():
        return None, "Không kết nối được Ollama hoặc chưa tải mô hình – chỉ xử lý bằng quy tắc."
    return client, None


def make_client_for(rules: Rules, provider: str) -> LlmClient:
    """Client cho MỘT tác vụ với nhà cung cấp riêng (vd. chọn câu hỏi dùng openai). Vẫn qua check_provider (luật 5)."""
    p = check_provider(provider)
    if p in ("ollama", "hybrid"):
        local = OllamaClient(model=ollama_model(rules), timeout=rules.get("llm.timeout_seconds", 120))
        if p == "ollama":
            return local
        primary = None
        if os.environ.get("OPENAI_API_KEY", "").strip():
            try:
                check_provider("openai")
                primary = OpenAIClient(model=rules.get("llm.openai_model"))
            except (ValueError, LlmError):
                pass  # Không có quyền gửi ra ngoài: hybrid chỉ dùng Ollama.
        return HybridClient(local=local, primary=primary)
    if p == "openai":
        return OpenAIClient(model=rules.get("llm.openai_model"))
    return MockClient()


def load_prompt(name: str) -> tuple[str, str]:
    from core.config import prompt_text

    return prompt_text(name)


def render_prompt(template: str, payload: dict[str, Any]) -> str:
    return template.replace("{input_json}", json.dumps(payload, ensure_ascii=False))

