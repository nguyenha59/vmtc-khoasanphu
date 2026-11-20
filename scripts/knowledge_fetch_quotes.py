"""Gợi ý câu trích từ trang nguồn để người duyệt chọn (knowledge/README.md). KHÔNG tự duyệt.

Chạy: python scripts/knowledge_fetch_quotes.py knowledge/danh_muc_kham.xlsx [--codes VMC_SA_11_DIEU,TA_3_MOC] [--all]
- Mặc định: các nguồn cấp A/B chưa có trích dẫn nào.
- Tải trang (chỉ link công khai, không gửi dữ liệu bệnh nhân), LƯU BẢN CHỤP (knowledge/sources/snapshots/), tách câu, giữ câu có thông tin dùng cho xếp lịch
  (phút, giờ, tiếng, nhịn, tuần, trước khi, kéo dài…), thêm vào sheet 'Trích dẫn' với Trạng thái 'Chờ duyệt'
  kèm 'Bản chụp' + 'SHA-256' (câu lấy từ đúng bản chụp đó).
- Người duyệt: mở trang gốc đối chiếu → đổi 'Chờ duyệt' thành 'Đã duyệt' (hoặc 'Loại'), điền 'Dịch vụ áp dụng',
  đổi 'Đã kiểm trang gốc' của nguồn thành 'Có' → chạy knowledge_import.py.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import knowledge_sources as ks  # noqa: E402
from core.config import TZ  # noqa: E402
from core.knowledge import default_know_dir  # noqa: E402
from core.textutil import deaccent  # noqa: E402

CUES = ["phut", "gio", "tieng", "nhin", "tuan", "truoc khi", "keo dai", "toi thieu", "mat khoang", "dao dong",
        "den truoc", "buoi sang", "lay mau", "uong"]
def candidate_sentences(text: str, limit: int = 15) -> list[str]:
    out: list[str] = []
    for line in text.split("\n"):
        for sent in re.split(r"(?<=[.!?])\s+", line.strip()):
            s = sent.strip()
            if not 25 <= len(s) <= 300:
                continue
            plain = deaccent(s).lower()
            if any(c in plain for c in CUES) and re.search(r"\d", s) and s not in out:
                out.append(s)
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--codes", default="")
    ap.add_argument("--all", action="store_true", help="Cả nguồn đã có trích dẫn")
    args = ap.parse_args()
    path = Path(args.xlsx)
    book = pd.read_excel(path, sheet_name=None, dtype=str)
    book = {k: v.fillna("") for k, v in book.items()}
    src, quotes = book["Nguồn"], book["Trích dẫn"]
    have = set(quotes["Mã nguồn"])
    wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
    rows = []
    for _, s in src.iterrows():
        code, url, tier = s["Mã"], s["Link"], s["Cấp"]
        if wanted and code not in wanted:
            continue
        if not wanted and (tier not in ("A", "B") or not url or (code in have and not args.all)):
            continue
        try:
            snap = ks.snapshot_source(default_know_dir(), code, url, datetime.now(TZ))
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {code}: không tải được ({type(e).__name__})")
            continue
        text = ks.read_snapshot(default_know_dir(), snap.path)
        if text is None:
            print(f"  ✗ {code}: {snap.path} là PDF chưa trích được chữ – không gợi ý câu")
            continue
        sents = candidate_sentences(text)
        print(f"  • {code}: {len(sents)} câu gợi ý (bản chụp {snap.path})")
        rows += [{"Mã nguồn": code, "Dịch vụ áp dụng": "", "Câu trích nguyên văn": t, "Trạng thái": "Chờ duyệt",
                  "Ngày truy cập": snap.accessed[:10], "Bản chụp": snap.path, "SHA-256": snap.sha256} for t in sents]
    if not rows:
        print("Không có câu mới.")
        return 0
    book["Trích dẫn"] = pd.concat([quotes, pd.DataFrame(rows)], ignore_index=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, df in book.items():
            df.to_excel(w, index=False, sheet_name=name)
    print(f"Đã thêm {len(rows)} câu 'Chờ duyệt' vào sheet Trích dẫn của {path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
