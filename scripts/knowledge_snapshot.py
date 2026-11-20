"""Lưu bản chụp trang nguồn + gắn câu trích vào bản chụp (knowledge/README.md mục 6).

Chạy:
  python scripts/knowledge_snapshot.py                                  # chụp nguồn cấp A/B có link + nguồn có trích dẫn
  python scripts/knowledge_snapshot.py --codes VMC_CTG,TA_TIEN_ME       # chỉ các mã này
  python scripts/knowledge_snapshot.py --attach knowledge/danh_muc_kham.xlsx
      # gắn 'Bản chụp' + 'SHA-256' cho các dòng Trích dẫn chưa có, nếu câu có NGUYÊN VĂN trong bản chụp mới nhất

Chỉ tải link công khai, không gửi dữ liệu bệnh nhân. Bản chụp trùng nội dung bản mới nhất thì không tạo thêm file.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import knowledge_sources as ks  # noqa: E402
from core.config import TZ, load_visit_services  # noqa: E402
from core.knowledge import QUOTE_COLUMNS, default_know_dir, edit_sheet  # noqa: E402


def take(codes: set[str], tiers: set[str]) -> int:
    cat = load_visit_services()
    know = default_know_dir()
    with_facts = {f["source"] for f in cat.get("facts") or []}
    fails = 0
    for code, s in cat["sources"].items():
        url = s.get("url") or ""
        if codes and code not in codes:
            continue
        if not codes and not (url and (s.get("tier") in tiers or code in with_facts)):
            continue
        try:
            snap = ks.snapshot_source(know, code, url, datetime.now(TZ))
        except Exception as e:  # noqa: BLE001 – một link lỗi không dừng cả lượt
            fails += 1
            print(f"  ✗ {code}: không tải được ({type(e).__name__}: {str(e)[:80]})")
            continue
        note = "" if snap.text_ok else "  (PDF – chưa trích được chữ, không làm căn cứ)"
        print(f"  • {code}: {snap.path}  sha256 {snap.sha256[:12]}…{note}")
    return 1 if fails else 0


def attach(xlsx: Path) -> int:
    know = default_know_dir()
    missing: list[str] = []

    def fill(rec: dict[str, str]) -> dict[str, str]:
        code, quote = rec.get("Mã nguồn", ""), rec.get("Câu trích nguyên văn", "")
        if not code or not quote or rec.get("Bản chụp") or rec.get("Trạng thái") == "Loại":
            return {}
        snap = ks.latest_snapshot(know, code)
        text = ks.read_snapshot(know, snap.path) if snap else None
        if not snap or text is None or not ks.quote_in_text(quote, text):
            missing.append(f"{code}: {quote[:90]}")
            return {}
        out = {"Bản chụp": snap.path, "SHA-256": snap.sha256}
        if not rec.get("Ngày truy cập"):
            out["Ngày truy cập"] = snap.accessed[:10]
        return out

    n = edit_sheet(xlsx, "Trích dẫn", QUOTE_COLUMNS, fill)
    print(f"Đã gắn bản chụp cho {n} câu trích trong {xlsx}.")
    if missing:
        print(f"⚠ {len(missing)} câu KHÔNG thấy nguyên văn trong bản chụp mới nhất – người duyệt mở trang gốc đối chiếu "
              "(sửa câu cho đúng nguyên văn, hoặc đổi Trạng thái thành 'Loại'):")
        for m in missing:
            print(f"  ✗ {m}")
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--tiers", default="A,B")
    ap.add_argument("--attach", default="", help="Sổ Excel cần gắn bản chụp cho câu trích")
    args = ap.parse_args()
    if args.attach:
        return attach(Path(args.attach))
    return take({c.strip() for c in args.codes.split(",") if c.strip()},
                {t.strip() for t in args.tiers.split(",") if t.strip()})


if __name__ == "__main__":
    raise SystemExit(main())
