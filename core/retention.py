"""Xóa file nhập gốc và file xuất (có họ tên, SĐT, PID dạng rõ) sau retention_days (docs/07)."""

from __future__ import annotations

import time
from pathlib import Path

from core.config import Rules, work_dir


def purge_uploads(rules: Rules, folder: Path | None = None) -> int:
    folder = folder or work_dir("uploads")
    if not folder.exists():
        return 0
    limit = time.time() - int(rules["retention_days"]) * 86400
    n = 0
    for p in folder.iterdir():
        if p.is_file() and p.stat().st_mtime < limit:
            p.unlink()
            n += 1
    return n


RETAINED_FOLDERS = ("uploads", "exports", "outbox")


def purge_all(rules: Rules, data_dir: Path | None = None) -> dict[str, int]:
    """File nhập (uploads) và file xuất gửi WorldSMS / danh sách gọi (exports, outbox) đều chứa dữ liệu cá nhân."""
    base = data_dir or work_dir("")
    return {name: purge_uploads(rules, base / name) for name in RETAINED_FOLDERS}
