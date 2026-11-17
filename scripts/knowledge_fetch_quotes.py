"""Gợi ý câu trích từ trang nguồn để người duyệt chọn (docs/17). KHÔNG tự duyệt.

Chạy: python scripts/knowledge_fetch_quotes.py knowledge/danh_muc_kham.xlsx [--codes VMC_SA_11_DIEU,TA_3_MOC] [--all]
- Mặc định: các nguồn cấp A/B chưa có trích dẫn nào.
- Tải trang (chỉ link công khai, không gửi dữ liệu bệnh nhân), tách câu, giữ câu có thông tin dùng cho xếp lịch
  (phút, giờ, tiếng, nhịn, tuần, trước khi, kéo dài…), thêm vào sheet 'Trích dẫn' với Trạng thái 'Chờ duyệt'.
- Người duyệt: mở trang gốc đối chiếu → đổi 'Chờ duyệt' thành 'Đã duyệt' (hoặc 'Loại'), điền 'Dịch vụ áp dụng',
  đổi 'Đã kiểm trang gốc' của nguồn thành 'Có' → chạy knowledge_import.py.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.textutil import deaccent  # noqa: E402

CUES = ["phut", "gio", "tieng", "nhin", "tuan", "truoc khi", "keo dai", "toi thieu", "mat khoang", "dao dong",
        "den truoc", "buoi sang", "lay mau", "uong"]
UA = {"User-Agent": "Mozilla/5.0 (compatible; knowledge-quote-helper)"}


def page_text(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    raw = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", r.text)
    txt = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", raw))
    return re.sub(r"[ \t]+", " ", txt)


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
            sents = candidate_sentences(page_text(url))
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {code}: không tải được ({type(e).__name__})")
            continue
        print(f"  • {code}: {len(sents)} câu gợi ý")
        rows += [{"Mã nguồn": code, "Dịch vụ áp dụng": "", "Câu trích nguyên văn": t, "Trạng thái": "Chờ duyệt",
                  "Ngày truy cập": date.today().isoformat()} for t in sents]
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
