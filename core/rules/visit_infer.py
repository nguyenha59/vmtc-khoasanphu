"""Suy ra loại khám + dịch vụ phải làm từ ghi chú lịch hẹn và tuần thai (file OH không có cột Dịch vụ).

Kiến thức nằm trong config/visit_services.yaml (có nguồn từng dòng). Hàm thuần: ghi chú (+ kết quả AI) → ServicePlan.
- Quy tắc: dịch vụ ghi rõ bằng từ khóa trong ghi chú → AI không được bỏ.
- AI: chọn loại khám + dịch vụ trong danh mục; chỉ được THÊM dịch vụ (giờ đến chỉ sớm hơn, không muộn hơn).
- Tuần thai (khám thai định kỳ): thêm dịch vụ theo mốc, kèm nguồn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.textutil import deaccent, nfc

ANC_TYPES = ("ANC", "ANC_PACKAGE")


@dataclass(frozen=True)
class PlannedService:
    code: str
    name: str
    why: str  # note | visit_type | ga | ai | service_column
    source: str  # mã nguồn trong visit_services.sources
    confirm: bool = (
        False  # suy ra (không ghi trong ghi chú) mà cần xác nhận trước khi dặn khách (vd nhịn ăn)
    )


@dataclass
class ServicePlan:
    visit_type: str
    visit_name: str
    services: list[PlannedService] = field(default_factory=list)
    fasting: bool = False
    ga_weeks: int | None = None
    ga_days: int | None = None
    visit_from: str = "rule"  # rule | ai | default
    knowledge_version: str = ""
    est_minutes: int | None = None  # số phút AI ước lượng theo nguồn (None = dùng số dự phòng)
    est_basis: list[str] = field(default_factory=list)
    floor_minutes: int = 0  # mức sàn có nguồn: tiếp đón + tổng thời gian tối thiểu các dịch vụ làm trước
    floor_sources: list[str] = field(default_factory=list)
    guessed: list[str] = field(default_factory=list)  # viết tắt 'Đang đoán' có trong ghi chú

    @property
    def codes(self) -> list[str]:
        return [s.code for s in self.services]

    def has(self, code: str) -> bool:
        return code in self.codes

    @property
    def to_confirm(self) -> list[PlannedService]:
        return [s for s in self.services if s.confirm]


def _plain(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9βα+]+", " ", deaccent(nfc(text or "")).lower()) + " "


def _has(plain: str, keyword: str) -> bool:
    kw = _plain(keyword).strip()
    return bool(kw) and f" {kw} " in plain


def terms_for(code: str, catalog: dict[str, Any]) -> list[str]:
    """Từ khóa của mã (dịch vụ / loại khám) + các viết tắt ánh xạ tới mã đó (sheet Viết tắt)."""
    own = (catalog["services"].get(code) or catalog["visit_types"].get(code) or {}).get("keywords", [])
    abbr = [a["term"] for a in catalog.get("abbreviations", []) if code in a.get("maps_to", [])]
    return [*own, *abbr]


def _accented(text: str) -> str:
    return " " + re.sub(r"[^\w+]+", " ", nfc(text or "").lower()) + " "


def done_services(text: str, catalog: dict[str, Any]) -> list[str]:
    """Dịch vụ ghi chú nói đã làm ("đã làm NPDN", "có kq double test") – tối đa 3 từ sau dấu hiệu "đã làm".
    Ghi chú có dấu → so đúng dấu ("chuyển dạ, SA" không phải "đã SA"); ghi chú không dấu → so không dấu."""
    from core.textutil import has_diacritics

    accented = has_diacritics(text)
    hay = _accented(text) if accented else _plain(text)
    out: list[str] = []
    for marker in catalog.get("done_markers", []):
        mk = (_accented(marker) if accented else _plain(marker)).strip()
        for m in re.finditer(rf" {re.escape(mk)} ((?:\S+ ){{1,2}})", hay):
            tail = " " + _plain(m.group(1))
            out.extend(c for c in catalog["services"]
                       if c not in out and any(_has(tail, k) for k in terms_for(c, catalog)))
    return out


def keyword_services(text: str, catalog: dict[str, Any]) -> list[str]:
    """Mã dịch vụ có từ khóa / viết tắt trong văn bản (thứ tự theo danh mục), trừ dịch vụ ghi là đã làm."""
    plain = _plain(text)
    done = set(done_services(text, catalog))
    return [code for code in catalog["services"]
            if code not in done and any(_has(plain, k) for k in terms_for(code, catalog))]


def rule_visit_type(text: str, catalog: dict[str, Any]) -> str | None:
    """Loại khám đầu tiên (theo thứ tự ưu tiên trong danh mục) có từ khóa / viết tắt trong ghi chú."""
    plain = _plain(text)
    for code in catalog["visit_types"]:
        if any(_has(plain, k) for k in terms_for(code, catalog)):
            return code
    return None


def guessed_terms(text: str, catalog: dict[str, Any]) -> list[str]:
    """Viết tắt 'Đang đoán' (chưa được bác sĩ / điều dưỡng xác nhận nghĩa) xuất hiện trong ghi chú."""
    plain = _plain(text)
    return [a["term"] for a in catalog.get("abbreviations", [])
            if a.get("status") == "guess" and _has(plain, a["term"])]


def milestone_services(
    ga_weeks: int | None, ga_days: int | None, catalog: dict[str, Any], present: list[str]
) -> list[tuple[str, str, bool]]:
    """Dịch vụ theo mốc tuần thai → [(mã, nguồn, cần xác nhận)]."""
    if ga_weeks is None:
        return []
    out: list[tuple[str, str, bool]] = []
    g = ga_weeks * 7 + (ga_days or 0)
    for m in catalog.get("ga_milestones", []):
        lo = int(m["from_week"]) * 7
        hi = int(m["to_week"]) * 7 + int(m.get("to_day", 6))
        if not lo <= g <= hi:
            continue
        if any(c in present for c in m.get("only_if_missing", [])):
            continue
        out.extend((c, str(m["source"]), bool(m.get("confirm"))) for c in m["services"])
    return out


def infer_services(
    note: str,
    catalog: dict[str, Any],
    *,
    ga: tuple[int, int | None] | None = None,
    ai_visit_type: str | None = None,
    ai_services: list[str] | None = None,
    service_text: str = "",
) -> ServicePlan:
    """Ghép quy tắc + AI + mốc tuần thai thành kế hoạch dịch vụ. AI chỉ thêm, không bớt."""
    services_cfg: dict[str, Any] = catalog["services"]
    types_cfg: dict[str, Any] = catalog["visit_types"]
    added: dict[str, PlannedService] = {}

    def add(code: str, why: str, source: str | None = None, confirm: bool = False) -> None:
        if code in services_cfg and code not in added:
            svc = services_cfg[code]
            added[code] = PlannedService(
                code, str(svc["name"]), why, str(source or svc.get("source", "")), confirm
            )

    rule_type = rule_visit_type(note, catalog)
    visit_from = "rule"
    visit_type = rule_type
    if ai_visit_type in types_cfg and ai_visit_type not in ("OTHER",):
        # AI chọn loại khám; quy tắc chỉ thắng khi AI trả loại chung chung còn quy tắc thấy loại cụ thể.
        if rule_type is None or rule_type in ("ANC", "GYN") or ai_visit_type == rule_type:
            visit_type, visit_from = ai_visit_type, "ai"
    if visit_type is None:
        visit_type, visit_from = ("ANC" if ga else "OTHER"), "default"

    for code in keyword_services(service_text, catalog):
        add(code, "service_column")
    for code in keyword_services(note, catalog):
        add(code, "note")
    # Ghi chú đã ghi rõ dịch vụ (ngoài "khám") → CHỈ làm đúng các dịch vụ đó (+ khám bác sĩ); không tự thêm theo loại
    # khám / mốc tuần thai / AI (người dùng chốt 17/11/2026, visit_flow.note_services_only).
    written = [c for c in added if c != "KHAM"]
    if written and (catalog.get("visit_flow") or {}).get("note_services_only", True):
        add("KHAM", "visit_type", types_cfg[visit_type].get("source"))
        fasting = any(bool(services_cfg[c].get("fasting")) for c in added)
        return ServicePlan(guessed=guessed_terms(note, catalog), visit_type=visit_type,
                           visit_name=str(types_cfg[visit_type]["name"]), services=list(added.values()),
                           fasting=fasting, ga_weeks=ga[0] if ga else None, ga_days=ga[1] if ga else None,
                           visit_from=visit_from, knowledge_version=str(catalog.get("version", "")))
    for code in types_cfg[visit_type].get("services", []):
        add(code, "visit_type", types_cfg[visit_type].get("source"))
    for code in ai_services or []:
        add(code, "ai")
    if visit_type in ANC_TYPES and ga:
        done = done_services(note, catalog)
        for code, src, confirm in milestone_services(ga[0], ga[1], catalog, [*added, *done]):
            if code not in done:
                add(code, "ga", src, confirm)
    # "Siêu âm đơn thuần": ghi chú chỉ nói SA → bỏ KHAM mặc định không cần (giữ nếu ghi chú có "khám").
    fasting = any(bool(services_cfg[c].get("fasting")) for c in added)
    return ServicePlan(guessed=guessed_terms(note, catalog),
        visit_type=visit_type,
        visit_name=str(types_cfg[visit_type]["name"]),
        services=list(added.values()),
        fasting=fasting,
        ga_weeks=ga[0] if ga else None,
        ga_days=ga[1] if ga else None,
        visit_from=visit_from,
        knowledge_version=str(catalog.get("version", "")),
    )


def plan_minutes(
    plan: ServicePlan, catalog: dict[str, Any], default_max: int
) -> tuple[int | None, int, str]:
    """(số phút đến sớm = max các dịch vụ, trần sớm = max các trần, dịch vụ quyết định số phút)."""
    best: int | None = None
    label = ""
    cap = default_max
    for s in plan.services:
        svc = catalog["services"][s.code]
        m = int(svc["minutes_early"])
        if best is None or m > best:
            best, label = m, s.name
        cap = max(cap, int(svc.get("max_minutes_early", default_max)))
    return best, cap, label


def source_titles(plan: ServicePlan, catalog: dict[str, Any]) -> list[str]:
    """Tên nguồn (không trùng) cho cột 'Căn cứ'."""
    srcs = catalog.get("sources", {})
    seen: list[str] = []
    for s in plan.services:
        t = str((srcs.get(s.source) or {}).get("title", s.source))
        if t and t not in seen:
            seen.append(t)
    return seen


def sourced_floor(plan: ServicePlan, catalog: dict[str, Any]) -> tuple[int, list[str]]:
    """Số phút tối thiểu có nguồn (NB_QUY_TRINH: giờ hẹn = giờ gặp bác sĩ, dịch vụ làm trước):
    tiếp đón + tổng min_minutes của các dịch vụ trong lịch. Trả (phút, mã nguồn)."""
    flow = catalog.get("visit_flow") or {}
    total = int(flow.get("reception_min", 0))
    srcs = [str(flow["reception_source"])] if flow.get("reception_source") else []
    if not flow.get("services_before_doctor", False):
        return total, srcs
    for s in plan.services:
        svc = catalog["services"][s.code]
        if svc.get("min_minutes"):
            total += int(svc["min_minutes"])
            if svc.get("min_source") and svc["min_source"] not in srcs:
                srcs.append(str(svc["min_source"]))
    return total, srcs
