"""Sao lưu CSDL an toàn khi app đang chạy (dùng API backup của SQLite – lấy cả dữ liệu còn trong file -wal).

python scripts/backup_db.py                 # → data/backups/app_YYYYmmdd_HHMMSS.db, giữ 14 bản mới nhất
python scripts/backup_db.py --keep 30

KHÔNG chép tay data/app.db khi server đang chạy: từ khi bật WAL, dữ liệu mới có thể còn trong app.db-wal.
Bản sao lưu vẫn chứa dữ liệu đã mã hóa (tên, SĐT, ghi chú) – cần cả APP_SECRET_KEY để đọc; cất khóa ở nơi khác.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import DATA_DIR, TZ  # noqa: E402


def backup(src: Path, out_dir: Path, keep: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"app_{datetime.now(TZ):%Y%m%d_%H%M%S}.db"
    with sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True) as s, sqlite3.connect(dest) as d:
        s.backup(d)
    with sqlite3.connect(dest) as d:
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        dest.unlink(missing_ok=True)
        raise SystemExit(f"Bản sao lưu lỗi: {ok}")
    for old in sorted(out_dir.glob("app_*.db"))[:-keep]:
        old.unlink()
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=14)
    ap.add_argument("--src", type=Path, default=DATA_DIR / "app.db")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "backups")
    a = ap.parse_args()
    print("Đã sao lưu:", backup(a.src, a.out, a.keep))


if __name__ == "__main__":
    main()
