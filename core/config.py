"""Đọc cấu hình: rules.yaml, his_columns.yaml, forms.yaml, .env.

Code nghiệp vụ chỉ đọc giá trị từ đây; không viết cứng ngưỡng y khoa.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TZ = ZoneInfo("Asia/Bangkok")

ALLOWED_LLM_PROVIDERS = ("ollama", "mock")


def load_env(path: Path | None = None) -> None:
    """Nạp biến từ file .env (nếu có) mà không ghi đè biến đã đặt."""
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class Rules:
    """Cấu hình quy tắc đã đọc; luôn mang theo rules_version."""

    data: dict[str, Any]
    path: Path

    @property
    def rules_version(self) -> str:
        return str(self.data["rules_version"])

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


@dataclass(frozen=True)
class HisColumns:
    columns: dict[str, str]
    required: list[str]
    date_format: str
    time_format: str
    self_booking_prefixes: tuple[str, ...] = ()
    vietnam_aliases: tuple[str, ...] = ("Việt Nam",)
    worldsms_columns: tuple[tuple[str, str], ...] = ()  # (tiêu đề, trường)
    worldsms_phone_format: str = "local"


@dataclass(frozen=True)
class FormsConfig:
    questions: dict[str, dict[str, Any]]
    epds: dict[str, Any]
    forms: dict[str, dict[str, Any]] = field(default_factory=dict)


def rules_path() -> Path:
    return Path(os.environ.get("APP_RULES_FILE", CONFIG_DIR / "rules.yaml"))


def load_rules(path: Path | str | None = None) -> Rules:
    p = Path(path) if path else rules_path()
    data = _read_yaml(p)
    if "rules_version" not in data:
        raise ValueError(f"{p}: thiếu rules_version")
    return Rules(data=data, path=p)


def load_his_columns(path: Path | str | None = None) -> HisColumns:
    p = Path(path) if path else CONFIG_DIR / "his_columns.yaml"
    d = _read_yaml(p)
    return HisColumns(
        columns=dict(d["columns"]),
        required=list(d["required"]),
        date_format=d.get("date_format", "%d/%m/%Y"),
        time_format=d.get("time_format", "%H:%M"),
        self_booking_prefixes=tuple(d.get("self_booking_prefixes", [])),
        vietnam_aliases=tuple(d.get("vietnam_aliases", ["Việt Nam"])),
        worldsms_columns=tuple((c["header"], c["field"]) for c in (d.get("worldsms") or {}).get("columns", [])),
        worldsms_phone_format=str((d.get("worldsms") or {}).get("phone_format", "local")),
    )


def load_forms(path: Path | str | None = None) -> FormsConfig:
    p = Path(path) if path else CONFIG_DIR / "forms.yaml"
    d = _read_yaml(p)
    return FormsConfig(questions=d["questions"], epds=d["epds"], forms=d["forms"])


def llm_provider(rules: Rules | None = None) -> str:
    """Nhà cung cấp LLM: biến môi trường LLM_PROVIDER ưu tiên, sau đó rules.yaml."""
    provider = os.environ.get("LLM_PROVIDER") or (rules.get("llm.provider") if rules else None)
    provider = (provider or "mock").strip().lower()
    if provider not in ALLOWED_LLM_PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER='{provider}' không được phép. MVP chỉ cho phép: {ALLOWED_LLM_PROVIDERS}"
        )
    return provider


def is_production() -> bool:
    """APP_ENV=production: tắt form demo, tài liệu API; chặn khởi động nếu còn mật khẩu demo."""
    return os.environ.get("APP_ENV", "").strip().lower() == "production"


def sms_whitelist() -> set[str]:
    raw = os.environ.get("SMS_WHITELIST", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def prompt_text(name: str) -> tuple[str, str]:
    """Trả (nội dung prompt, prompt_version) từ config/prompts/<name>.md."""
    path = CONFIG_DIR / "prompts" / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    version = f"{name}-0"
    first = text.splitlines()[0] if text else ""
    if "prompt_version:" in first:
        version = first.split("prompt_version:")[1].replace("-->", "").strip()
    return text, version
