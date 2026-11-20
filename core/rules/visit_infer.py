"""Suy ra loại khám + dịch vụ phải làm từ ghi chú lịch hẹn và tuần thai (file OH không có cột Dịch vụ).

Kiến thức nằm trong config/visit_services.yaml (có nguồn từng dòng). Hàm thuần: ghi chú (+ kết quả AI) → ServicePlan.
Người dùng chốt 17/11/2026:
- AI chỉ đọc ghi chú để xác định LOẠI KHÁM + DỊCH VỤ; CODE tự tính giờ đến từ thời gian tối thiểu có nguồn.
- Ba nhóm dịch vụ:
  * ghi trong ghi chú (explicit) → có thì CHỈ dùng nhóm này (+ khám) để tính giờ / nhịn ăn;
  * suy ra (theo loại khám / mốc tuần thai) → chỉ khi ghi chú không ghi dịch vụ; tính giờ nhưng cần xác nhận;
  * có điều kiện (OGTT, Triple, Double, NIPT, GBS, βhCG – `conditional`) → KHÔNG tính giờ / nhịn ăn, chỉ hiện
    "Có thể cần (nếu chưa làm)" (trừ khi ghi chú ghi rõ → thành explicit).
- Nghi chuyển dạ (PRETERM_CHECK): luôn có CTG.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.textutil import deaccent, nfc


def anc_types(catalog: dict[str, Any]) -> tuple[str, ...]:
    return tuple((catalog.get("visit_flow") or {}).get("anc_visit_types", ["ANC", "ANC_PACKAGE"]))


@dataclass(frozen=True)
class PlannedService:
    code: str
    name: str
    why: str  # note | visit_type | ga | ai | service_column | safety
    source: str  # mã nguồn trong visit_services.sources
    confirm: bool = False  # (giữ tương thích) suy ra mà cần xác nhận
    conditional: bool = False  # "nếu chưa làm" – không tính giờ / nhịn ăn


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
    floor_minutes: int = 0  # tiếp đón + tổng thời gian tối thiểu có nguồn của các dịch vụ làm trước
    floor_sources: list[str] = field(default_factory=list)
    floor_parts: list[tuple[str, int]] = field(default_factory=list)  # (tên, phút) để giải thích phép cộng
    guessed: list[str] = field(default_factory=list)  # viết tắt 'Đang đoán' có trong ghi chú
    explicit: bool = False  # ghi chú có ghi dịch vụ
    urgent: bool = False  # nghi chuyển dạ / dấu hiệu cần cấp cứu sản
    urgent_advice: str = ""  # câu cảnh báo theo loại (thai ngoài TC, dọa sảy, rỉ ối, chuyển dạ…)
    fasting_check: bool = False  # chỉ chuẩn bị hồ sơ thủ thuật – chưa chắc làm hôm nay → hỏi lại nhịn ăn
    over_cap: bool = False  # tổng thời gian tối thiểu vượt giới hạn an toàn của các dịch vụ → Cần xác nhận

    @property
    def codes(self) -> list[str]:
        return [s.code for s in self.services]

    def has(self, code: str) -> bool:
        return code in self.codes

    @property
    def timed(self) -> list[PlannedService]:
        """Dịch vụ tính vào giờ đến / nhịn ăn (bỏ nhóm 'có điều kiện')."""
        return [s for s in self.services if not s.conditional]

    @property
    def maybe(self) -> list[PlannedService]:
        """Dịch vụ 'có thể cần (nếu chưa làm)'."""
        return [s for s in self.services if s.conditional]

    @property
    def inferred_timed(self) -> list[PlannedService]:
        """Dịch vụ suy ra (không ghi trong ghi chú) mà có tính thời gian."""
        return [s for s in self.timed if s.why in ("visit_type", "ga", "ai") and s.code != "KHAM"]

    @property
    def to_confirm(self) -> list[PlannedService]:
        return [s for s in self.services if s.confirm and not s.conditional]


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
    """Dịch vụ ghi chú nói đã làm ("đã làm NPDN", "có kq double test") – tối đa 2 từ sau dấu hiệu "đã làm".
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
    # dạng đặt sau: "NPDN rồi", "triple đã làm", "GBS có kq"
    for marker in catalog.get("done_after_markers", []):
        mk = (_accented(marker) if accented else _plain(marker)).strip()
        for m in re.finditer(rf"((?:\S+ ){{1,2}}){re.escape(mk)}(?= *(?:[,;.+/)\n]|$))", hay.rstrip()):
            head = " " + _plain(m.group(1))
            out.extend(c for c in catalog["services"]
                       if c not in out and any(_has(head, k) for k in terms_for(c, catalog)))
    return out


