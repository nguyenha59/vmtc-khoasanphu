"""Quyết định 'Cần xác nhận' (docs/04 A5)."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import Rules
from core.rules.arrival import ArrivalResult, NoteAI
from core.rules.note_patterns import find_ga
from core.textutil import contains_keyword

REASON_ORDER = [
    "AI_FAILED", "LOW_CONF", "CALL_NOTE", "LATE_GA", "CONFLICT", "AI_RULE_DIFF", "WAITING_NOTE_TIME", "WAITING_MIXED", "BAD_PHONE", "PII_LEFT",
    "RECHECK", "URGENT", "SHARED_PHONE", "FASTING_CHECK", "DOCTOR_SHORT", "INFERRED_SERVICES", "NO_GA", "NO_PID", "SCOPE_CHECK", "CLINIC_CLOSED", "OPEN_TIME", "SVC_TIME", "OVER_CAP", "LAB_CONFIRM", "AI_MINUTES", "ABBR_GUESS", "SERVICE_CONFIRM", "NO_RULE",
]

REASON_LABELS = {
    "AI_FAILED": "AI không chạy được (lỗi kết nối / hết ngân sách) – kiểm tra theo ghi chú",
    "LOW_CONF": "AI không chắc chắn",
    "CALL_NOTE": "Ghi chú yêu cầu gọi / xác nhận",
    "LATE_GA": "Tuần thai ≥ {late_ga_weeks}",  # ngưỡng lấy từ arrival.late_ga_weeks (reason_label)
    "CONFLICT": "Hai lịch cùng ngày cách nhau hơn 3 tiếng hoặc lịch muộn cần nhịn ăn – một tin nhắn chỉ báo giờ sớm nhất",
    "RECHECK": "Đã xác nhận lần trước nhưng tính lại ra giờ / dịch vụ khác – xác nhận lại",
    "SHARED_PHONE": "Hai khách khác nhau dùng chung số điện thoại – xác nhận người nhận tin",
    "FASTING_CHECK": "Chỉ ghi chuẩn bị hồ sơ thủ thuật – hỏi lại hôm nay có làm / có nhịn ăn không",
    "AI_RULE_DIFF": "AI và quy tắc lệch nhau",
    "WAITING_NOTE_TIME": "Khám chờ nhưng ghi chú có giờ đến",
    "WAITING_MIXED": "Khám chờ kèm lịch giờ thật lệch nhiều",
    "BAD_PHONE": "Số điện thoại không hợp lệ",
    "PII_LEFT": "Che dữ liệu chưa sạch – không gửi AI",
    "NO_RULE": "Không quy tắc nào áp dụng được",
    "LAB_CONFIRM": "Có xét nghiệm máu / nước tiểu – xác nhận có phải chờ kết quả trước khi khám không",
    "OVER_CAP": "Tổng thời gian dịch vụ vượt giới hạn an toàn (đến quá sớm) – kiểm lại",
    "AI_MINUTES": "AI không ước lượng được số phút đến trước có căn cứ – đang dùng số dự phòng (mã cũ, trước 18/11/2026)",
    "NO_PID": "Khách tự đặt (Autobooking) chưa có mã bệnh nhân – tìm / tạo hồ sơ",
    "URGENT": "Có dấu hiệu cần cấp cứu sản (chuyển dạ / ra máu / rỉ ối / thai ngoài tử cung) – gọi khách ngay",
    "DOCTOR_SHORT": "Giờ bác sĩ ghi ít hơn thời gian tối thiểu của các dịch vụ làm trước",
    "INFERRED_SERVICES": "Dịch vụ suy ra từ tuần thai / loại khám (ghi chú không ghi) – xác nhận",
    "NO_GA": "Khám thai nhưng ghi chú không có tuần thai – hỏi lại",
    "SCOPE_CHECK": "Ghi chú nhắc hỗ trợ sinh sản (IVF…) – kiểm tra có thuộc khoa Sản phụ khoa không",
    "CLINIC_CLOSED": "Lịch vào ngày / buổi phòng khám nghỉ (chiều thứ 7, chủ nhật)",
    "OPEN_TIME": "Giờ đến sớm hơn giờ phòng khám nhận khách – kiểm lại / đổi giờ hẹn",
    "SVC_TIME": "Ghi chú có giờ hẹn riêng cho dịch vụ làm trước (vd 'có hẹn 9h30') – kiểm lại giờ đến",
    "ABBR_GUESS": "Ghi chú có viết tắt chưa được xác nhận nghĩa (vd kgm, tkss) – xem danh mục tri thức",
    "SERVICE_CONFIRM": "Dịch vụ suy ra theo tuần thai cần xác nhận (vd test đường huyết – nhịn ăn)",
}


def reason_label(code: str, rules: Rules) -> str:
    """Nhãn lý do cần xác nhận; số ngưỡng đọc từ rules.yaml (không viết cứng)."""
    label = REASON_LABELS.get(code, code)
    return label.format(late_ga_weeks=rules["arrival"]["late_ga_weeks"]) if "{" in label else label


@dataclass
class ReviewDecision:
    status: str  # auto | needs_review
    reasons: list[str]
    ga_weeks: int | None
    ga_days: int | None


def is_call_note(note: str, keywords: list[str]) -> bool:
    return any(contains_keyword(note, k) for k in keywords)


def decide(
    *,
    arrival: ArrivalResult,
    note: str,
    phone_ok: bool,
    conflict: bool,
    ai: NoteAI | None,
    ai_attempted: bool,
    mask_ok: bool,
    rules: Rules,
    ai_failed: bool = False,
) -> ReviewDecision:
    cfg = rules["arrival"]
    reasons: set[str] = set(arrival.reasons)

    # AI không trả lời được lần nào (lỗi mạng, hết ngân sách) ≠ AI trả lời nhưng không chắc: báo đúng nguyên nhân.
    if ai_attempted and ai_failed:
        reasons.add("AI_FAILED")
    elif ai_attempted and (ai is None or not ai.ok or ai.confidence < cfg["ai_confidence_min"]):
        reasons.add("LOW_CONF")
    if note and is_call_note(note, cfg["call_note_keywords"]):
        reasons.add("CALL_NOTE")
    if ai is not None and ai.ok and ai.asks_confirmation:
        reasons.add("CALL_NOTE")

    ga_w: int | None = None
    ga_d: int | None = None
    if (ai is not None and ai.ok and ai.ga_weeks is not None and ai.ga_grounded
            and ai.ga_confidence is not None and ai.ga_confidence >= cfg["ai_confidence_min"]):
        ga_w, ga_d = ai.ga_weeks, ai.ga_days
    else:
        found = find_ga(note) if note else None
        if found:
            ga_w, ga_d = found
    # Bác sĩ đã ghi rõ giờ đến và hệ thống dùng đúng giờ đó → không cần gọi xác nhận vì tuần thai (người dùng 17/11/2026).
    doctor_time = (cfg.get("late_ga_skip_if_doctor_time", False) and arrival.explicit_time is not None
                   and arrival.arrival_time == arrival.explicit_time)
    if ga_w is not None and ga_w >= cfg["late_ga_weeks"] and not doctor_time:
        reasons.add("LATE_GA")
    if conflict:
        reasons.add("CONFLICT")
    if not phone_ok:
        reasons.add("BAD_PHONE")
    if not mask_ok:
        reasons.add("PII_LEFT")
    if arrival.arrival_time is None and not arrival.waiting:
        reasons.add("NO_RULE")

    ordered = sorted(reasons, key=REASON_ORDER.index)
    return ReviewDecision(
        status="needs_review" if ordered else "auto", reasons=ordered, ga_weeks=ga_w, ga_days=ga_d
    )
