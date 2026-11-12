"""Che dữ liệu cá nhân trước khi gửi mô hình ngôn ngữ (docs/05 mục 1).

Còn sót số điện thoại / CCCD / tên → MaskResult(ok=False): KHÔNG gửi cho LLM.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.textutil import deaccent, nfc

log = logging.getLogger(__name__)

# SĐT: 0xxx / +84xxx / 84xxx, cho phép dấu cách, chấm, gạch giữa các số.
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){9,10}(?!\d)")
# CCCD 12 số, kể cả viết tách nhóm "0123 4567 8901".
CCCD_RE = re.compile(r"(?<!\d)\d(?:[\s.\-]?\d){11}(?!\d)")
# CMND cũ 9 số.
CMND_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")
# Lưới an toàn ở lần quét 2: mọi dãy ≥ 9 chữ số (kể cả có dấu tách) đều coi là còn sót.
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d(?:[\s.\-]?\d){8,}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PLACEHOLDER_RE = re.compile(r"\[(?:SĐT|CCCD|PID|EMAIL|TÊN|BS)\]")

NerFn = Callable[[str], list[str]]


@dataclass
class MaskResult:
    text: str
    ok: bool
    replaced: dict[str, int] = field(default_factory=dict)
    leftovers: list[str] = field(default_factory=list)  # chỉ nhãn loại, KHÔNG chứa giá trị
    # Tên người NER tìm thấy trong toàn văn – chỉ dùng trong bộ nhớ để che đoạn trích (mask_fragment); không lưu, không in.
    persons: list[str] = field(default_factory=list, repr=False)


def _underthesea_ner(text: str) -> list[str]:
    """Trả danh sách thực thể người (PER) do underthesea nhận dạng."""
    from underthesea import ner  # nhập muộn vì nạp mô hình chậm

    persons: list[str] = []
    current: list[str] = []
    for token in ner(text):
        word, tag = token[0], token[-1]
        if tag == "B-PER":
            if current:
                persons.append(" ".join(current))
            current = [word]
        elif tag == "I-PER" and current:
            current.append(word)
        else:
            if current:
                persons.append(" ".join(current))
            current = []
    if current:
        persons.append(" ".join(current))
    return persons


_default_ner: NerFn | None = None


def default_ner() -> NerFn | None:
    global _default_ner
    if _default_ner is None:
        try:
            import underthesea  # noqa: F401

            _default_ner = _underthesea_ner
        except Exception:  # pragma: no cover - thiếu thư viện
            log.warning("Không có underthesea – bỏ qua bước NER khi che dữ liệu.")
            return None
    return _default_ner


# Đệm tên tiếng Việt không tự định danh một người – không che riêng lẻ để khỏi làm hỏng ghi chú.
_NAME_PARTICLES = {"thị", "văn", "thi", "van"}


def _name_variants(full_name: str) -> list[str]:
    """Họ tên đầy đủ, từng từ trong tên và từng cặp từ liền nhau, kèm dạng không dấu.

    Che mọi từ (không chỉ từ cuối) vì tên nước ngoài có thể đặt tên gọi trước họ
    ("Yuki Tanaka", "Emily Smith") hoặc tên gọi hai âm ("Kim Min Ji").
    """
    full_name = nfc(full_name).strip()
    if not full_name:
        return []
    parts = full_name.split()
    variants = {full_name}
    variants |= {p for p in parts if p.lower() not in _NAME_PARTICLES}
    variants |= {" ".join(parts[i : i + 2]) for i in range(len(parts) - 1)}
    # Dạng không dấu: từ đơn ngắn ("Đỗ"→"do", "Hồ"→"ho", "Lê"→"le") trùng chữ thường trong ghi chú
    # không dấu → chỉ thêm dạng không dấu cho cụm nhiều từ hoặc từ dài ≥ 4 ký tự.
    variants |= {deaccent(v) for v in list(variants) if " " in v or len(v) >= 4 or deaccent(v) == v}
    return sorted((v for v in variants if len(v) >= 2), key=len, reverse=True)


def _sub_words(text: str, words: list[str], placeholder: str) -> tuple[str, int]:
    n = 0
    for w in words:
        pattern = re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.IGNORECASE)
        text, k = pattern.subn(placeholder, text)
        n += k
    return text, n


def _regex_pass(text: str, pid: str | None) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    # PID trước: PID có thể đủ dài để trùng mẫu số giấy tờ.
    if pid:
        text, counts["PID"] = re.subn(rf"(?<!\w){re.escape(pid)}(?!\w)", "[PID]", text)
    text, counts["SĐT"] = PHONE_RE.subn("[SĐT]", text)
    text, counts["CCCD"] = CCCD_RE.subn("[CCCD]", text)
    text, k = CMND_RE.subn("[CCCD]", text)
    counts["CCCD"] += k
    text, counts["EMAIL"] = EMAIL_RE.subn("[EMAIL]", text)
    return text, counts


def _ner_persons(text: str, ner: NerFn | None) -> list[str]:
    if ner is None:
        return []
    stripped = PLACEHOLDER_RE.sub(" ", text)
    try:
        found = ner(stripped)
    except Exception as e:  # pragma: no cover
        log.warning("NER lỗi: %s", e)
        return []
    # Bỏ chữ viết tắt y khoa toàn chữ hoa (ĐTĐTK, THA, SA…) mà NER hay nhận nhầm là tên người.
    return [
        p for p in found
        if len(p.strip()) >= 2 and not PLACEHOLDER_RE.fullmatch(p.strip()) and p.upper() != p
    ]


def mask_text(
    text: str,
    *,
    full_name: str | None = None,
    pid: str | None = None,
    doctor_names: list[str] | None = None,
    ner: NerFn | None | bool = True,
) -> MaskResult:
    """Che SĐT, CCCD, PID, email, tên khách, tên bác sĩ; quét lại lần 2."""
    ner_fn: NerFn | None = default_ner() if ner is True else (ner or None)  # type: ignore[assignment]
    text = nfc(text or "")
    if not text.strip():
        return MaskResult(text=text, ok=True)

    masked, counts = _regex_pass(text, pid)
    name_words = _name_variants(full_name or "")
    masked, counts["TÊN"] = _sub_words(masked, name_words, "[TÊN]")
    doc_words: list[str] = []
    for dn in doctor_names or []:
        clean = re.sub(r"^(BS\.?|Bác sĩ|ThS\.?|TS\.?)\s*", "", nfc(dn), flags=re.IGNORECASE)
        doc_words += _name_variants(clean)
    masked, counts["BS"] = _sub_words(masked, sorted(set(doc_words), key=len, reverse=True), "[BS]")

    persons = _ner_persons(masked, ner_fn)
    if persons:
        masked, k = _sub_words(masked, sorted(set(persons), key=len, reverse=True), "[TÊN]")
        counts["TÊN"] = counts.get("TÊN", 0) + k

    # Lần quét thứ 2
    leftovers: list[str] = []
    for rx, label in ((LONG_NUMBER_RE, "SỐ"), (EMAIL_RE, "EMAIL")):
        leftovers += [label for _m in rx.finditer(masked)]
    if pid and re.search(rf"(?<!\w){re.escape(pid)}(?!\w)", masked):
        leftovers.append("PID")
    for w in name_words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", masked, re.IGNORECASE):
            leftovers.append("TÊN")
    leftovers += ["NER" for _p in _ner_persons(masked, ner_fn)]
    return MaskResult(
        text=masked, ok=not leftovers, replaced={k: v for k, v in counts.items() if v}, leftovers=leftovers,
        persons=sorted(set(persons)),
    )


def mask_fragment(fragment: str, *, full_name: str | None, pid: str | None, persons: list[str]) -> str:
    """Che một đoạn trích (bằng chứng) của văn bản gốc trước khi LƯU. Dùng lại tên NER đã tìm trong toàn văn.
    Che không sạch → trả rỗng (không lưu gì còn hơn lưu dữ liệu cá nhân)."""
    if not (fragment or "").strip():
        return ""
    def present(t: str) -> list[str]:  # chỉ tên còn thật sự xuất hiện (lần quét 2 chạy trên văn bản đã che)
        return [p for p in persons if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", t, re.IGNORECASE)]

    r = mask_text(fragment, full_name=full_name, pid=pid, ner=present)
    return r.text if r.ok else ""
