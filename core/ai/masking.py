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

# SĐT: 0xxx / +84xxx / 84xxx / (+84), cho phép dấu cách, chấm, gạch giữa các số.
PHONE_RE = re.compile(r"(?<!\d)(?:\(?\+?84\)?|0)(?:[\s.\-]?\d){9,10}(?!\d)")
# Thẻ BHYT: 2 chữ + 13 số (DN4010123456789).
BHYT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{13}(?!\d)")
# Hộ chiếu: 1–2 chữ + 7–8 số (C1234567, B9876543).
PASSPORT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,2}\d{7,8}(?![A-Za-z0-9])")
# Mã số 7–8 chữ số đứng riêng (PID / mã BN của người khác).
CODE_RE = re.compile(r"(?<![\d/.:,])\d{7,8}(?![\d/.:,])")
# Đường dẫn / mạng xã hội.
URL_RE = re.compile(r"(?:https?://|www\.|(?:fb|facebook|zalo|instagram|tiktok)\.(?:com|me|vn)/)\S+", re.IGNORECASE)
# Ngày sinh: "sinh ngày 12/03/1995", "ns 1995", "DOB 12-03-1995"; ngày có năm 4 số trước 2020.
DOB_RE = re.compile(
    r"(?:(?:ngày|ngay)\s+sinh|sinh\s+(?:ngày|ngay|năm|nam)|\bns|\bdob)\s*:?\s*(?:\d{1,2}[/.\-]\d{1,2}[/.\-])?\d{4}",
    re.IGNORECASE,
)
OLD_DATE_RE = re.compile(r"(?<!\d)\d{1,2}[/.\-]\d{1,2}[/.\-](?:19\d{2}|200\d|201\d)(?!\d)")
# Chữ cái viết hoa tiếng Việt (không dùng dải À-Ỹ vì dải đó lẫn cả chữ thường như "đ").
_UP = "A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ"
_CAP_WORD = rf"[{_UP}][\w.]*"
# Địa chỉ: từ khóa địa chỉ CHỈ tính khi theo sau là số hoặc chữ viết hoa ("đường Láng", "quận 3", "P.Bến Nghé")
# → không che nhầm "đường huyết", "tiểu đường", "dương tính", "quan trọng". Lấy tới dấu phẩy / chấm phẩy,
# nối tiếp đoạn sau dấu phẩy nếu đoạn đó cũng là địa chỉ hoặc chỉ gồm ≤ 4 từ viết hoa (tên phường / quận).
_ADDR_WORD = (
    r"(?:số\s*nhà|nhà\s*số|so\s*nha|nha\s*so|ngõ|ngách|hẻm|kiệt|đường|phố|phường|quận|huyện|thị\s*xã|thôn|xóm|"
    r"tổ\s*dân\s*phố|khu\s*phố|chung\s*cư|tòa|toà|ngo|ngach|hem|duong|pho|phuong|quan|huyen|thon|xom|"
    r"P\.|Q\.|TP\.?|ở|o)"
)
_ADDR_KEY = rf"(?:(?:địa\s*chỉ|dia\s*chi|\bđc|\bdc)\s*:?|\b{_ADDR_WORD}(?=\s*(?:\d|[{_UP}])))"
_CAPS_SEG = rf"{_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}}(?=\s*(?:[,;\n]|$))"
ADDRESS_RE = re.compile(
    rf"{_ADDR_KEY}[^,;\n]{{0,60}}(?:,\s*(?:{_ADDR_KEY}[^,;\n]{{0,40}}|{_CAPS_SEG}))*"
)
# "tên Hùng", "ten hung", "tên là Trần Tuấn": viết hoa → tới 3 từ; viết thường → 1 từ.
NAMED_RE = re.compile(rf"\b(?:tên|ten)(?:\s+(?:là|la))?\s+(?:{_CAP_WORD}(?:\s+{_CAP_WORD}){{0,2}}|[a-zđ]\w+)")
# CCCD 12 số, kể cả viết tách nhóm "0123 4567 8901".
CCCD_RE = re.compile(r"(?<!\d)\d(?:[\s.\-]?\d){11}(?!\d)")
# CMND cũ 9 số.
CMND_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")
# Lưới an toàn ở lần quét 2: mọi dãy ≥ 9 chữ số (kể cả có dấu tách) đều coi là còn sót.
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d(?:[\s.\-]?\d){8,}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PLACEHOLDER_RE = re.compile(r"\[(?:SĐT|CCCD|PID|EMAIL|TÊN|BS|BHYT|HỘ CHIẾU|MÃ|LINK|NGÀY SINH|ĐỊA CHỈ)\]")

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

