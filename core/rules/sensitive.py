"""Phát hiện nội dung nhạy cảm trong ghi chú (bệnh lây truyền, đình chỉ thai, bạo lực, tâm thần, yêu cầu giữ kín…).

Danh mục nằm trong config/rules.yaml → privacy.sensitive_topics. Kết quả chỉ để CẢNH BÁO người dùng
(không trao đổi với người nhà, không ghi vào tin nhắn) và để KHÔNG gửi ghi chú đó ra API bên ngoài.
"""

from __future__ import annotations

from typing import Any

from core.textutil import contains_keyword


def find_sensitive(note: str, topics: list[dict[str, Any]]) -> list[str]:
    """Trả nhãn các nhóm nhạy cảm có trong ghi chú (theo thứ tự cấu hình, không trùng)."""
    if not (note or "").strip():
        return []
    return [t["label"] for t in topics if any(contains_keyword(note, k) for k in t["keywords"])]
