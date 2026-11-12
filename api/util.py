"""Tiện ích chung cho API: chuyển dữ liệu sang JSON, trả file tải về, làm sạch tên file."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
from fastapi import Response

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def jsonable(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, time):
        return v.strftime("%H:%M")
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set):
        return [jsonable(x) for x in v]
    if hasattr(v, "item"):  # numpy scalar
        return jsonable(v.item())
    return v


def df_records(df: pd.DataFrame) -> dict[str, Any]:
    """DataFrame → {columns, rows} (giữ thứ tự cột để giao diện hiển thị đúng)."""
    if df is None or df.empty:
        return {"columns": [], "rows": []}
    return {"columns": [str(c) for c in df.columns], "rows": [jsonable(r) for r in df.to_dict("records")]}


def safe_name(name: str, default: str = "upload.xlsx") -> str:
    """Tên file do trình duyệt gửi: chỉ giữ phần tên, bỏ thư mục / ký tự lạ."""
    return re.sub(r"[^\w.\-]", "_", Path(name or "").name)[-120:] or default


def file_response(data: bytes, filename: str, media_type: str = XLSX) -> Response:
    return Response(data, media_type=media_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})