# Thuật ngữ y khoa có chữ trùng họ / tên người ("Dương" – dương tính, "Đường" – đường huyết, "Hồng" – hồng cầu,
# "Tâm" – tâm thu…). Giữ nguyên khi che tên, nếu không ghi chú mất nghĩa ("HPV [TÊN] tính 16").
# So cả dạng có dấu và không dấu, không phân biệt hoa thường; chỉ giữ đúng CỤM (một chữ "Dương" đứng riêng vẫn bị che).
_MEDICAL_TERMS = [
    "dương tính", "âm tính", "đường huyết", "đường máu", "đường niệu", "hồng cầu", "bạch cầu", "tiểu cầu",
    "tâm thu", "tâm trương", "huyết áp", "thai nhi", "tử cung", "cổ tử cung", "phụ khoa", "sản khoa",
]
_MEDICAL_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(sorted({re.escape(v) for t in _MEDICAL_TERMS for v in (t, deaccent(t))}, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)
_SHIELD_BASE = 0xE100  # ký tự vùng riêng (không phải \w) giữ chỗ cho cụm thuật ngữ trong lúc che


def _shield_terms(text: str) -> tuple[str, list[str]]:
    kept: list[str] = []

    def keep(m: re.Match[str]) -> str:
        kept.append(m.group(0))
        return chr(_SHIELD_BASE + len(kept) - 1)

    return _MEDICAL_RE.sub(keep, text), kept


def _unshield(text: str, kept: list[str]) -> str:
    for i, term in enumerate(kept):
        text = text.replace(chr(_SHIELD_BASE + i), term)
    return text


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
    text, counts["EMAIL"] = EMAIL_RE.subn("[EMAIL]", text)
    text, counts["LINK"] = URL_RE.subn("[LINK]", text)
    text, counts["BHYT"] = BHYT_RE.subn("[BHYT]", text)
    text, counts["NGÀY SINH"] = DOB_RE.subn("[NGÀY SINH]", text)
    text, k = OLD_DATE_RE.subn("[NGÀY SINH]", text)
    counts["NGÀY SINH"] += k
    text, counts["SĐT"] = PHONE_RE.subn("[SĐT]", text)
    text, counts["CCCD"] = CCCD_RE.subn("[CCCD]", text)
    text, k = CMND_RE.subn("[CCCD]", text)
    counts["CCCD"] += k
    text, counts["HỘ CHIẾU"] = PASSPORT_RE.subn("[HỘ CHIẾU]", text)
    text, counts["MÃ"] = CODE_RE.subn("[MÃ]", text)
    text, counts["ĐỊA CHỈ"] = ADDRESS_RE.subn("[ĐỊA CHỈ]", text)
    text, counts["TÊN"] = NAMED_RE.subn(lambda m: m.group(0).split()[0] + " [TÊN]", text)
    return text, counts


_NER_NOT_NAMES = {
    "tai", "tai kham", "pap", "pap smear", "kham", "sieu am", "thai", "be", "con", "doppler", "double", "triple",
    "npdn", "ogtt", "rh", "gbs", "hpv", "insulin",
}


def _ner_persons(text: str, ner: NerFn | None) -> list[str]:
    if ner is None:
        return []
    stripped = PLACEHOLDER_RE.sub(" ", text)
    try:
        found = ner(stripped)
    except Exception as e:  # pragma: no cover
        log.warning("NER lỗi: %s", e)
        return []
    # Bỏ chữ viết tắt y khoa toàn chữ hoa (ĐTĐTK, THA, SA…) và từ y khoa đầu câu ("Tái khám", "Pap smear")
    # mà NER hay nhận nhầm là tên người.
    return [
        p for p in found
        if len(p.strip()) >= 2 and not PLACEHOLDER_RE.fullmatch(p.strip()) and p.upper() != p
        and deaccent(p.strip()).lower() not in _NER_NOT_NAMES
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
    masked, shielded = _shield_terms(masked)
    name_words = _name_variants(full_name or "")
    masked, k = _sub_words(masked, name_words, "[TÊN]")
    counts["TÊN"] = counts.get("TÊN", 0) + k
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
    for rx, label in ((LONG_NUMBER_RE, "SỐ"), (EMAIL_RE, "EMAIL"), (PASSPORT_RE, "HỘ CHIẾU"), (URL_RE, "LINK"),
                      (CODE_RE, "MÃ")):
        leftovers += [label for _m in rx.finditer(masked)]
    if pid and re.search(rf"(?<!\w){re.escape(pid)}(?!\w)", masked):
        leftovers.append("PID")
    for w in name_words:
        if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", masked, re.IGNORECASE):
            leftovers.append("TÊN")
    leftovers += ["NER" for _p in _ner_persons(masked, ner_fn)]
    masked = _unshield(masked, shielded)
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