def _generic_only(code: str, note: str, catalog: dict[str, Any]) -> bool:
    """Dịch vụ chỉ khớp qua từ chung ("SA", "XN") – không có từ khóa riêng (vd "2D", "xét nghiệm máu")."""
    generic = ((catalog.get("visit_flow") or {}).get("generic_service_keywords") or {}).get(code, [])
    plain = _plain(note)
    specific = [k for k in terms_for(code, catalog) if k not in generic]
    return bool(generic) and not any(_has(plain, k) for k in specific)


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


def is_urgent(note: str, visit_type: str, ga: tuple[int, int | None] | None, catalog: dict[str, Any]) -> bool:
    """Nghi chuyển dạ, hoặc thai phụ có dấu hiệu (đau bụng cơn, ra máu, ra nước…) → cảnh báo cấp cứu sản."""
    if visit_type in (catalog.get("visit_flow") or {}).get("urgent_visit_types", []):
        return True
    kws = (catalog.get("visit_flow") or {}).get("urgent_keywords", [])
    plain = _plain(note)
    flow = catalog.get("visit_flow") or {}
    if flow.get("urgent_requires_ga", True) and not ga:
        return False
    return any(_has(plain, k) for k in kws)


def urgent_rule(
    note: str, visit_type: str, ga: tuple[int, int | None] | None, catalog: dict[str, Any]
) -> dict[str, Any] | None:
    """Luật cảnh báo an toàn đầu tiên khớp (visit_flow.urgent_rules, theo thứ tự) – None nếu không khớp."""
    plain = _plain(note)
    for r in (catalog.get("visit_flow") or {}).get("urgent_rules", []):
        hit = visit_type in r.get("visit_types", []) or any(_has(plain, k) for k in r.get("keywords", []))
        if not hit:
            continue
        if r.get("requires_ga") and not ga:
            continue
        w = ga[0] if ga else None
        if w is not None and r.get("max_ga_weeks") is not None and w > int(r["max_ga_weeks"]):
            continue
        if w is not None and r.get("min_ga_weeks") is not None and w < int(r["min_ga_weeks"]):
            continue
        return dict(r)
    return None


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
    """Ghép quy tắc + AI + mốc tuần thai thành kế hoạch dịch vụ (3 nhóm – xem đầu file)."""
    services_cfg: dict[str, Any] = catalog["services"]
    types_cfg: dict[str, Any] = catalog["visit_types"]
    added: dict[str, PlannedService] = {}

    def add(code: str, why: str, source: str | None = None, confirm: bool = False) -> None:
        if code in services_cfg and code not in added:
            svc = services_cfg[code]
            cond = bool(svc.get("conditional")) and why not in ("note", "service_column")
            added[code] = PlannedService(code, str(svc["name"]), why, str(source or svc.get("source", "")),
                                         confirm, cond)

    rule_type = rule_visit_type(note, catalog)
    sym = (catalog.get("visit_flow") or {}).get("pregnancy_symptom_keywords", [])
    if rule_type == "GYN" and ga and sym:
        gyn_hits = [k for k in types_cfg["GYN"].get("keywords", []) if _has(_plain(note), k)]
        if gyn_hits and all(k in sym for k in gyn_hits):
            rule_type = "ANC"  # "36w ra máu": có tuần thai → triệu chứng thai kỳ, không phải khám phụ khoa
    visit_from = "rule"
    visit_type = rule_type
    needs_kw = (catalog.get("visit_flow") or {}).get("ai_needs_keyword_types", [])
    if ai_visit_type in needs_kw and rule_type != ai_visit_type:
        ai_visit_type = None  # vd AI đoán "đăng ký gói" mà ghi chú không có "đăng ký / dky / mua gói" (CBNV ≠ gói)
    if ai_visit_type in types_cfg and ai_visit_type not in ("OTHER",):
        # AI chọn loại khám; quy tắc chỉ thắng khi AI trả loại chung chung còn quy tắc thấy loại cụ thể.
        generic = (catalog.get("visit_flow") or {}).get("generic_visit_types", ["ANC", "GYN", "OTHER"])
        if rule_type is None or rule_type in generic or ai_visit_type == rule_type:
            visit_type, visit_from = ai_visit_type, "ai"
    if visit_type is None:
        visit_type, visit_from = ("ANC" if ga else "OTHER"), "default"
    pre = urgent_rule(note, visit_type, ga, catalog)
    generic_types = (catalog.get("visit_flow") or {}).get("generic_visit_types", ["ANC", "GYN", "OTHER"])
    if pre and pre.get("set_visit_type") in types_cfg and visit_type in generic_types:
        visit_type, visit_from = str(pre["set_visit_type"]), "rule"  # vd "34w ra nước" → nghi rỉ ối

    for code in keyword_services(service_text, catalog):
        add(code, "service_column")
    for code in keyword_services(note, catalog):
        add(code, "note")
    excluded = set(types_cfg[visit_type].get("exclude_services", []))
    for code in excluded:  # vd tái khám sau hút thai ≠ làm thủ thuật (bỏ TRƯỚC khi xét ghi chú có ghi dịch vụ)
        added.pop(code, None)
    explicit = any(c != "KHAM" for c in added)
    flow = catalog.get("visit_flow") or {}
    if explicit and flow.get("note_services_only", True):
        add("KHAM", "visit_type", types_cfg[visit_type].get("source"))
    else:
        for code in types_cfg[visit_type].get("services", []):
            add(code, "visit_type", types_cfg[visit_type].get("source"))
        for code in ai_services or []:
            add(code, "ai")
    for code in types_cfg[visit_type].get("always_services", []):  # an toàn: vd nghi chuyển dạ luôn có CTG
        if code not in added:
            add(code, "safety", types_cfg[visit_type].get("source"))
    urule = urgent_rule(note, visit_type, ga, catalog)
    for code in (urule or {}).get("add_services", []):  # vd nghi rỉ ối → luôn SA + CTG
        if code not in added:
            add(code, "safety", types_cfg[visit_type].get("source"))
    if visit_type in anc_types(catalog) and ga:
        done = done_services(note, catalog)
        for code, src, confirm in milestone_services(ga[0], ga[1], catalog, [*added, *done]):
            if code in done:
                continue
            # Ghi chú đã ghi dịch vụ → chỉ thêm nhóm "có điều kiện" (để hiện "có thể cần"), không thêm dịch vụ tính giờ.
            if explicit and not services_cfg.get(code, {}).get("conditional"):
                continue
            add(code, "ga", src, confirm)
    for code in excluded:
        added.pop(code, None)
    # Đã có loại siêu âm / xét nghiệm cụ thể → bỏ "SA 2D" / "XN máu" chỉ khớp qua chữ chung "SA", "XN"
    # (rà soát 18/11: "SA NT", "SA đầu dò" thêm thừa SA 2D; "XN GBS" thêm thừa XN máu – GBS là lấy dịch)
    sa_types = set((catalog.get("visit_flow") or {}).get("specific_ultrasound_services", []))
    lab_types = set((catalog.get("visit_flow") or {}).get("specific_lab_services", []))
    if "SA_2D" in added and added["SA_2D"].why == "note" and sa_types & set(added) and _generic_only("SA_2D", note, catalog):
        del added["SA_2D"]
    if "SA_4D" in added and "SA_2D" in added and added["SA_2D"].why not in ("safety", "note"):
        del added["SA_2D"]
    if "XN_MAU" in added and added["XN_MAU"].why == "note" and lab_types & set(added) and _generic_only("XN_MAU", note, catalog):
        del added["XN_MAU"]
    plan = ServicePlan(
        guessed=guessed_terms(note, catalog), visit_type=visit_type,
        visit_name=str(types_cfg[visit_type]["name"]), services=list(added.values()),
        ga_weeks=ga[0] if ga else None, ga_days=ga[1] if ga else None, visit_from=visit_from,
        knowledge_version=str(catalog.get("version", "")), explicit=explicit,
        urgent=urule is not None or is_urgent(note, visit_type, ga, catalog),
        urgent_advice=str((urule or {}).get("advice", "")),
    )
    flow_cfg = catalog.get("visit_flow") or {}
    note_fasting = any(_has(_plain(note), k) for k in flow_cfg.get("fasting_note_keywords", []))
    svc_fasting = any(bool(services_cfg[s.code].get("fasting")) for s in plan.timed)
    prep_only = any(_has(_plain(note), k) for k in flow_cfg.get("prep_only_keywords", []))
    if svc_fasting and prep_only and not note_fasting and plan.visit_type not in flow_cfg.get("prep_only_exempt_types", []):
        # "chuẩn bị hồ sơ LEEP": hôm nay chưa chắc làm thủ thuật → không tự dặn nhịn ăn, hỏi lại (rà soát 18/11/2026)
        plan.fasting, plan.fasting_check = False, True
    else:
        plan.fasting = svc_fasting or note_fasting
    return plan


