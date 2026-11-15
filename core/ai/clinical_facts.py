"""Source-bound clinical assertions shared by symptom and measurement readers.

This is a conservative Vietnamese assertion parser, not a diagnosis model. A fact
keeps its source span and the scope of subject, negation, time and modality so a
keyword alone cannot establish that a patient currently has a symptom.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from core.textutil import deaccent

Subject = Literal["patient", "other"]
Status = Literal["affirmed", "negated", "conditional", "other_site", "anatomy_only", "uncertain"]

_OTHER = re.compile(
    r"\b(?:me chong|me em|me toi|me minh|chi gai|em gai|con lon|be lon|con gai|con trai|"
    r"ong noi|ong ngoai|ba noi|ba ngoai|chi dau|em dau|anh trai|chi ho|em ho|co em|"
    r"ban em|hang xom|chong|bo|me)(?:\s+(?:em|toi|minh))?\b"
)
_SELF = re.compile(r"\b(?:em|toi|minh)\b")
_BRIDGES = {"bi", "cung", "co", "dang", "hay", "thi", "lai", "van", "hoi", "rat"}
_NEG_BRIDGES = {"bi", "co", "thay", "con", "he", "phai"}
_NEGATORS = {"khong", "chua", "ko", "k", "het", "chang", "cha"}
_COORD = re.compile(r"^\s*(?:(?:va|hay|hoac|cung|dong thoi|kem)\s*)+$")
_CURRENT = re.compile(r"\b(?:hom nay|sang nay|chieu nay|toi nay|bay gio|hien tai|van|den gio|toi gio|den nay)\b")
_PAST = re.compile(r"\b(?:hom qua|hom kia|hom truoc|tuan truoc|thang truoc|luc truoc|toi qua)\b")
_RESOLVED = re.compile(
    r"\b(?:da het|gio het|het roi|khoi roi|do roi|gio khoe)\b|"
    r"\b(?:sang|chieu|toi|hom) nay (?:da )?(?:het|khoi)(?=\s*(?:roi|han)?\s*$)"
)
_CONDITIONAL = re.compile(r"\b(?:neu|gia su|gia nhu|phong khi|lo ma|khi nao)\b")
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
    matches = list(_OTHER.finditer(plain))
    if not matches:
        return "patient"
    last = matches[-1]
    tail = re.findall(r"\w+", plain[last.end():])
    # "chồng em thấy EM khó thở" establishes a new patient subject.
    if any(t in {"em", "toi", "minh"} for t in tail):
        return "patient"
    return "other" if len(tail) <= 3 and all(t in _BRIDGES for t in tail) else "patient"


def has_explicit_subject(text: str) -> bool:
    plain = _plain(text)
    return bool(_OTHER.search(plain) or _SELF.search(plain))


def _resolved(text: str) -> bool:
    plain = _plain(text)
    for match in _RESOLVED.finditer(plain):
        preceding = re.findall(r"\w+", plain[:match.start()])[-2:]
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
    while idx >= 0 and tokens[idx] in _NEG_BRIDGES and skipped < 2:
        idx -= 1
        skipped += 1
    negated = negation_allowed and idx >= 0 and tokens[idx] in _NEGATORS
    if negation_allowed and previous is not None and not previous.present and _COORD.fullmatch(_plain(scope_before)):
        negated = previous.assertion_status == "negated"
    # Postposed negation requires an explicit grammatical cue. Bare "... không?"
    # is a question, not proof that the symptom is absent.
    if negation_allowed and re.match(r"^\s*(?:thi|cung|deu)\s+(?:khong|ko|chua)(?:\s+co)?\s*$", _plain(after)):
        negated = True

    subject = subject_before(before)
    if not has_explicit_subject(scope_before):
        if previous is not None and _COORD.fullmatch(_plain(scope_before)):
            subject = previous.experiencer
        elif previous is None and inherited_subject is not None and (
                not scope_before.strip() or _COORD.fullmatch(_plain(scope_before))):
            subject = inherited_subject

    status: Status = "negated" if negated else "affirmed"
    if _CONDITIONAL.search(_plain(scope_before)):
        status = "conditional"

    # A time in the past does not establish that an event has resolved.
    resolution_tail = after
    if next_clause and not has_explicit_subject(next_clause):
        resolution_tail += " " + next_clause
    resolved = _resolved(resolution_tail)
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
        scoped_after = re.split(r"\b(?:va|roi|nhung|vi|nen)\s+(?:uong|an|di|ngu|doc|xem)\b", local_after)[0]
        severity_scope = " ".join(local_before.split()[-4:]) + " " + _plain(clause[start:end]) + " " + scoped_after
        if next_clause and not has_explicit_subject(next_clause):  # "đau đầu, uống thuốc không đỡ"
            severity_scope += " " + _plain(next_clause)
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


_BP = re.compile(
    r"\b(?:huyet\s+ap|ha|blood\s+pressure|bp)\b[^\d\n.;!?]{0,45}"
    r"(?P<sys>\d{2,4}(?:[.,]\d+)?)\s*/\s*(?P<dia>\d{2,3}(?:[.,]\d+)?)"
    r"\s*(?P<unit>mm\s*hg|kpa|psi|bar|pa)?", re.IGNORECASE,
)


def blood_pressure_facts(text: str) -> list[MeasurementFact]:
    """Only clearly labelled pairs; unknown units and old/other-person readings are not current vitals."""
    plain = _plain(text)
    out = []
    for match in _BP.finditer(plain):
        # Clause boundaries preserve the subject appearing before the measurement label.
        boundary = max((plain.rfind(c, 0, match.start()) for c in ".;!?\n"), default=-1) + 1
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
        out.append(MeasurementFact(match.start(), match.end(),
                                   float(match.group("sys").replace(",", ".")),
                                   float(match.group("dia").replace(",", ".")), unit,
                                   attrs["experiencer"], attrs["temporality"], attrs["assertion_status"]))
    return out
