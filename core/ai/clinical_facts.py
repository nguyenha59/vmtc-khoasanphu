"""Source-bound clinical assertions shared by symptom and measurement readers.

This is a conservative Vietnamese assertion parser, not a diagnosis model. A fact
keeps its source span and the scope of subject, negation, time and modality so a
keyword alone cannot establish that a patient currently has a symptom.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from core.textutil import deaccent

Subject = Literal["patient", "other"]
Status = Literal["affirmed", "negated", "conditional", "other_site", "anatomy_only", "uncertain"]

_SELF = re.compile(r"\b(?:em|toi|minh)\b")


@dataclass(frozen=True)
class _Kin:
    """Bộ nhận "người khác" dựng từ rules.yaml `experiencer` (hàm thuần trên văn bản không dấu)."""

    regex: re.Pattern[str]  # trên văn bản không dấu; nhóm rel = người thân, own = chủ sở hữu
    accented: dict[str, frozenset[str]]  # dạng không dấu → các dạng có dấu hợp lệ
    need_owner: frozenset[str]
    ambiguous: frozenset[str]


@lru_cache(maxsize=8)
def _build_kin(standalone: tuple[str, ...], need_owner: tuple[str, ...], owners: tuple[str, ...],
               modifiers: tuple[str, ...], ambiguous: tuple[str, ...]) -> _Kin:
    accented: dict[str, set[str]] = {}
    for word in (*standalone, *need_owner):
        accented.setdefault(_plain(word), set()).add(unicodedata.normalize("NFC", word).lower())
    rel = "|".join(re.escape(w) for w in sorted(accented, key=len, reverse=True))
    own = "|".join(re.escape(_plain(w)) for w in owners)
    mods = "|".join(re.escape(_plain(w)) for w in modifiers)
    regex = re.compile(rf"\b(?P<rel>{rel})(?P<own>(?:\s+(?:{mods}))*\s+(?:{own}))?\b")
    standalone_plain = {_plain(w) for w in standalone}
    return _Kin(regex=regex, accented={k: frozenset(v) for k, v in accented.items()},
                need_owner=frozenset(_plain(w) for w in need_owner) - standalone_plain,
                ambiguous=frozenset(_plain(w) for w in ambiguous))


# Cấu hình đang dùng cho lần đọc hiện tại (lexicon_findings / read_free_text đặt) – để danh sách người thân lấy từ
# ĐÚNG bộ Rules nơi gọi truyền vào (rules_version được ghi cùng kết quả), không đọc lại file mỗi lần.
_RULES: ContextVar[Any] = ContextVar("experiencer_rules", default=None)


@contextmanager
def using_rules(rules: Any) -> Iterator[None]:
    """Đặt bộ Rules cho các hàm phân tích chủ ngữ trong khối `with` (None = giữ như cũ)."""
    if rules is None:
        yield
        return
    token = _RULES.set(rules)
    try:
        yield
    finally:
        _RULES.reset(token)


def _kin() -> _Kin:
    rules = _RULES.get()
    if rules is None:
        from core.config import load_rules  # muộn: tránh vòng import

        rules = load_rules()
    cfg = rules["experiencer"]
    return _build_kin(tuple(cfg["relatives_standalone"]), tuple(cfg["relatives_need_owner"]),
                      tuple(cfg["owner_words"]), tuple(cfg.get("owner_modifiers", [])),
                      tuple(cfg.get("ambiguous_plain", [])))


def _other_matches(text: str, start: int = 0, *, anchored: bool = False) -> list[re.Match[str]]:
    """Các lần nhắc người khác trong `text` (văn bản gốc HOẶC đã bỏ dấu).

    Văn bản có dấu: người thân phải đúng dấu ("còn em" không phải "con em", "mà em" không phải "má em").
    Văn bản không dấu: bỏ các dạng trùng từ khác (`ambiguous_plain`). Người thân cần chủ sở hữu mà thiếu → bỏ.
    """
    kin = _kin()
    original = unicodedata.normalize("NFC", text or "").lower()
    plain = _plain(original)
    aligned = len(plain) == len(original)
    found = [m for m in ([kin.regex.match(plain, start)] if anchored else kin.regex.finditer(plain, start)) if m]
    out = []
    for m in found:
        rel = m.group("rel")
        if rel in kin.need_owner and not m.group("own"):
            continue
        written = original[m.start("rel"):m.end("rel")] if aligned else rel
        if written != rel:  # có dấu → phải là một dạng có dấu của người thân
            if written not in kin.accented[rel]:
                continue
        elif rel in kin.ambiguous:
            continue
        out.append(m)
    return out
_BRIDGES = {"bi", "cung", "co", "dang", "hay", "thi", "lai", "van", "hoi", "rat"}
_SUBJECT_TIME = re.compile(r"\b(?:hom nay|hom qua|hom kia|hom truoc|tuan truoc|thang truoc|luc truoc|"
                           r"sang nay|chieu nay|toi nay|toi qua|bay gio|hien tai|luc nay)\b")
_NEG_BRIDGES = {"bi", "co", "thay", "con", "he", "phai"}
_NEG_GROUPS = {("dau", "hieu"), ("trieu", "chung"), ("bieu", "hien"), ("tinh", "trang"),
               ("hien", "tuong"), ("ghi", "nhan"), ("bao", "gio")}  # "chưa bao giờ ra máu" = phủ định
_NEGATORS = {"khong", "chua", "ko", "k", "het", "chang", "cha"}
_COORD = re.compile(r"^\s*(?:(?:va|hay|hoac|cung|dong thoi|kem)\s*)+$")
_CURRENT = re.compile(r"\b(?:hom nay|sang nay|chieu nay|toi nay|bay gio|hien tai|van|den gio|toi gio|den nay)\b")
_PAST = re.compile(r"\b(?:hom qua|hom kia|hom truoc|tuan truoc|thang truoc|luc truoc|toi qua)\b")
_RESOLVED = re.compile(
    r"\b(?:da het|gio het|het roi|khoi roi|do roi|gio khoe)\b|"
    r"\b(?:sang|chieu|toi|hom) nay (?:da )?(?:het|khoi)(?=\s*(?:roi|han)?\s*$)"
)
_CONDITIONAL = re.compile(r"\b(?:neu|gia su|gia nhu|phong khi|lo ma|khi nao)\b")
_DOUBT = re.compile(r"\b(?:khong chac|khong ro|chua ro|khong biet|co le|hinh nhu|nghi la)\b")
_NON_GENITAL = re.compile(
    r"\b(?:chan rang|nuou|loi rang|mui|vet thuong|ngoai da|ngon tay|dau goi|"
    r"hau mon|truc trang|di ngoai|non ra mau|ho ra mau)\b"
)
_GENITAL = re.compile(r"\b(?:am dao|vung kin|cua minh|quan lot|bang ve sinh)\b")
# "chảy máu cam" = chảy máu mũi. Không khớp "máu cảm thấy…" (cùng dạng không dấu "mau cam thay").
_NOSEBLEED = re.compile(r"^\s*cam\b(?!\s+thay)")
# Nước ra từ chỗ khác âm đạo: "ra nước mũi", "chảy nước mắt", "nước bọt", "nước tiểu" (FLUID loại trừ nước tiểu).
_FLUID_OTHER = re.compile(r"^\s*(?:mui|mat|bot|dai|mieng|tieu|muoi)\b")
# "nằm im" mà chủ ngữ là MẸ ("em mệt nên nằm im nghỉ") – không phải thai nằm im.
_FETUS = re.compile(r"\b(?:con|be|thai|em be)\b")
# Thai phụ kể thông tin đọc / nghe được, không phải triệu chứng của mình → Vàng, gọi xác minh (không về Xanh).
_INFO = re.compile(r"\b(?:doc tren mang|tren mang|doc bao|xem tren mang|nghe noi|nghe nguoi ta|nguoi ta bao|"
                   r"tren facebook|tren tiktok|tren nhom)\b")
# Vú căng tức khi mang thai / cho bú – không phải đau ngực.
_BREAST = re.compile(r"\b(?:cang|vu|dau ti|nhu hoa|cho bu|sua)\b")


@dataclass(frozen=True)
class ClinicalFact:
    concept: str
    start: int
    end: int
    evidence: str
    present: bool
    temporality: str
    experiencer: Subject
    severity: str = "unknown"
    assertion_status: Status = "affirmed"
    anatomy: str | None = None

    def finding(self) -> dict:
        return {"concept": self.concept, "present": self.present, "temporality": self.temporality,
                "experiencer": self.experiencer, "severity": self.severity, "evidence": self.evidence,
                "assertion_status": self.assertion_status, "anatomy": self.anatomy,
                "evidence_start": self.start, "evidence_end": self.end}


def _plain(text: str) -> str:
    return deaccent(text).lower()


def subject_before(before: str) -> Subject:
    """Explicit adjacent family subject; reporting verbs do not transfer symptoms."""
    plain = _plain(before)
    matches = _other_matches(before)
    if not matches:
        return "patient"
    last = matches[-1]
    tail_text = _SUBJECT_TIME.sub(" ", plain[last.end():])
    # "con gái lớn CỦA EM bị sốt": "của em" là sở hữu, không phải chủ ngữ mới là thai phụ.
    tail_text = re.sub(r"^\s*(?:(?:lon|nho|ca|ut|be)\s+)?cua\s+(?:em|toi|minh)\b", " ", tail_text)
    tail = re.findall(r"\w+", tail_text)
    # "chồng em thấy EM khó thở" establishes a new patient subject.
    if any(t in {"em", "toi", "minh"} for t in tail):
        return "patient"
    return "other" if len(tail) <= 3 and all(t in _BRIDGES for t in tail) else "patient"


def has_explicit_subject(text: str) -> bool:
    plain = _plain(text)
    return bool(_other_matches(text) or _SELF.search(plain))


def assertion_content_start(text: str) -> int:
    """Bỏ phần chủ ngữ / trạng thái đầu một đoạn trích để ràng buộc sự kiện chưa có mã."""
    plain = _plain(text)
    position = 0
    time = _SUBJECT_TIME.match(plain)
    if time:
        position = time.end()
    whitespace = re.match(r"\s*", plain[position:])
    position += whitespace.end()
    others = _other_matches(text, position, anchored=True)
    subject = others[0] if others else _SELF.match(plain, position)
    if subject:
        position = subject.end()
    modifiers = _BRIDGES | _NEG_BRIDGES | _NEGATORS | {"da"}
    while True:
        word = re.match(r"\s*(\w+)\s*", plain[position:])
        if word is None or word.group(1) not in modifiers:
            break
        position += word.end()
    return min(position, max(0, len(text) - 1))


def resolution_asserted(text: str) -> bool:
    """A recovery phrase must describe the symptom, not running out of medicine."""
    plain = _plain(text)
    for match in _RESOLVED.finditer(plain):
        preceding = re.findall(r"\w+", plain[:match.start()])[-2:]
        if match.group().endswith("het") and not re.match(
            r"^\s*(?:(?:roi|han|a|nhe|nha)\s*)*(?:[.,;!?]|$)", plain[match.end():]
        ):
            continue
        if not any(t in {"chua", "khong", "ko"} for t in preceding):
            return True
    return False


def assertion_context(
    clause: str, start: int, end: int, *, next_clause: str = "", negation_allowed: bool = True,
    previous: ClinicalFact | None = None, previous_end: int = 0,
    inherited_subject: Subject | None = None, next_start: int | None = None,
) -> dict:
    """Bind attributes to this mention, carrying only explicit coordinated scope."""
    before, after = clause[:start], clause[end: next_start]
    scope_before = clause[previous_end:start] if previous is not None else before
    tokens = re.findall(r"\w+", _plain(before))
    idx, skipped = len(tokens) - 1, 0
    while idx >= 0 and skipped < 8:
        if tokens[idx] in _NEG_BRIDGES:
            idx -= 1
            skipped += 1
        elif idx >= 1 and tuple(tokens[idx - 1:idx + 1]) in _NEG_GROUPS:
            idx -= 2
            skipped += 2
        else:
            break
    denied = idx >= 1 and tokens[idx - 1:idx + 1] == ["phu", "nhan"]
    negated = negation_allowed and idx >= 0 and (tokens[idx] in _NEGATORS or denied)
    # Phủ định kép "không phải là không ra máu" / "không phải không có" = khẳng định.
    if negated and re.search(r"\b(?:khong|ko|chang)\s+phai\s+(?:la\s+)?$", " ".join(tokens[:idx]) + " "):
        negated = False
    # "chưa/không hết X" là X vẫn tiếp diễn, không phải phủ định X.
    not_resolved = bool(idx > 0 and tokens[idx] == "het" and tokens[idx - 1] in {"khong", "chua", "ko", "k"})
    if not_resolved:
        negated = False
    coordinated = _plain(scope_before)
    if previous is not None and previous.anatomy == "genital":
        coordinated = re.sub(r"^\s*(?:am dao|vung kin|cua minh|quan lot|bang ve sinh)\b", " ", coordinated)
    if negation_allowed and previous is not None and not previous.present and _COORD.fullmatch(coordinated):
        negated = previous.assertion_status == "negated"
    # Postposed negation requires an explicit grammatical cue. Bare "... không?"
    # is a question, not proof that the symptom is absent.
    assertion_tail = re.sub(r"^\s*(?:am dao|vung kin|cua minh|quan lot|bang ve sinh)\b", " ", _plain(after))
    if negation_allowed and re.match(r"^\s*(?:thi|cung|deu)\s+(?:khong|ko|chua)(?:\s+co)?\s*$", assertion_tail):
        negated = True
    if negation_allowed and re.match(r"^\s*[:=\-]\s*(?:khong|ko|chua)(?:\s+co)?\s*$", assertion_tail):
        negated = True

    subject = subject_before(before)
    # Chủ ngữ đứng sau ("ra máu là của chị em"): dò trên câu GỐC (có dấu) để nhận đúng người thân viết có dấu;
    # bản bỏ dấu cùng độ dài nên vị trí khớp nhau.
    postposed_subject = re.match(
        r"^\s*(?:(?:am dao|vung kin|cua minh|quan lot|bang ve sinh)\b\s*)?(?:(?:la|cua)\s+)+", _plain(after))
    if (postposed_subject and _other_matches(after, postposed_subject.end(), anchored=True)
            and not _SELF.search(_plain(before))):
        subject = "other"
    if not has_explicit_subject(scope_before):
        if previous is not None and _COORD.fullmatch(coordinated):
            subject = previous.experiencer
        elif previous is None and inherited_subject is not None and (
                not scope_before.strip() or _COORD.fullmatch(_plain(scope_before))):
            subject = inherited_subject

    status: Status = "negated" if negated else "affirmed"
    conditionals = list(_CONDITIONAL.finditer(_plain(before)))
    in_condition = bool(conditionals and not re.search(r"\bthi\b", _plain(before)[conditionals[-1].end():]))
    if _CONDITIONAL.search(_plain(scope_before)) or in_condition:
        status = "conditional"
        # "Nếu đứng lâu LÀ EM bị ra máu", "khi nào đi tiểu EM CŨNG thấy ra máu": khách kể việc lặp lại đã xảy ra,
        # không phải câu giả định → chưa chắc, cần gọi xác minh (không cho Xanh). Câu hỏi giả định thật
        # ("nếu ra máu thì em phải làm sao") vẫn là điều kiện.
        cond_tail = _plain(before)[conditionals[-1].end():] if conditionals else _plain(scope_before)
        habitual = re.search(r"\b(?:la|thi)\s+(?:em|toi|minh)\b|\b(?:em|toi|minh)\s+(?:cung|lai|hay|deu|toan|luc nao)\b",
                             cond_tail)
        asking = re.search(r"\b(?:lam sao|lam gi|the nao|co sao|co nguy hiem|co sao khong|co can|nen lam|phai di)\b",
                           _plain(clause))
        if habitual and not asking:
            status = "uncertain"
    if (status == "affirmed" and previous is not None and previous.assertion_status == "uncertain"
            and _COORD.fullmatch(coordinated)):
        # "đọc trên mạng về ra máu VÀ khó thở": cả hai vẫn là thông tin đọc được.
        # Chủ ngữ / diễn biến mới ("và em đang khó thở") chấm dứt phạm vi này.
        status = "uncertain"
    if status == "affirmed":
        doubts = list(_DOUBT.finditer(_plain(scope_before)))
        if doubts:
            tail = re.findall(r"\w+", _plain(scope_before)[doubts[-1].end():])
            if len(tail) <= 4 and all(word in {"co", "bi", "thay", "la", "phai", "em", "toi", "minh"} for word in tail):
                status = "uncertain"
        question = bool(re.search(r"\b(?:co nguy hiem|co sao|la gi|nghia la gi|co can di kham)\b", _plain(after)))
        generic_question = (not _SELF.search(_plain(before))
                            or re.search(r"\b(?:cho em hoi|em muon hoi|xin hoi)\b", _plain(before)))
        leads = list(re.finditer(r"\b(?:cho em hoi|em muon hoi|xin hoi)\b", _plain(before)))
        if leads and _SELF.search(_plain(before)[leads[-1].end():]):
            generic_question = False
        if question and generic_question and not re.search(r"\b(?:dang|vua|van)\b", _plain(before)):
            status = "uncertain"

    # A time in the past does not establish that an event has resolved.
    resolution_tail = after
    if next_clause and not has_explicit_subject(next_clause):
        resolution_tail += " " + next_clause
    pre_resolved = bool(idx >= 0 and tokens[idx] == "het" and not not_resolved)
    explicit_history = pre_resolved and (idx > 0 and tokens[idx - 1] == "da" or bool(_PAST.search(_plain(before))))
    if explicit_history and status != "conditional":
        # "đã hết X": xác nhận một sự kiện trước đó; "không X" chỉ phủ định.
        negated, status = False, "affirmed"
    resolved = resolution_asserted(resolution_tail) or pre_resolved
    temporality = "past_resolved" if resolved else (
        "current" if _CURRENT.search(_plain(clause)) or not _PAST.search(_plain(clause)) else "unknown"
    )
    return {"present": not negated and status != "conditional", "temporality": temporality,
            "experiencer": subject, "assertion_status": status}


def make_fact(
    concept: str, clause: str, start: int, end: int, *, offset: int = 0,
    next_clause: str = "", negation_allowed: bool = True, severe_words: list[str] | None = None,
    previous: ClinicalFact | None = None, previous_end: int = 0,
    inherited_subject: Subject | None = None, next_start: int | None = None,
) -> ClinicalFact:
    attrs = assertion_context(clause, start, end, next_clause=next_clause,
                              negation_allowed=negation_allowed, previous=previous,
                              previous_end=previous_end, inherited_subject=inherited_subject,
                              next_start=next_start)
    anatomy = None
    local_after = _plain(clause[end:next_start])
    local_before = _plain(clause[previous_end:start] if previous else clause[:start])
    if concept == "BLEEDING":
        # Prefer a site attached after this mention over a site on a previous event.
        if _NON_GENITAL.search(local_after) and not _GENITAL.search(local_after):
            anatomy, attrs["present"], attrs["assertion_status"] = "non_genital", False, "other_site"
        elif _GENITAL.search(local_after):
            anatomy = "genital"
        elif _NON_GENITAL.search(" ".join(local_before.split()[-4:])):
            anatomy, attrs["present"], attrs["assertion_status"] = "non_genital", False, "other_site"

    if concept == "BLEEDING" and _NOSEBLEED.match(local_after):
        anatomy, attrs["present"], attrs["assertion_status"] = "nose", False, "other_site"

    if concept == "FLUID" and _FLUID_OTHER.match(local_after) and not _GENITAL.search(local_after):
        anatomy, attrs["present"], attrs["assertion_status"] = "non_genital", False, "other_site"

    if concept == "REDUCED_FM" and re.fullmatch(r"nam\s+im", _plain(clause[start:end])):
        near = " ".join(local_before.split()[-6:])
        if not _FETUS.search(near) and _SELF.search(near):
            anatomy, attrs["present"], attrs["assertion_status"] = "mother", False, "other_site"

    if concept == "CHEST_PAIN":
        window = " ".join(local_before.split()[-3:]) + " " + _plain(clause[start:end]) + " " + " ".join(local_after.split()[:4])
        if _BREAST.search(window):
            anatomy, attrs["present"], attrs["assertion_status"] = "breast", False, "other_site"

    if attrs["present"] and _INFO.search(local_before):
        attrs["assertion_status"] = "uncertain"

    if concept == "CALF":
        attached = " ".join(local_before.split()[-3:]) + " " + _plain(clause[start:end]) + " " + " ".join(local_after.split()[:4])
        if not re.search(r"\b(?:dau|sung|do|nong|nhuc|cang)\b", attached):
            attrs["present"], attrs["assertion_status"] = False, "anatomy_only"
            anatomy = "calf"
        elif negation_allowed:
            negated_property = r"\b(?:khong|chua|ko|het)(?:\s+(?:bi|con))?\s+(?:dau|sung|do|nong|nhuc)\b"
            remainder = re.sub(negated_property, " ", attached)
            remaining_property = re.search(r"\b(?:dau|sung|do|nong|nhuc)\b", remainder)
            continued_property = re.match(r"^\s*(?:ma\s+)?(?:sung|do|nong|nhuc)\b", _plain(next_clause))
            if re.search(negated_property, attached) and not remaining_property and not continued_property:
                attrs["present"], attrs["assertion_status"] = False, "negated"

    severity = "unknown"
    if severe_words:
        scoped_after = re.split(r"\b(?:va|roi|nhung|vi|nen)\s+(?:(?:em|toi|minh)\s+)?(?:uong|an|di|ngu|doc|xem)\b", local_after)[0]
        # 6 từ trước: "không đi lại được vì đau bụng" (từ chỉ nặng đứng trước triệu chứng).
        severity_scope = " ".join(local_before.split()[-6:]) + " " + _plain(clause[start:end]) + " " + scoped_after
        if next_clause and not has_explicit_subject(next_clause):  # "đau đầu, uống thuốc không đỡ"
            severity_scope += " " + _plain(next_clause)
        severity_scope = re.sub(r"\b(?:da\s+)?(?:do|giam)\s+(?:nhieu|han|dang ke)\b", " ", severity_scope)
        severity = "severe" if any(re.search(rf"(?<!\w){re.escape(_plain(w))}(?!\w)", severity_scope)
                                    for w in severe_words) else "mild"
    elif concept == "HEADACHE":
        severity_scope = " ".join(local_before.split()[-2:]) + " " + local_after
        if re.search(r"\b(?:hoi|nhe|thoang qua)\b", severity_scope):
            severity = "mild"
    evidence_start = max(0, start - 35)
    evidence_end = min(len(clause), max(end, evidence_start + 120))
    return ClinicalFact(concept, offset + start, offset + end,
                        clause[evidence_start:evidence_end].strip()[:120], severity=severity,
                        anatomy=anatomy, **attrs)


@dataclass(frozen=True)
class MeasurementFact:
    start: int
    end: int
    systolic: float
    diastolic: float
    unit: str
    experiencer: Subject
    temporality: str
    assertion_status: str

    @property
    def usable(self) -> bool:
        return self.experiencer == "patient" and self.temporality == "current" and self.assertion_status == "affirmed"


# Nhãn huyết áp. "ha" (không dấu) cũng là "hạ" → loại "hạ đường huyết / hạ sốt / hạ nhiệt / hạ áp".
_BP_LABEL = re.compile(r"\b(?:huyet\s+ap|blood\s+pressure|bp|ha(?!\s+(?:duong|sot|nhiet|ap|huyet|than)))\b",
                       re.IGNORECASE)
_BP_PAIR = re.compile(r"(?<![/\d.,])(?P<sys>\d{2,4}(?:[.,]\d+)?)\s*/\s*(?P<dia>\d{2,3}(?:[.,]\d+)?)"
                      r"\s*(?P<unit>mm\s*hg|kpa|psi|bar|pa)?(?![/\d])", re.IGNORECASE)
def _is_date_pair(plain: str, m: re.Match[str]) -> bool:
    """Ngày tháng ("14/11", "14/11/2026"), không phải số đo. Không dựa vào từ đứng trước ("tối" ≈ "tới" khi bỏ dấu)."""
    if re.match(r"\s*/\s*\d", plain[m.end():]):  # 14/11/2026
        return True
    a = float(m.group("sys").replace(",", "."))
    b = float(m.group("dia").replace(",", "."))
    return a <= 31 and b <= 12 and not m.group("unit")


def blood_pressure_facts(text: str) -> list[MeasurementFact]:
    """Only clearly labelled pairs; unknown units and old/other-person readings are not current vitals.

    Bỏ qua ngày tháng sau nhãn ("huyết áp hôm 14/11 là 160/110" → 160/110). Văn bản chuẩn hóa NFC trước khi so,
    để vị trí trên bản bỏ dấu khớp vị trí trên văn bản gốc (chữ tổ hợp NFD từ iPhone / Mac).
    """
    text = unicodedata.normalize("NFC", text or "")
    plain = _plain(text)
    if len(plain) != len(text):  # an toàn: vị trí phải khớp 1-1
        text = plain
    out = []
    used_to = -1
    for label in _BP_LABEL.finditer(plain):
        if label.start() < used_to:
            continue
        # Hết mệnh đề = dấu câu theo sau là khoảng trắng / hết chuỗi (không cắt dấu thập phân "21.3", "14.11").
        end_m = re.compile(r"[.;!?](?=\s|$)|\n").search(plain, label.end())
        stop = min(end_m.start() if end_m else len(plain), label.end() + 60)  # 60 ký tự: giới hạn chỗ BẮT ĐẦU số đo
        pairs = []
        for m in _BP_PAIR.finditer(plain, label.end()):  # không dùng endpos (cắt cụt số); dừng khi qua giới hạn
            if m.start() >= stop:
                break
            pairs.append(m)
        match = next((m for m in pairs if not _is_date_pair(plain, m)), None)
        date_only = match is None and bool(pairs)
        if date_only:  # "HA 16/11": chỉ có cặp giống ngày tháng → không bỏ qua, chuyển người xem
            match = pairs[0]
        if match is None:
            continue
        used_to = match.end()
        # Clause boundaries preserve the subject appearing before the measurement label.
        boundary = max((plain.rfind(c, 0, label.start()) for c in ".;!?\n"), default=-1) + 1
        clause = text[boundary:match.end()]
        attrs = assertion_context(clause, match.start("sys") - boundary, match.end() - boundary)
        subject_prefix = re.sub(r"\b(?:huyet ap|ha|blood pressure|bp|do|la|cua|duoc|vua)\b", " ",
                                _plain(clause[:match.start("sys") - boundary]))
        attrs["experiencer"] = subject_before(subject_prefix)
        if re.search(r"\b(?:muc tieu|nguong|vi du|tham chieu)\b", _plain(clause)):
            attrs["assertion_status"] = "conditional"
        unit = re.sub(r"\s+", "", match.group("unit") or "mmhg")
        # The product's labelled BP pair convention is mmHg. Do not silently convert
        # an explicitly different unit or combine dates/unknown numeric expressions.
        if unit != "mmhg":
            attrs["assertion_status"] = "uncertain"
        if re.search(r"\b(?:khong toi|khong vuot|thap hon|duoi)\s*$", subject_prefix):
            attrs["assertion_status"] = "uncertain"
        if date_only:
            attrs["assertion_status"] = "uncertain"
        out.append(MeasurementFact(match.start(), match.end(),
                                   float(match.group("sys").replace(",", ".")),
                                   float(match.group("dia").replace(",", ".")), unit,
                                   attrs["experiencer"], attrs["temporality"], attrs["assertion_status"]))
    return out
