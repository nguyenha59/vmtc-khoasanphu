"""Nạp danh mục nguồn (Excel người dùng tổng hợp) vào config/visit_services.yaml.

Chạy: python scripts/import_source_catalog.py <danh_muc_nguon_he_thong.xlsx>
- Gộp theo URL với nguồn đã có (giữ mã cũ, giữ `verified` + câu trích trong `facts`).
- Thêm cấp (tier A/B/C/X), tổ chức, dùng cho, thông tin tóm tắt, ghi chú kiểm chứng của người dùng.
- Nguồn mới chưa có câu trích nguyên văn → verified: false (AI không dùng làm căn cứ cho tới khi trích).
- Sheet "Nội bộ phải thu thập" → mục internal_required.
Ghi đè bằng cách thay khối giữa các dấu # >>> import và # <<< import (chạy lại được nhiều lần).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "visit_services.yaml"


def _norm(url: str) -> str:
    return url.strip().rstrip("/").replace("https://www.", "https://").lower()


def main() -> None:
    src_xlsx = Path(sys.argv[1])
    text = CFG.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    sources: dict = data["sources"]
    by_url = {_norm(v.get("url", "")): k for k, v in sources.items() if v.get("url")}

    pub = pd.read_excel(src_xlsx, "Nguồn công khai", dtype=str).fillna("")
    meta: dict[str, dict] = {}
    new_sources: dict[str, dict] = {}
    for _, r in pub.iterrows():
        url = r["Link"].strip()
        entry = {"tier": r["Cấp"], "org": r["Tổ chức"], "used_for": r["Dùng cho"],
                 "info": r["Thông tin dùng được (diễn đạt lại)"], "user_check": r["Kiểm chứng"],
                 "user_note": r["Ghi chú / xung đột"], "user_code": r["Mã"]}
        code = by_url.get(_norm(url))
        if code is None:
            code = r["Mã"] if r["Mã"] not in sources else f"{r['Mã']}_X"
            new_sources[code] = {"title": f"{r['Tổ chức']} – {r['Tiêu đề']}", "url": url, "verified": False}
        meta[code] = entry

    internal = pd.read_excel(src_xlsx, "Nội bộ phải thu thập", dtype=str).fillna("")
    internal_required = {
        r["Mã"]: {"need": r["Cần có"], "owner": r["Lấy từ đâu / ai duyệt"], "why": r["Vì sao cần"],
                  "status": r["Trạng thái"]}
        for _, r in internal.iterrows()
    }

    block = {
        "source_catalog": {"imported_from": src_xlsx.name, "imported_at": "2026-11-17",
                           "tiers": {"A": "chính thống, đã đọc nội dung → căn cứ (khi có câu trích nguyên văn)",
                                     "B": "chính thống nhưng chỉ đọc trích yếu / khác bối cảnh → tham khảo",
                                     "C": "bên thứ ba → chỉ đối chiếu", "X": "không dùng"},
                           "meta": meta},
        "extra_sources": new_sources,
        "internal_required": internal_required,
    }
    dumped = yaml.safe_dump(block, allow_unicode=True, sort_keys=False, width=200)
    start, end = "# >>> import danh mục nguồn (scripts/import_source_catalog.py)\n", "# <<< import\n"
    if start in text:
        text = text[: text.index(start)] + text[text.index(end) + len(end):]
    text = text.rstrip("\n") + "\n\n" + start + dumped + end
    CFG.write_text(text, encoding="utf-8")
    print(f"Nạp {len(meta)} nguồn ({len(new_sources)} mới, chưa có câu trích), {len(internal_required)} việc nội bộ.")


if __name__ == "__main__":
    main()
