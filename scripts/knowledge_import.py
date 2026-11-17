"""Nạp sổ Excel danh mục tri thức khám vào hệ thống (docs/17).

Chạy:
  python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx            # chỉ kiểm tra + xem thay đổi
  python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx --apply    # ghi (cần sheet Phê duyệt đủ thông tin)

Khi --apply: kiểm tra không lỗi → lưu bản cũ vào knowledge/history/ → tăng phiên bản → ghi config/visit_services.yaml
→ ghi knowledge/CHANGELOG.md → ghi audit_log (knowledge_import). Có lỗi → không ghi gì.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import CONFIG_DIR, TZ, load_env, load_visit_services  # noqa: E402
from core.knowledge import bump_minor, diff_summary, dump_yaml, from_workbook, validate  # noqa: E402

KNOW = Path(os.environ.get("APP_KNOWLEDGE_DIR", ROOT / "knowledge"))
REQUIRED_APPROVAL = ("Người duyệt", "Chức danh", "Ngày duyệt", "Nội dung thay đổi")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--apply", action="store_true", help="Ghi vào hệ thống (mặc định chỉ kiểm tra)")
    args = ap.parse_args()
    load_env()
    old = load_visit_services()
    new, approval = from_workbook(Path(args.xlsx), old)
    rep = validate(new)

    print(f"Sổ: {args.xlsx}  |  phiên bản đang chạy: {old['version']}")
    print("Thay đổi:")
    changes = diff_summary(old, new)
    for c in changes:
        print(f"  • {c}")
    for w in rep.warnings:
        print(f"  ⚠ {w}")
    if not rep.ok:
        print(f"\n✗ {len(rep.errors)} lỗi – KHÔNG nạp:")
        for e in rep.errors:
            print(f"  ✗ {e}")
        return 1
    if not args.apply:
        print("\n✓ Hợp lệ. Thêm --apply để nạp (cần điền sheet Phê duyệt).")
        return 0

    missing = [k for k in REQUIRED_APPROVAL if not approval.get(k)]
    if missing:
        print(f"\n✗ Sheet 'Phê duyệt' thiếu: {', '.join(missing)} – KHÔNG nạp.")
        return 1
    if changes == ["Không có thay đổi nội dung"]:
        print("\nKhông có thay đổi – không nạp.")
        return 0

    new_version = bump_minor(str(old["version"]))
    now = datetime.now(TZ)
    new["version"] = new_version
    new["checked_at"] = now.date().isoformat()
    new["approval"] = {"by": approval["Người duyệt"], "role": approval["Chức danh"], "at": approval["Ngày duyệt"],
                       "summary": approval["Nội dung thay đổi"], "imported_at": now.isoformat(timespec="seconds"),
                       "previous_version": old["version"]}

    target = Path(os.environ.get("APP_VISIT_SERVICES_FILE", CONFIG_DIR / "visit_services.yaml"))
    hist = KNOW / "history"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"visit_services_{old['version']}.yaml").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(dump_yaml(new), encoding="utf-8")
    tmp.replace(target)

    log = KNOW / "CHANGELOG.md"
    entry = [f"\n## {new_version} – {now:%d/%m/%Y %H:%M}",
             f"- Duyệt: {approval['Người duyệt']} ({approval['Chức danh']}), ngày {approval['Ngày duyệt']}",
             f"- Nội dung: {approval['Nội dung thay đổi']}", *[f"- {c}" for c in changes]]
    with open(log, "a", encoding="utf-8") as f:
        if log.stat().st_size == 0:
            f.write("# Nhật ký thay đổi danh mục tri thức khám\n")
        f.write("\n".join(entry) + "\n")

    try:  # luật 7: mọi thao tác sửa ghi audit_log
        from core import audit

        audit.log("knowledge_import", "visit_services", new_version,
                  {"from": old["version"], "to": new_version, "approved_by": approval["Người duyệt"],
                   "role": approval["Chức danh"], "changes": changes, "file": Path(args.xlsx).name})
    except Exception as e:  # noqa: BLE001 – không có CSDL (vd máy soạn) thì vẫn có CHANGELOG
        print(f"  ⚠ Không ghi được audit_log ({type(e).__name__}) – đã ghi knowledge/CHANGELOG.md")
    backup = hist / f"visit_services_{old['version']}.yaml"
    print(f"\n✓ Đã nạp phiên bản {new_version}. Bản cũ lưu ở {backup}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
