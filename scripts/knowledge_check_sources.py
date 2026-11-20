"""Kiểm tra lại nguồn định kỳ (knowledge/README.md mục 6) – chạy hằng tháng và trước mỗi lần nạp.

Chạy:
  python scripts/knowledge_check_sources.py                                  # chỉ báo cáo
  python scripts/knowledge_check_sources.py --write knowledge/danh_muc_kham.xlsx
      # ghi kết quả vào cột 'Kiểm tự động' / 'Ngày kiểm tự động' của sheet Nguồn (nạp lại để vào hệ thống)

Với mỗi nguồn có link (cấp A/B hoặc có trích dẫn): tải lại trang, so mã băm với bản chụp mới nhất, kiểm từng câu trích
đã duyệt còn nguyên văn trên trang không. Trang đổi → lưu thêm bản chụp mới (câu trích vẫn trỏ bản chụp lúc duyệt).
Kết quả: Không đổi / Trang đổi, câu trích vẫn còn / Câu trích không còn / Link không mở được. Hai loại cuối → người
duyệt mở trang gốc xem lại. Máy KHÔNG tự xóa nguồn hay câu trích. Báo cáo: knowledge/sources/kiem_tra/<ngày>.md.
Mã thoát 1 nếu có nguồn cần xem lại.
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
from core.knowledge import default_know_dir, edit_sheet  # noqa: E402


def check_one(know: Path, code: str, url: str, quotes: list[str], now: datetime) -> tuple[str, str]:
    """→ (mã trạng thái, chi tiết)."""
    prev = ks.latest_snapshot(know, code)
    try:
        status, final_url, ctype, body = ks.fetch(url)
        if status >= 400:
            raise ValueError(f"HTTP {status}")
    except Exception as e:  # noqa: BLE001
        return "DEAD", f"{type(e).__name__}: {str(e)[:80]}"
    snap = ks.save_snapshot(know, code, url, final_url, status, ctype, body, now)
    text = ks.read_snapshot(know, snap.path)
    if not snap.text_ok or text is None:
        return ("UNCHANGED" if prev and prev.sha256 == snap.sha256 else "CHANGED_OK"), "PDF – không so được câu trích"
    result = ks.compare(prev.sha256 if prev else None, text, quotes)
    gone = [q for q in quotes if not ks.quote_in_text(q, text)]
    detail = f"bản chụp {snap.path}" + (f"; {len(gone)} câu không còn: " + " | ".join(q[:60] for q in gone) if gone else "")
    return result, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--write", default="", help="Sổ Excel để ghi kết quả vào sheet Nguồn")
    args = ap.parse_args()
    wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
    cat = load_visit_services()
    know = default_know_dir()
    now = datetime.now(TZ)
    quotes: dict[str, list[str]] = {}
    for f in cat.get("facts") or []:
        quotes.setdefault(f["source"], []).append(str(f.get("quote", "")))
    results: dict[str, tuple[str, str]] = {}
    for code, s in cat["sources"].items():
        url = s.get("url") or ""
        if wanted and code not in wanted:
            continue
        if not url or not (s.get("tier") in ("A", "B") or code in quotes):
            continue
        results[code] = check_one(know, code, url, quotes.get(code, []), now)
        st, detail = results[code]
        mark = "✗" if st in ks.CHECK_NEEDS_REVIEW else "•"
        print(f"  {mark} {code}: {ks.CHECK_STATUS[st]} – {detail}")

    out = know / "sources" / "kiem_tra" / f"{now:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Kiểm tra nguồn {now:%d/%m/%Y %H:%M}", "", f"Danh mục phiên bản {cat['version']}.", "",
             "| Mã | Kết quả | Chi tiết |", "|---|---|---|"]
    lines += [f"| {c} | {ks.CHECK_STATUS[st]} | {d.replace('|', '/')} |" for c, (st, d) in results.items()]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bad = [c for c, (st, _) in results.items() if st in ks.CHECK_NEEDS_REVIEW]
    print(f"\nĐã kiểm {len(results)} nguồn, {len(bad)} cần xem lại. Báo cáo: {out}")

    if args.write:
        day = now.date().isoformat()

        def upd(rec: dict[str, str]) -> dict[str, str]:
            r = results.get(rec.get("Mã", ""))
            return {"Kiểm tự động": r[0], "Ngày kiểm tự động": day} if r else {}

        n = edit_sheet(Path(args.write), "Nguồn", ["Kiểm tự động", "Ngày kiểm tự động"], upd)
        print(f"Đã ghi kết quả cho {n} nguồn vào {args.write} (nạp lại để vào hệ thống).")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