def plan_minutes(
    plan: ServicePlan, catalog: dict[str, Any], default_max: int
) -> tuple[int | None, int, str]:
    """(số phút dự phòng = max các dịch vụ tính giờ, trần sớm = max các trần, dịch vụ quyết định)."""
    best: int | None = None
    label = ""
    cap = default_max
    for s in plan.timed or plan.services:
        svc = catalog["services"][s.code]
        m = int(svc["minutes_early"])
        if best is None or m > best:
            best, label = m, s.name
        cap = max(cap, int(svc.get("max_minutes_early", default_max)))
    return best, cap, label


def source_titles(plan: ServicePlan, catalog: dict[str, Any]) -> list[str]:
    """Tên nguồn (không trùng) cho cột 'Căn cứ': nguồn tiếp đón + nguồn thời gian tối thiểu + nguồn dịch vụ."""
    srcs = catalog.get("sources", {})
    codes = [*plan.floor_sources, *(s.source for s in plan.services)]
    seen: list[str] = []
    for c in codes:
        src = srcs.get(c) or {}
        t = str(src.get("title", c))
        if src.get("tier") == "N":  # quyết định nội bộ: ghi rõ, không trộn với nguồn công khai
            t = f"Nội bộ – {t}" + (" (tạm)" if src.get("decision_status") == "provisional" else "")
        if t and t not in seen and src.get("tier", "A") != "X":
            seen.append(t)
    return seen


