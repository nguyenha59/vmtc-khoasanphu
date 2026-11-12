"""Client mô hình ngôn ngữ: Ollama cục bộ, mock, hoặc OpenAI (API ngoài – CHỈ dữ liệu giả)."""

from __future__ import annotations

import csv
import ipaddress
import json
import os
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import urlparse

import jsonschema
import requests

from core.config import ROOT, Rules, llm_provider

INPUT_RE = re.compile(r"<<<INPUT>>>\s*(.*?)\s*<<<END>>>", re.DOTALL)
TASK_RE = re.compile(r"<<<TASK:([\w-]+)>>>")


class LlmError(Exception):
    pass


class LlmClient(Protocol):
    name: str

    def generate_json(
        self, prompt: str, schema: dict[str, Any], temperature: float, seed: int | None
    ) -> dict[str, Any]: ...


def validate(output: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as e:
        raise LlmError(f"Sai lược đồ JSON: {e.message}") from e
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
        return {m["name"]: m for m in r.json().get("models", [])}

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
            text = r.json().get("response", "")
            out = json.loads(text)
        except (requests.RequestException, ValueError) as e:
            raise LlmError(f"Ollama lỗi: {e}") from e
        return validate(out, schema)

    def available(self) -> bool:
        try:
            info = self._tags().get(self.model)
        except (requests.RequestException, ValueError):
            return False
        self._local = info is not None and (self.allow_cloud_eval or not is_cloud_model(self.model, info))
        return self._local


# ---------------------------------------------------------------- OpenAI (chỉ dữ liệu giả)
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


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
            "response_format": {"type": "json_object"},
        }
        if seed is not None:
            body["seed"] = seed
        if not self._no_temperature:
            body["temperature"] = temperature
        try:
            r = self._post(body)
            if r.status_code == 400 and "temperature" in r.text and "temperature" in body:
                self._no_temperature = True
                body.pop("temperature")
                r = self._post(body)
            if r.status_code >= 400:
                raise LlmError(f"OpenAI lỗi {r.status_code}: {r.text[:200]}")
            out = json.loads(r.json()["choices"][0]["message"]["content"])
        except (requests.RequestException, ValueError, KeyError, IndexError) as e:
            raise LlmError(f"OpenAI lỗi: {e}") from e
        return validate(out, schema)

    def available(self) -> bool:
        return bool(self.api_key)


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


DEFAULT_RESPONDERS: dict[str, Callable[[dict[str, Any], int | None], dict[str, Any]]] = {
    "note_extractor": _mock_note,
    "form_reader": _mock_form,
    "summary": _mock_summary,
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
    provider = llm_provider(rules)
    if provider == "ollama":
        return OllamaClient(model=rules["llm"]["model"], timeout=rules["llm"].get("timeout_seconds", 120))
    if provider == "openai":
        return OpenAIClient(model=rules["llm"].get("openai_model"))
    return MockClient()


def load_prompt(name: str) -> tuple[str, str]:
    from core.config import prompt_text

    return prompt_text(name)


def render_prompt(template: str, payload: dict[str, Any]) -> str:
    return template.replace("{input_json}", json.dumps(payload, ensure_ascii=False))

