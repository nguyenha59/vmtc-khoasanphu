"""Tạo file HIS GIẢ có kịch bản test cho màn Lịch hẹn (mỗi dòng một tình huống) + sheet kết quả mong đợi.

    python scripts/gen_his_test_cases.py [--date 12/11/2026]

Ghi data/synthetic/his_test_kich_ban_<yyyy-mm-dd>.xlsx (hiện trong danh sách "file mẫu" của màn Lịch hẹn).
Toàn bộ tên, số điện thoại, mã BN là GIẢ (dải mã 99xxxxxx). Sheet đầu là dữ liệu đúng định dạng config/his_columns.yaml;
sheet "Kich ban test" ghi tình huống, kết quả mong đợi và kết quả chạy thử thật (quy tắc / AI mock).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COLS = ["Mã BN", "Họ tên", "Điện thoại", "Quốc tịch", "Ngày hẹn", "Giờ bắt đầu", "Giờ kết thúc", "Dịch vụ",
        "Khoa/Phòng", "Bác sĩ", "Ghi chú", "Nguồn đặt", "Thời điểm đặt"]
KHOA = "Phòng khám Sản phụ khoa"
BS = "BS. Phạm Văn Khải"

# (mã, họ tên, SĐT, quốc tịch, bắt đầu, kết thúc, dịch vụ, khoa, ghi chú, nguồn đặt, tình huống, mong đợi, phụ thuộc AI)
CASES = [
    ("99000001", "Đinh Thị Mai Anh", "0900000101", "Việt Nam", "08:00", "08:30", "Siêu âm hình thái", KHOA, "", "Bác sĩ",
     "Siêu âm → đến sớm 15 phút theo bảng dịch vụ", "Giờ đến 07:45 · Tự điền", False),
    ("99000002", "Lục Thị Bảo Ngọc", "0900000102", "Việt Nam", "09:00", "09:15", "Khám thai", KHOA, "", "Bác sĩ",
     "Khám thai không ghi chú", "Giờ đến 09:00 · Tự điền", False),
    ("99000003", "Kha Thị Cẩm Tú", "0900000103", "Việt Nam", "12:30", "12:45", "Khám thai", KHOA, "", "Bác sĩ",
     "Lịch trong khung khám chờ 12:00–13:00", "Khám chờ (không tính giờ) · Tự điền", False),
    ("99000004", "Ông Thị Diệu Linh", "0900000104", "Việt Nam", "12:15", "12:30", "Khám thai", KHOA,
     "BS hẹn đến 11g45", "Bác sĩ",
     "Khám chờ nhưng ghi chú có giờ đến", "Khám chờ · Cần xác nhận (khám chờ nhưng ghi chú có giờ)", False),
    ("99000005", "Âu Thị Hồng Nhung", "0900000105", "Việt Nam", "10:00", "10:15", "Khám thai", KHOA,
     "BS hẹn đến 9g30 lấy máu", "Bác sĩ",
     "Bác sĩ ghi rõ giờ đến trong ghi chú", "Giờ đến 09:30 · Tự điền", False),
    ("99000006", "Cù Thị Kim Oanh", "0900000106", "Việt Nam", "14:00", "14:15", "Khám thai", KHOA,
     "Nhờ gọi xác nhận lại lịch", "Bác sĩ",
     "Ghi chú yêu cầu gọi xác nhận", "Giờ đến 14:00 · Cần xác nhận (ghi chú yêu cầu gọi)", False),
    ("99000007", "Tiêu Thị Phương Thảo", "0900000107", "Việt Nam", "15:00", "15:15", "Khám thai", KHOA,
     "Thai 38 tuần, khám định kỳ", "Bác sĩ",
     "Thai ≥ 37 tuần", "Giờ đến 15:00 · Cần xác nhận (tuần thai ≥ 37)", False),
    ("99000008", "La Thị Quỳnh Như", "0123 45", "Việt Nam", "08:30", "08:45", "Khám thai", KHOA, "", "Bác sĩ",
     "Số điện thoại sai", "Cảnh báo E06 · Cần xác nhận (SĐT không hợp lệ) · không vào file WorldSMS", False),
    ("99000009", "Mạc Thị Thu Hà", "0900000109", "Việt Nam", "07:30", "07:45", "Xét nghiệm máu", KHOA, "", "Bác sĩ",
     "Cùng mã BN 2 dịch vụ (xét nghiệm trước, khám sau) → gộp", "1 lịch gộp, giờ đến 07:30 · Tự điền", False),
    ("99000009", "Mạc Thị Thu Hà", "0900000109", "Việt Nam", "08:00", "08:15", "Khám thai", KHOA, "", "Bác sĩ",
     "(dòng thứ 2 của mã 99000009)", "(gộp vào dòng trên)", False),
    ("99000010", "Ninh Thị Xuân Mai", "0900000110", "Việt Nam", "08:00", "08:15", "Khám thai", KHOA, "", "Bác sĩ",
     "Cùng mã BN, dịch vụ nhịn ăn ở lịch muộn", "1 lịch gộp · Cần xác nhận (xung đột)", False),
    ("99000010", "Ninh Thị Xuân Mai", "0900000110", "Việt Nam", "10:00", "12:00",
     "Nghiệm pháp dung nạp glucose (NPDN)", KHOA, "", "Bác sĩ",
     "(dòng thứ 2 của mã 99000010)", "(gộp vào dòng trên)", False),
    ("99000011", "Viên Thị Hải Yến", "0900000111", "Việt Nam", "09:00", "09:15", "Khám nhi", "Phòng khám Nhi", "",
     "Bác sĩ", "Khác khoa", "Bị bỏ (không có trong danh sách lịch hẹn)", False),
    ("99000012", "Mei Tanaka", "0900000112", "Nhật Bản", "10:30", "10:45", "Khám thai", KHOA, "", "Bác sĩ",
     "Khách nước ngoài", "Giờ đến 10:30 · Tự điền · nằm trong file WorldSMS nước ngoài", False),
    ("99000013", "Doãn Thị Ánh Tuyết", "0900000113", "Việt Nam", "11:00", "11:15", "Khám phụ khoa", KHOA,
     "Autobooking", "",
     "Khách tự đặt (tiền tố Autobooking)", "Nguồn đặt = Autobooking · Giờ đến 11:00 · Tự điền", False),
    ("99000014", "Lã Thị Mười Bốn", "0900000114", "Việt Nam", "13:30", "13:45", "Khám thai", KHOA,
     "Chị Mười Bốn dặn gọi số 0912 345 678 trước khi đến", "Bác sĩ",
     "Ghi chú có tên + SĐT", "Che tên/SĐT trước khi gửi AI · Cần xác nhận (ghi chú yêu cầu gọi)", False),
    ("99000015", "Khúc Thị Ngân Hà", "0900000115", "Việt Nam", "05:30", "05:45", "Khám thai", KHOA, "", "Bác sĩ",
     "Giờ hẹn ngoài khung 06:00–20:00", "Cảnh báo E04 · Cần xác nhận (không quy tắc nào áp dụng)", False),
    ("99000016", "Thân Thị Diễm My", "0900000116", "Việt Nam", "09:30", "09:45", "Khám thai", KHOA,
     "Dặn đến sớm 30 phút làm thủ tục", "Bác sĩ",
     "Ghi chú 'đến sớm 30 phút' – chỉ AI đọc được", "Có AI: giờ đến 09:00 · Cần xác nhận (AI lệch quy tắc)\n"
     "Không AI: giờ đến 09:30 · Tự điền", True),
]


def build_rows(day: str) -> pd.DataFrame:
    booked = (datetime.strptime(day, "%d/%m/%Y").replace(hour=10) - timedelta(days=7)).strftime("%d/%m/%Y %H:%M")
    rows = [{"Mã BN": c[0], "Họ tên": c[1], "Điện thoại": c[2], "Quốc tịch": c[3], "Ngày hẹn": day,
             "Giờ bắt đầu": c[4], "Giờ kết thúc": c[5], "Dịch vụ": c[6], "Khoa/Phòng": c[7], "Bác sĩ": BS,
             "Ghi chú": c[8], "Nguồn đặt": c[9], "Thời điểm đặt": booked} for c in CASES]
    return pd.DataFrame(rows, columns=COLS)


def run_actual(path: Path, day: str) -> dict[str, tuple[str, str]]:
    """Chạy đúng luồng xử lý của app: quy tắc (không AI) và AI mock. Trả mã BN → (không AI, mock)."""
    from core.ai.llm_client import MockClient
    from core.config import load_his_columns, load_rules
    from core.ingest.his_reader import read_raw
    from core.pipeline import process_his
    from core.rules.arrival import arrival_text
    from core.rules.review import REASON_LABELS

    rules, cols = load_rules(), load_his_columns()
    d = datetime.strptime(day, "%d/%m/%Y").date()

    def describe(client) -> dict[str, str]:
        res = process_his(read_raw(path), cols, rules, d, client=client, ner=False)
        out = {}
        for p in res.planned:
            t = arrival_text(p.arrival, rules["arrival"]) or "—"
            why = ", ".join(REASON_LABELS.get(r, r).lower() for r in p.decision.reasons)
            out[p.merged.pid] = f"{t} · " + ("Cần xác nhận (" + why + ")" if why else "Tự điền")
        for r in res.dropped:
            out[r.pid] = "Bị bỏ (khác khoa)"
        warn = {i.row_no: i.code for i in res.report.row_issues}
        for r in res.rows:
            if r.row_no in warn and r.pid in out:
                out[r.pid] = f"[{warn[r.row_no]}] " + out[r.pid]
        return out

    no_ai, mock = describe(None), describe(MockClient())
    return {pid: (no_ai.get(pid, "—"), mock.get(pid, "—")) for pid in {c[0] for c in CASES}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="12/11/2026", help="ngày hẹn dd/mm/yyyy (chọn đúng ngày này khi tải file)")
    day = ap.parse_args().date
    iso = datetime.strptime(day, "%d/%m/%Y").date().isoformat()
    out = ROOT / "data" / "synthetic" / f"his_test_kich_ban_{iso}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)

    data = build_rows(day)
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        data.to_excel(xw, sheet_name="Lich hen", index=False)
    actual = run_actual(out, day)

    scen = []
    seen: set[str] = set()
    for i, c in enumerate(CASES, start=2):
        first = c[0] not in seen
        seen.add(c[0])
        a = actual.get(c[0], ("—", "—")) if first else ("", "")
        scen.append({"Dòng Excel": i, "Mã BN": c[0], "Tình huống": c[10], "Kết quả mong đợi": c[11],
                     "Chạy thử – không AI": a[0], "Chạy thử – AI mock": a[1],
                     "Phụ thuộc AI": "Có" if c[12] else ""})
    with pd.ExcelWriter(out, engine="openpyxl", mode="a") as xw:
        pd.DataFrame(scen).to_excel(xw, sheet_name="Kich ban test", index=False)

    # Định dạng: font Arial, tiêu đề đậm, cột rộng, xuống dòng
    wb = load_workbook(out)
    head = PatternFill("solid", fgColor="DDEBF7")
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                cell.font = Font(name="Arial", size=10, bold=cell.row == 1)
                cell.alignment = Alignment(vertical="top", wrap_text=ws.title == "Kich ban test")
                if cell.row == 1:
                    cell.fill = head
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(10, width + 2), 60)
        ws.freeze_panes = "A2"
    note = wb["Kich ban test"]
    r = note.max_row + 2
    note.cell(r, 1, "Cách test: màn Lịch hẹn → chọn ngày " + day + " → tải file này (hoặc chọn ở danh sách file mẫu).")
    note.cell(r + 1, 1, "Dữ liệu GIẢ hoàn toàn (mã BN 99xxxxxx). Cột “Chạy thử” do script chạy đúng luồng của app tạo ra.")
    note.cell(r + 2, 1, "Dòng phụ thuộc AI: kết quả với Ollama có thể khác mock.")
    for k in range(3):
        note.cell(r + k, 1).font = Font(name="Arial", size=10, italic=True)
    wb.save(out)
    print(out)


if __name__ == "__main__":
    main()
