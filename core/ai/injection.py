"""Phát hiện câu giống LỆNH điều khiển máy trong văn bản người dùng tự viết (prompt injection).

Văn bản thai phụ đã được đưa vào prompt dưới dạng chuỗi JSON (đã thoát ký tự) và đầu ra bị ép theo lược đồ, nên
một câu "bỏ qua hướng dẫn…" không phá được cấu trúc. Lớp này thêm phòng thủ theo chiều sâu: thấy dấu hiệu → không cho
AI hạ mức ở form đó (rules.yaml `ai_override`) và chuyển người xem. Không bao giờ hạ mức của quy tắc (luật 3).
Mẫu câu nằm trong rules.yaml `privacy.prompt_injection` (luật 2). Hàm thuần.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from core.textutil import deaccent

REVIEW_NOTE = "Văn bản có câu giống lệnh điều khiển máy – không cho AI hạ mức, cần người xem"


@lru_cache(maxsize=8)
def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


def injection_signals(text: str, rules: Any) -> list[str]:
    """Các đoạn (không dấu, ≤ 60 ký tự) khớp mẫu lệnh. Rỗng = không thấy / tắt."""
    cfg = ((rules.get("privacy") or {}).get("prompt_injection")) or {}
    if not cfg.get("enabled") or not (text or "").strip():
        return []
    plain = re.sub(r"\s+", " ", deaccent(text).lower())
    hits: list[str] = []
    for rx in _compiled(tuple(cfg.get("patterns") or [])):
        m = rx.search(plain)
        if m and m.group(0)[:60] not in hits:
            hits.append(m.group(0)[:60])
    return hits
