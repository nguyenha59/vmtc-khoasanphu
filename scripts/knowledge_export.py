"""Xuất danh mục tri thức khám ra sổ Excel cho khoa xem / sửa / duyệt (knowledge/README.md).

Chạy: python scripts/knowledge_export.py [--out knowledge/danh_muc_kham.xlsx]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_visit_services  # noqa: E402
from core.knowledge import to_workbook, validate  # noqa: E402

DEFAULT_OUT = ROOT / "knowledge" / "danh_muc_kham.xlsx"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    cat = load_visit_services()
    out = Path(args.out)
    to_workbook(cat, out)
    rep = validate(cat)
    print(f"Đã xuất {out} (phiên bản {cat['version']}).")
    for w in rep.warnings:
        print(f"  ⚠ {w}")


if __name__ == "__main__":
    main()
