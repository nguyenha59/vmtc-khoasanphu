"""Khóa hành vi phần đọc tự viết: mọi câu trong các bộ đo × 12 kịch bản AI giả phải cho đúng kết quả đã chụp.

Đổi hành vi CỐ Ý → chạy `python scripts/snapshot_form_reader.py --write`, xem diff ảnh chụp, ghi lý do vào commit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import snapshot_form_reader as snap  # noqa: E402


def test_form_reader_behavior_matches_snapshot():
    expected = json.loads(snap.SNAPSHOT.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(snap.compute(), ensure_ascii=False, default=str))
    assert set(actual) == set(expected), "Bộ câu đo đổi – chạy lại snapshot_form_reader.py --write"
    diffs = [k for k in sorted(expected) if actual[k] != expected[k]]
    detail = "\n".join(f"- {k.replace(chr(0x241F), ' | ')}\n  cũ: {expected[k]}\n  mới: {actual[k]}" for k in diffs[:10])
    assert not diffs, f"{len(diffs)}/{len(expected)} kết quả đọc tự viết thay đổi:\n{detail}"
