"""Kiểm tra file lịch hẹn (docs/04 A1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core.config import HisColumns, Rules, parse_hhmm
from core.ingest.his_reader import HisRow, missing_columns
from core.security import is_valid_phone


def _mask_raw_phone(raw: str | None) -> str:
    """SĐT sai định dạng vẫn có thể là số thật → chỉ hiện 3 số cuối (báo cáo lưu không mã hóa, hiện giao diện, audit)."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return str(raw or "")[:20]  # chữ ("không có", "xem ghi chú") – không phải số điện thoại
    return "•" * max(0, len(digits) - 3) + digits[-3:]

MESSAGES = {
    "E01": "Thiếu cột bắt buộc: {cols}. Kiểm tra lại file xuất từ HIS hoặc ánh xạ trong config/his_columns.yaml.",
    "E02": "Giờ bắt đầu trùng giờ kết thúc ở {pct:.0%} số dòng – nghi nhầm cột. Xuất lại file từ HIS, kiểm tra cột 'Giờ bắt đầu'.",
    "E03": "Dòng {row}: giờ bắt đầu ({start}) không trước giờ kết thúc ({end}).",
    "E04": "Dòng {row}: giờ {value} nằm ngoài khung {lo}–{hi}.",
    "E05": "Dòng {row}: ngày hẹn {value} khác ngày đã chọn {selected}.",
    "E06": "Dòng {row}: số điện thoại '{value}' không hợp lệ – dòng sẽ vào 'Cần xác nhận'.",
    "E07": "Dòng {rows}: thiếu mã bệnh nhân (HN/PID) và không phải khách tự đặt (Autobooking) – không phân biệt được khách, không nhập file. Sửa file rồi tải lại.",
    "E08": "Không có dòng nào thuộc khoa trong file – không nhập, giữ nguyên lịch hẹn đã có. Kiểm tra lại file / cột khoa.",
}


@dataclass
class Issue:
    code: str
    message: str
    row_no: int | None = None
    fatal: bool = False


@dataclass
class ValidationReport:
    fatal: list[Issue] = field(default_factory=list)
    row_issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fatal

    def issues_for(self, row_no: int) -> list[Issue]:
        return [i for i in self.row_issues if i.row_no == row_no]

    def as_dicts(self) -> list[dict[str, object]]:
        return [vars(i) for i in self.fatal + self.row_issues]


def check_columns(raw: pd.DataFrame, cfg: HisColumns) -> ValidationReport:
    rep = ValidationReport()
    missing = missing_columns(raw, cfg)
    if missing:
        rep.fatal.append(Issue("E01", MESSAGES["E01"].format(cols=", ".join(missing)), fatal=True))
    return rep


def check_kept_rows(kept: list[HisRow]) -> list[Issue]:
    """Sau khi lọc khoa: không còn dòng nào (E08) hoặc dòng thiếu PID (E07) → lỗi dừng, KHÔNG đụng lịch đã có.
    PID là khóa nhận diện khách: dòng thiếu PID sẽ gộp nhầm nhiều người vào một hồ sơ."""
    if not kept:
        return [Issue("E08", MESSAGES["E08"], fatal=True)]
    blank = [r.row_no for r in kept if not str(r.pid or "").strip()]
    if blank:
        shown = ", ".join(map(str, blank[:10])) + ("…" if len(blank) > 10 else "")
        return [Issue("E07", MESSAGES["E07"].format(rows=shown), fatal=True)]
    return []


def validate_rows(rows: list[HisRow], rules: Rules, selected_date: date | None) -> ValidationReport:
    rep = ValidationReport()
    lo = parse_hhmm(rules["arrival"]["valid_hours"]["from"])
    hi = parse_hhmm(rules["arrival"]["valid_hours"]["to"])

    with_times = [r for r in rows if r.start_time and r.end_time]
    if with_times:
        same = sum(1 for r in with_times if r.start_time == r.end_time)
        pct = same / len(with_times)
        if pct > 0.5:
            rep.fatal.append(Issue("E02", MESSAGES["E02"].format(pct=pct), fatal=True))
            return rep

    for r in rows:
        # File OH không có giờ kết thúc → chỉ bắt buộc giờ bắt đầu; có giờ kết thúc mới kiểm tra E03.
        if r.start_time is None:
            rep.row_issues.append(
                Issue("E04", MESSAGES["E04"].format(row=r.row_no, value="(trống/sai định dạng)", lo=lo.strftime("%H:%M"), hi=hi.strftime("%H:%M")), r.row_no)
            )
        else:
            if r.end_time is not None and r.start_time >= r.end_time:
                rep.row_issues.append(
                    Issue("E03", MESSAGES["E03"].format(row=r.row_no, start=r.start_time.strftime("%H:%M"), end=r.end_time.strftime("%H:%M")), r.row_no)
                )
            for t in (r.start_time, r.end_time):
                if t is not None and (t < lo or t > hi):
                    rep.row_issues.append(
                        Issue("E04", MESSAGES["E04"].format(row=r.row_no, value=t.strftime("%H:%M"), lo=lo.strftime("%H:%M"), hi=hi.strftime("%H:%M")), r.row_no)
                    )
                    break
        if selected_date and r.appt_date != selected_date:
            rep.row_issues.append(
                Issue("E05", MESSAGES["E05"].format(row=r.row_no, value=r.appt_date, selected=selected_date), r.row_no)
            )
        if not is_valid_phone(r.phone):
            rep.row_issues.append(Issue("E06", MESSAGES["E06"].format(row=r.row_no, value=_mask_raw_phone(r.phone_raw)),
                                  r.row_no))
    for r in rows:
        r.warnings = [i.code for i in rep.issues_for(r.row_no)]
    return rep
