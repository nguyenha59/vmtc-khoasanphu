"""Đóng gói "Dữ liệu kiểm thử (Mock Data & Schema)" vào thư mục mock-data/ để chia sẻ (repo công khai).

Chạy: python scripts/gen_synthetic.py && python scripts/make_oh_demo.py && python scripts/export_mock_package.py
CHỈ dữ liệu GIẢ (scripts/gen_synthetic.py, scripts/make_oh_demo.py) – không bao giờ chép file thật vào đây.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ai.note_extractor import note_schema  # noqa: E402
from core.config import load_his_columns, load_visit_services  # noqa: E402

OUT = ROOT / "mock-data"
SYN = ROOT / "data" / "synthetic"

FILES = {
    "oh": ["his_oh_demo_2026-11-18.xlsx", "his_oh_2026-11-18.xlsx", "his_oh_2026-11-19.xlsx", "his_oh_2026-11-20.xlsx"],
    "legacy-his": ["his_export_2026-11-18.xlsx", "his_export_2026-11-19.xlsx", "his_export_2026-11-20.xlsx",
                   "his_error_bad_time.xlsx", "his_error_missing_col.xlsx", "his_error_swapped_cols.xlsx"],
    "tables": ["patients.csv", "appointments_all.csv", "arrivals_all.csv", "vitals.csv", "form_responses.csv",
               "form_expected.csv"],
}
GOLDEN = ["oh_notes.yaml", "notes_answers.csv", "notes_eval.csv", "expected_arrivals_2026-11-13.csv",
          "his_day_2026-11-13.xlsx", "worldsms_expected_VN_2026-11-13.xlsx", "worldsms_expected_NN_2026-11-13.xlsx",
          "form_expected.csv", "question_selection_gold.yaml"]


def db_schema_md() -> str:
    from sqlalchemy import inspect as sa_inspect

    from core.models import Base

    lines = ["## 2. Cơ sở dữ liệu (core/models.py)", "",
             "Trường có hậu tố `_enc` được mã hóa Fernet (khóa `APP_SECRET_KEY`).", ""]
    for table in Base.metadata.sorted_tables:
        lines += [f"### `{table.name}`", "", "| Cột | Kiểu | Khóa / ràng buộc |", "|---|---|---|"]
        for col in table.columns:
            flags = []
            if col.primary_key:
                flags.append("PK")
            for fk in col.foreign_keys:
                flags.append(f"FK → {fk.target_fullname}")
            if not col.nullable and not col.primary_key:
                flags.append("bắt buộc")
            if col.index:
                flags.append("index")
            lines.append(f"| `{col.name}` | {col.type} | {', '.join(flags)} |")
        lines.append("")
    del sa_inspect
    return "\n".join(lines)


def oh_schema_md() -> str:
    his = load_his_columns()
    oh = ["HN", "Patient Name", "Appointment Start Date Time", "Appointment Resource", "Appointment Comment", "Mobile"]
    desc = {
        "HN": "Mã bệnh nhân. Khách tự đặt (Autobooking) có thể trống → mã tạm TAM-… + Cần xác nhận",
        "Patient Name": "Họ tên (mã hóa khi lưu)",
        "Appointment Start Date Time": "Ngày + giờ hẹn trong một ô (giờ GẶP BÁC SĨ)",
        "Appointment Resource": "Tên bác sĩ",
        "Appointment Comment": "Ghi chú – nguồn DUY NHẤT để suy ra loại khám, dịch vụ, tuần thai, giờ dặn đến",
        "Mobile": "Số điện thoại (mã hóa khi lưu, hiển thị dạng che)",
    }
    lines = ["## 1. File lịch hẹn xuất từ OH (đầu vào)", "",
             "| Cột trong file | Trường chuẩn | Mô tả |", "|---|---|---|"]
    lines += [f"| `{c}` | `{his.columns[c]}` | {desc[c]} |" for c in oh]
    lines += ["", f"Bắt buộc: {', '.join(f'`{r}`' for r in his.required)} (`a|b` = một trong hai).",
              "Ghi chú khách tự đặt: tiền tố `Autobooking_…`; phần kênh / cộng tác viên / mã đơn được cắt bỏ trước khi đọc.",
              ""]
    return "\n".join(lines)


def knowledge_md() -> str:
    cat = load_visit_services()
    lines = ["## 4. Danh mục tri thức khám (config/visit_services.yaml ↔ knowledge/danh_muc_kham.xlsx)", "",
             f"Phiên bản {cat['version']}. Gồm:", "",
             f"- `services` ({len(cat['services'])}): mã dịch vụ → tên, từ khóa, số phút dự phòng, tối thiểu có nguồn, "
             "giới hạn an toàn, nhịn ăn, nguồn",
             f"- `visit_types` ({len(cat['visit_types'])}): loại khám → từ khóa, dịch vụ thường làm, nguồn",
             f"- `abbreviations` ({len(cat.get('abbreviations', []))}): viết tắt → mã, trạng thái",
             f"- `ga_milestones` ({len(cat.get('ga_milestones', []))}): khoảng tuần thai → dịch vụ",
             f"- `sources` ({len(cat['sources'])}): nguồn (cấp A/B/C/X, đã kiểm trang gốc hay chưa)",
             f"- `facts` ({len(cat.get('facts', []))}): câu trích nguyên văn từ nguồn – AI chỉ dùng các câu này",
             "- `visit_flow`: giờ hẹn = giờ gặp bác sĩ; dịch vụ làm trước; thời gian tiếp đón", "",
             "| Mã dịch vụ | Tên | Tối thiểu có nguồn (phút) |", "|---|---|---|"]
    lines += [f"| `{c}` | {s['name']} | {s.get('min_minutes', '')} |" for c, s in cat["services"].items()]
    lines += ["", "| Mã loại khám | Tên | Dịch vụ thường làm |", "|---|---|---|"]
    lines += [f"| `{c}` | {v['name']} | {', '.join(v['services'])} |" for c, v in cat["visit_types"].items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    for sub in [*FILES, "golden", "schema"]:  # giữ README.md (viết tay)
        if (OUT / sub).exists():
            shutil.rmtree(OUT / sub)
    for sub, names in FILES.items():
        (OUT / sub).mkdir(parents=True, exist_ok=True)
        for n in names:
            shutil.copy(SYN / n, OUT / sub / n)
    (OUT / "golden").mkdir(parents=True, exist_ok=True)
    for n in GOLDEN:
        shutil.copy(ROOT / "tests" / "golden" / n, OUT / "golden" / n)
    (OUT / "schema").mkdir(parents=True, exist_ok=True)
    cat = load_visit_services()
    (OUT / "schema" / "ai_note_output.schema.json").write_text(
        json.dumps(note_schema(cat), ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy(ROOT / "config" / "his_columns.yaml", OUT / "schema" / "his_columns.yaml")
    shutil.copy(ROOT / "config" / "visit_services.yaml", OUT / "schema" / "visit_services.yaml")
    shutil.copy(ROOT / "knowledge" / "danh_muc_kham.xlsx", OUT / "schema" / "danh_muc_kham.xlsx")

    schema = ["# Schema dữ liệu – Trợ lý lịch hẹn & theo dõi thai kỳ", "",
              "Sinh tự động bởi `scripts/export_mock_package.py`.", "", oh_schema_md(), db_schema_md(),
              "## 3. Kết quả AI đọc ghi chú (JSON)", "",
              "Xem `schema/ai_note_output.schema.json`. AI chỉ được chọn mã trong danh mục (enum); số phút đến trước phải "
              "kèm mã nguồn đã kiểm (`minutes_basis`).", "", knowledge_md()]
    (OUT / "SCHEMA.md").write_text("\n".join(schema), encoding="utf-8")
    print(f"Đã đóng gói {OUT}")


if __name__ == "__main__":
    main()