def sourced_floor(plan: ServicePlan, catalog: dict[str, Any]) -> tuple[int, list[str]]:
    """Giờ hẹn = giờ gặp bác sĩ, dịch vụ làm trước (NB_QUY_TRINH): tiếp đón + tổng thời gian tối thiểu có nguồn của
    các dịch vụ TÍNH GIỜ (bỏ nhóm có điều kiện). Ghi phép cộng vào plan.floor_parts. Trả (phút, mã nguồn)."""
    flow = catalog.get("visit_flow") or {}
    total = int(flow.get("reception_min", 0))
    srcs = [str(flow["reception_source"])] if flow.get("reception_source") else []
    parts: list[tuple[str, int]] = [("tiếp đón", total)]
    if flow.get("services_before_doctor", False):
        # Nhóm làm trong 1 lần (vd nhiều loại siêu âm) → chỉ tính loại lâu nhất (người dùng 18/11/2026)
        group_of = {code: g for g, codes in (flow.get("one_session_groups") or {}).items() for code in codes}
        best: dict[str, Any] = {}
        members: dict[str, list[str]] = {}
        for s in plan.timed:
            svc = catalog["services"][s.code]
            if not svc.get("min_minutes"):
                continue
            g = group_of.get(s.code)
            if g is None:
                best[s.code] = (s.name, int(svc["min_minutes"]), svc.get("min_source"))
                continue
            members.setdefault(g, []).append(s.name.removeprefix(f"{g} "))  # "Siêu âm 4D" → "4D" trong nhóm
            cur = best.get(f"group:{g}")
            if cur is None or int(svc["min_minutes"]) > cur[1]:
                best[f"group:{g}"] = (s.name, int(svc["min_minutes"]), svc.get("min_source"))
        for key, (name, mm, src) in best.items():
            if key.startswith("group:") and len(members[key[6:]]) > 1:
                name = f"{key[6:]} ({' + '.join(members[key[6:]])} – làm 1 lần, tính loại lâu nhất)"
            total += mm
            parts.append((name, mm))
            if src and src not in srcs:
                srcs.append(str(src))
    plan.floor_parts = parts
    return total, srcs
