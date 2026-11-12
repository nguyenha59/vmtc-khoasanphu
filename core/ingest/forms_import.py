"""Nhập câu trả lời form từ CSV Microsoft Forms (docs/03 mục 1.3)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from core.config import FormsConfig


@dataclass
class ImportedResponse:
    token: str
    form_code: str
    submitted_at: datetime | None
    answers: dict[str, Any]
    free_text: str


def _parse_dt(v: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(v.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_msforms_csv(data: bytes, forms_cfg: FormsConfig) -> list[ImportedResponse]:
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    if "token" not in df.columns or "form_code" not in df.columns:
        raise ValueError("File CSV thiếu cột 'token' hoặc 'form_code'.")
    known = set(forms_cfg.questions) | {f"E{i}" for i in range(1, 11)}
    out = []
    for r in df.to_dict("records"):
        answers = {k: v for k, v in r.items() if k in known and str(v).strip() != ""}
        out.append(ImportedResponse(token=r["token"].strip(), form_code=r["form_code"].strip(),
                                    submitted_at=_parse_dt(r.get("submitted_at", "")), answers=answers,
                                    free_text=str(r.get("free_text", "")).strip()))
    return out
