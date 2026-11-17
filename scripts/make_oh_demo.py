"""Sinh file demo định dạng OH (giống file xuất thật: 6 cột) – DỮ LIỆU GIẢ, dùng quay video demo.

Chạy: python scripts/make_oh_demo.py [--date 2026-11-18]
→ data/synthetic/his_oh_demo_<ngày>.xlsx (hiện trong danh sách "File mẫu" của màn Lịch hẹn).
Ghi chú viết theo kiểu bác sĩ / tổng đài ghi trong OH (viết tắt, Autobooking_…), tên + SĐT + mã CTV đều giả.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# (giờ hẹn, bác sĩ, ghi chú) – mô phỏng các kiểu ghi chú thường gặp
ROWS = [
    ("09:00", "BS. Trần Minh Anh", "Autobooking_DRP_CTV_Phạm Thu Hà_CTV001201_dky gói thai 27w"),
    ("14:30", "BS. Lê Hoàng Nam", "Autobooking_MVM_ko tam ung_#500101_Có khí hư trắng"),
    ("08:30", "BS. Trần Minh Anh", "Autobooking_OVM_ko tam ung_#500102_khám thai"),
    ("08:30", "BS. Nguyễn Thu Trang", "Autobooking_MVM_ko tam ung_#500103_Chậm kinh"),
    ("09:00", "BS. Lê Hoàng Nam", "tái khám sau sinh thường"),
    ("09:00", "BS. Đỗ Quang Huy", "làm hs mổ u buồng trứng/khám gây mê"),
    ("11:00", "BS. Nguyễn Thu Trang", "Autobooking_DRP_CTV_Vũ Minh Khoa_CTV001202_Khám mốc 31 tuần"),
    ("10:30", "BS. Trần Minh Anh", "Autobooking_DRP_CTV_Vũ Minh Khoa_CTV001202_Siêu âm 4d tuần 31"),
    ("11:30", "BS. Đỗ Quang Huy", "21w/sa4d có hẹn 9h30"),
    ("12:00", "BS. Lê Hoàng Nam", "Chuẩn bị hồ sơ làm LEEP, KH tới 10h"),
    ("11:00", "BS. Nguyễn Thu Trang", "Thai sớm/Chưa loại trừ chuyển dạ /SA trước"),
    ("08:00", "BS. Trần Minh Anh", "31w3d, 4d + khám"),
    ("09:30", "BS. Đỗ Quang Huy", "Autobooking_MVM_ko tam ung_#500104_Thai 39w5d_CBNV_Vinmec_100201"),
    ("11:00", "BS. Lê Hoàng Nam", "Thai 39w6d/SA/CTG tới 08h30"),
    ("08:45", "BS. Nguyễn Thu Trang", "36w5, sa, ctg, khám gây mê đến 8h30"),
    ("10:30", "BS. Trần Minh Anh", "39w3d đến 8h"),
    ("14:00", "BS. Đỗ Quang Huy", "tk sau hút buồng tử cung"),
    ("09:00", "BS. Lê Hoàng Nam", "31w, 2d + khám"),
    ("08:40", "BS. Nguyễn Thu Trang", "17w5d"),
    ("16:00", "BS. Trần Minh Anh", "37w3d, đến 13h"),
]
NAMES = ["Nguyễn Thị Lan", "Trần Thu Hoa", "Lê Ngọc Mai", "Phạm Thị Hương", "Hoàng Thu Trang", "Vũ Thị Linh",
         "Đặng Ngọc Thảo", "Bùi Thị Ngân", "Đỗ Thu Vy", "Ngô Thị Nhung", "Dương Ngọc Yến", "Lý Thị Quyên",
         "Đinh Thu Hạnh", "Trịnh Thị Huyền", "Mai Ngọc Dung", "Tạ Thị Loan", "Hồ Thu Tuyết", "Phan Thị Phượng",
         "Võ Ngọc Oanh", "Huỳnh Thị Giang"]


NEW_SELF_BOOKED = {2, 4}  # dòng Autobooking chưa có mã bệnh nhân


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-11-18")
    args = ap.parse_args()
    d = date.fromisoformat(args.date)
    data = []
    for i, ((hhmm, doctor, note), name) in enumerate(zip(ROWS, NAMES, strict=True), start=1):
        hh, mm = (int(x) for x in hhmm.split(":"))
        data.append({
            # Khách tự đặt mới (Autobooking) thường chưa có HN trên OH → để trống như file thật
            "HN": "" if i in NEW_SELF_BOOKED else f"DEMO{i:04d}", "Patient Name": name,
            "Appointment Start Date Time": datetime(d.year, d.month, d.day, hh, mm),
            "Appointment Resource": doctor, "Appointment Comment": note,
            "Mobile": f"0900{i:06d}",
        })
    out = ROOT / "data" / "synthetic" / f"his_oh_demo_{d.isoformat()}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(data).to_excel(out, index=False)
    print(f"Đã tạo {out} – {len(data)} lịch (dữ liệu giả, định dạng OH).")


if __name__ == "__main__":
    main()
