"""Danh mục tri thức khám (config/visit_services.yaml) ↔ sổ Excel cho khoa duyệt (knowledge/danh_muc_kham.xlsx).

Quy trình (docs/17): khoa sửa Excel → `scripts/knowledge_import.py` kiểm tra (validate) → ghi người duyệt →
tăng phiên bản → ghi YAML + nhật ký thay đổi + audit_log. Code chỉ đọc YAML (luật 2 CLAUDE.md).
Hàm thuần: không đọc giao diện; đọc/ghi file chỉ ở to_workbook / from_workbook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

TIERS = ("A", "B", "C", "X")
ABBR_STATUS = {"Thông dụng": "common", "Đã xác nhận": "confirmed", "Đang đoán": "guess"}
QUOTE_STATUS = {"Đã duyệt": "approved", "Chờ duyệt": "pending", "Loại": "rejected"}
YES = {"có", "co", "x", "yes", "true", "1"}

SHEETS = ["Hướng dẫn", "Phê duyệt", "Dịch vụ", "Loại khám", "Viết tắt", "Mốc tuần thai", "Đã làm", "Quy trình",
          "Nguồn", "Trích dẫn", "Việc nội bộ"]

GUIDE = [
    "DANH MỤC TRI THỨC KHÁM – dùng để hệ thống suy ra dịch vụ và giờ đến từ ghi chú lịch hẹn OH.",
    "1. Chỉ sửa trong file này. Không sửa config/visit_services.yaml bằng tay (file đó được sinh ra từ đây).",
    "2. Mã (cột 'Mã') viết HOA không dấu, không đổi mã đang dùng – muốn bỏ thì xóa dòng.",
    "3. Nhiều giá trị trong một ô: phân cách bằng dấu ';'.",
    "4. Từ khóa / viết tắt so KHÔNG DẤU, không phân biệt hoa thường, khớp nguyên từ.",
    "5. Viết tắt 'Đang đoán' → lịch có viết tắt đó vào 'Cần xác nhận' cho tới khi bác sĩ / điều dưỡng xác nhận.",
    "6. Trích dẫn: chép NGUYÊN VĂN từ trang gốc. Chỉ dòng 'Đã duyệt' của nguồn 'Đã kiểm trang gốc = Có' được đưa cho AI.",
    "7. Nguồn cấp A = căn cứ; B = tham khảo; C = đối chiếu; X = không dùng.",
    "8. Sheet 'Phê duyệt': BẮT BUỘC điền Người duyệt, Chức danh, Ngày duyệt, Nội dung thay đổi trước khi nạp.",
    "9. Nạp: python scripts/knowledge_import.py knowledge/danh_muc_kham.xlsx  (thêm --apply để ghi; mặc định chỉ kiểm tra).",
]


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------- kiểm tra
def validate(cat: dict[str, Any]) -> Report:
    """Kiểm tra toàn vẹn danh mục. Lỗi = không được nạp; cảnh báo = vẫn nạp được nhưng cần xem."""
    rep = Report()
    e, w = rep.errors.append, rep.warnings.append
    if not cat.get("version"):
        e("Thiếu version")
    sources: dict[str, Any] = cat.get("sources") or {}
    services: dict[str, Any] = cat.get("services") or {}
    vtypes: dict[str, Any] = cat.get("visit_types") or {}
    for code, s in sources.items():
        if s.get("tier", "A") not in TIERS:
            e(f"Nguồn {code}: cấp '{s.get('tier')}' không hợp lệ (A/B/C/X)")
        if s.get("verified") and not s.get("url"):
            e(f"Nguồn {code}: đánh dấu đã kiểm nhưng không có link")
    for code, s in services.items():
        src = s.get("source")
        if src not in sources:
            e(f"Dịch vụ {code}: nguồn '{src}' không có trong sheet Nguồn")
        lo, hi = int(s.get("minutes_early", 0)), int(s.get("max_minutes_early", 0))
        if not 0 <= lo <= hi:
            e(f"Dịch vụ {code}: số phút dự phòng {lo} phải ≥ 0 và ≤ giới hạn an toàn {hi}")
        if s.get("min_minutes") is not None:
            mn = int(s["min_minutes"])
            if mn > hi:
                e(f"Dịch vụ {code}: tối thiểu có nguồn {mn} > giới hạn an toàn {hi}")
            ms = s.get("min_source")
            if not ms or not (sources.get(ms) or {}).get("verified"):
                e(f"Dịch vụ {code}: số phút tối thiểu phải có nguồn đã kiểm trang gốc (min_source='{ms}')")
    for code, v in vtypes.items():
        miss = [x for x in v.get("services", []) if x not in services]
        if miss:
            e(f"Loại khám {code}: dịch vụ không tồn tại {miss}")
        if v.get("source") not in sources:
            e(f"Loại khám {code}: nguồn '{v.get('source')}' không có")
    for i, m in enumerate(cat.get("ga_milestones") or [], start=1):
        miss = [x for x in [*m.get("services", []), *m.get("only_if_missing", [])] if x not in services]
        if miss:
            e(f"Mốc tuần thai dòng {i}: dịch vụ không tồn tại {miss}")
        if not 4 <= int(m["from_week"]) <= int(m["to_week"]) <= 42:
            e(f"Mốc tuần thai dòng {i}: tuần {m['from_week']}–{m['to_week']} không hợp lệ")
        if m.get("source") not in sources:
            e(f"Mốc tuần thai dòng {i}: nguồn '{m.get('source')}' không có")
    targets = set(services) | set(vtypes)
    seen: dict[str, str] = {}
    for a in cat.get("abbreviations") or []:
        bad = [x for x in a.get("maps_to", []) if x not in targets]
        if bad:
            e(f"Viết tắt '{a.get('term')}': ánh xạ tới mã không tồn tại {bad}")
        if a.get("status") not in ABBR_STATUS.values():
            e(f"Viết tắt '{a.get('term')}': trạng thái '{a.get('status')}' không hợp lệ")
        t = str(a.get("term", "")).strip().lower()
        if t in seen:
            e(f"Viết tắt '{t}' bị khai báo 2 lần")
        seen[t] = "x"
    for i, f in enumerate(cat.get("facts") or [], start=1):
        src = sources.get(f.get("source"))
        if src is None:
            e(f"Trích dẫn dòng {i}: nguồn '{f.get('source')}' không có")
            continue
        if not str(f.get("quote", "")).strip():
            e(f"Trích dẫn dòng {i}: thiếu câu trích nguyên văn")
        if not src.get("verified"):
            e(f"Trích dẫn dòng {i}: nguồn {f.get('source')} chưa kiểm trang gốc – không được duyệt trích dẫn")
        if src.get("tier") in ("C", "X"):
            e(f"Trích dẫn dòng {i}: nguồn cấp {src.get('tier')} không được làm căn cứ")
        miss = [x for x in f.get("services", []) if x not in services]
        if miss:
            e(f"Trích dẫn dòng {i}: dịch vụ không tồn tại {miss}")
    flow = cat.get("visit_flow") or {}
    if flow.get("reception_source") and flow["reception_source"] not in sources:
        e("Quy trình: nguồn tiếp đón không có trong sheet Nguồn")
    # cảnh báo: cùng từ khóa ở nhiều loại khám → loại đứng trước thắng
    kw_owner: dict[str, str] = {}
    for code, v in vtypes.items():
        for k in dict.fromkeys(v.get("keywords", [])):
            if k in kw_owner:
                w(f"Từ khóa '{k}' có ở cả {kw_owner[k]} và {code} – {kw_owner[k]} (đứng trước) được chọn")
            else:
                kw_owner[k] = code
    guess = [a["term"] for a in cat.get("abbreviations") or [] if a.get("status") == "guess"]
    if guess:
        w(f"{len(guess)} viết tắt còn 'Đang đoán' (lịch có các từ này sẽ vào Cần xác nhận): {', '.join(guess)}")
    pend = sum(1 for f in cat.get("pending_quotes") or [])
    if pend:
        w(f"{pend} trích dẫn đang 'Chờ duyệt' – chưa đưa cho AI")
    return rep


# ---------------------------------------------------------------- YAML → Excel
def _j(xs: Any) -> str:
    return "; ".join(str(x) for x in (xs or []))


def _yn(v: Any) -> str:
    return "Có" if v else "Không"


def to_frames(cat: dict[str, Any]) -> dict[str, pd.DataFrame]:
    meta = ((cat.get("source_catalog") or {}).get("meta")) or {}
    notes = cat.get("verification_notes") or {}
    abbr_rev = {v: k for k, v in ABBR_STATUS.items()}
    q_rev = {v: k for k, v in QUOTE_STATUS.items()}
    approval = cat.get("approval") or {}
    frames: dict[str, pd.DataFrame] = {
        "Hướng dẫn": pd.DataFrame({"Hướng dẫn": GUIDE}),
        "Phê duyệt": pd.DataFrame([
            {"Mục": "Phiên bản hiện tại", "Giá trị": cat.get("version", "")},
            {"Mục": "Người duyệt", "Giá trị": ""},
            {"Mục": "Chức danh", "Giá trị": ""},
            {"Mục": "Ngày duyệt", "Giá trị": ""},
            {"Mục": "Nội dung thay đổi", "Giá trị": ""},
            {"Mục": "Lần duyệt trước", "Giá trị": " · ".join(str(approval.get(k, "")) for k in
                                                             ("by", "role", "at", "summary") if approval.get(k))},
        ]),
        "Dịch vụ": pd.DataFrame([{
            "Mã": c, "Tên": s["name"], "Từ khóa": _j(s.get("keywords")),
            "Đến trước dự phòng (phút)": s.get("minutes_early"),
            "Tối thiểu có nguồn (phút)": s.get("min_minutes", ""), "Nguồn tối thiểu": s.get("min_source", ""),
            "Giới hạn an toàn (phút)": s.get("max_minutes_early"), "Nhịn ăn": _yn(s.get("fasting")),
            "Nguồn": s.get("source", ""), "Ghi chú": s.get("note", ""),
        } for c, s in cat["services"].items()]),
        "Loại khám": pd.DataFrame([{
            "Thứ tự ưu tiên": i, "Mã": c, "Tên": v["name"], "Từ khóa": _j(v.get("keywords")),
            "Dịch vụ thường làm": _j(v.get("services")), "Nguồn": v.get("source", ""),
        } for i, (c, v) in enumerate(cat["visit_types"].items(), start=1)]),
        "Viết tắt": pd.DataFrame([{
            "Viết tắt": a["term"], "Nghĩa": a.get("meaning", ""), "Ánh xạ tới (mã)": _j(a.get("maps_to")),
            "Trạng thái": abbr_rev.get(a.get("status", "guess"), "Đang đoán"),
            "Người xác nhận": a.get("confirmed_by", ""), "Ngày": a.get("confirmed_at", ""),
        } for a in cat.get("abbreviations") or []],
            columns=["Viết tắt", "Nghĩa", "Ánh xạ tới (mã)", "Trạng thái", "Người xác nhận", "Ngày"]),
        "Mốc tuần thai": pd.DataFrame([{
            "Từ tuần": m["from_week"], "Đến tuần": m["to_week"], "Đến ngày": m.get("to_day", 6),
            "Dịch vụ": _j(m["services"]), "Chỉ khi chưa có": _j(m.get("only_if_missing")),
            "Cần xác nhận": _yn(m.get("confirm")), "Nguồn": m.get("source", ""),
        } for m in cat.get("ga_milestones") or []]),
        "Đã làm": pd.DataFrame({"Dấu hiệu 'đã làm' trong ghi chú": cat.get("done_markers") or []}),
        "Quy trình": pd.DataFrame([{"Khóa": k, "Giá trị": v} for k, v in (cat.get("visit_flow") or {}).items()]),
        "Nguồn": pd.DataFrame([{
            "Mã": c, "Cấp": s.get("tier") or (meta.get(c) or {}).get("tier") or ("A" if s.get("verified") else "B"),
            "Tổ chức": s.get("org") or (meta.get(c) or {}).get("org", ""), "Tiêu đề": s.get("title", ""),
            "Link": s.get("url", ""), "Dùng cho": s.get("used_for") or (meta.get(c) or {}).get("used_for", ""),
            "Tóm tắt": s.get("info") or (meta.get(c) or {}).get("info", ""),
            "Đã kiểm trang gốc": _yn(s.get("verified")), "Ngày kiểm": s.get("checked_at", ""),
            "Ghi chú kiểm chứng": s.get("check_note") or notes.get(c, "")
            or (meta.get(c) or {}).get("user_note", ""),
        } for c, s in {**cat["sources"], **(cat.get("extra_sources") or {})}.items()]),
        "Trích dẫn": pd.DataFrame([{
            "Mã nguồn": f["source"], "Dịch vụ áp dụng": _j(f.get("services")),
            "Câu trích nguyên văn": f.get("quote") or f.get("text", ""), "Trạng thái": q_rev.get("approved"),
            "Ngày truy cập": f.get("accessed", ""),
        } for f in cat.get("facts") or []] + [{
            "Mã nguồn": f["source"], "Dịch vụ áp dụng": _j(f.get("services")),
            "Câu trích nguyên văn": f.get("quote", ""), "Trạng thái": q_rev.get(f.get("status", "pending")),
            "Ngày truy cập": f.get("accessed", ""),
        } for f in cat.get("pending_quotes") or []]),
        "Việc nội bộ": pd.DataFrame([{
            "Mã": k, "Cần có": v.get("need", ""), "Lấy từ đâu / ai duyệt": v.get("owner", ""),
            "Vì sao cần": v.get("why", ""), "Trạng thái": v.get("status", ""),
        } for k, v in (cat.get("internal_required") or {}).items()]),
    }
    return frames


def to_workbook(cat: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = to_frames(cat)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name in SHEETS:
            frames[name].to_excel(w, index=False, sheet_name=name)
        for ws in w.book.worksheets:  # độ rộng cột dễ đọc, cố định dòng tiêu đề
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 80)


# ---------------------------------------------------------------- Excel → YAML
def _split(v: Any) -> list[str]:
    return [x.strip() for x in str(v or "").replace(",", ";").split(";") if x.strip()]


def _int(v: Any) -> int | None:
    s = str(v).strip()
    if s in ("", "nan", "None"):
        return None
    return int(float(s))


def _str(v: Any) -> str:
    s = str(v if v is not None else "").strip()
    return "" if s in ("nan", "None") else s


def from_workbook(path: Path, base: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Đọc sổ Excel → danh mục mới (giữ các mục không có trong sổ từ `base`). Trả (danh mục, thông tin phê duyệt)."""
    x = pd.read_excel(path, sheet_name=None, dtype=str)
    x = {k: v.fillna("") for k, v in x.items()}
    missing = [s for s in SHEETS if s not in x]
    if missing:
        raise ValueError(f"Thiếu sheet: {', '.join(missing)}")
    cat = {k: v for k, v in base.items() if k not in ("source_catalog", "extra_sources", "verification_notes")}

    approval = {str(r["Mục"]).strip(): _str(r["Giá trị"]) for _, r in x["Phê duyệt"].iterrows()}

    cat["services"] = {}
    for _, r in x["Dịch vụ"].iterrows():
        code = _str(r["Mã"])
        if not code:
            continue
        svc: dict[str, Any] = {
            "name": _str(r["Tên"]), "minutes_early": _int(r["Đến trước dự phòng (phút)"]) or 0,
            "max_minutes_early": _int(r["Giới hạn an toàn (phút)"]) or 90,
            "fasting": _str(r["Nhịn ăn"]).lower() in YES, "source": _str(r["Nguồn"]),
            "keywords": _split(r["Từ khóa"]),
        }
        if _int(r["Tối thiểu có nguồn (phút)"]) is not None:
            svc["min_minutes"] = _int(r["Tối thiểu có nguồn (phút)"])
            svc["min_source"] = _str(r["Nguồn tối thiểu"])
        if _str(r["Ghi chú"]):
            svc["note"] = _str(r["Ghi chú"])
        cat["services"][code] = svc

    vt = x["Loại khám"].copy()
    vt["_o"] = vt["Thứ tự ưu tiên"].map(lambda v: _int(v) or 999)
    cat["visit_types"] = {
        _str(r["Mã"]): {"name": _str(r["Tên"]), "services": _split(r["Dịch vụ thường làm"]),
                        "source": _str(r["Nguồn"]), "keywords": _split(r["Từ khóa"])}
        for _, r in vt.sort_values("_o", kind="stable").iterrows() if _str(r["Mã"])
    }

    cat["abbreviations"] = []
    for _, r in x["Viết tắt"].iterrows():
        if not _str(r["Viết tắt"]):
            continue
        st = ABBR_STATUS.get(_str(r["Trạng thái"]))
        a: dict[str, Any] = {"term": _str(r["Viết tắt"]), "meaning": _str(r["Nghĩa"]),
                             "maps_to": _split(r["Ánh xạ tới (mã)"]), "status": st or f"?{_str(r['Trạng thái'])}"}
        if _str(r["Người xác nhận"]):
            a["confirmed_by"], a["confirmed_at"] = _str(r["Người xác nhận"]), _str(r["Ngày"])
        cat["abbreviations"].append(a)

    cat["ga_milestones"] = []
    for _, r in x["Mốc tuần thai"].iterrows():
        if _int(r["Từ tuần"]) is None:
            continue
        m: dict[str, Any] = {"from_week": _int(r["Từ tuần"]), "to_week": _int(r["Đến tuần"]),
                             "to_day": _int(r["Đến ngày"]) if _int(r["Đến ngày"]) is not None else 6,
                             "services": _split(r["Dịch vụ"]), "source": _str(r["Nguồn"])}
        if _split(r["Chỉ khi chưa có"]):
            m["only_if_missing"] = _split(r["Chỉ khi chưa có"])
        if _str(r["Cần xác nhận"]).lower() in YES:
            m["confirm"] = True
        cat["ga_milestones"].append(m)

    cat["done_markers"] = [_str(v) for v in x["Đã làm"].iloc[:, 0] if _str(v)]

    flow: dict[str, Any] = {}
    for _, r in x["Quy trình"].iterrows():
        k, v = _str(r["Khóa"]), _str(r["Giá trị"])
        if k:
            flow[k] = (v.lower() == "true") if v.lower() in ("true", "false") else (int(v) if v.isdigit() else v)
    cat["visit_flow"] = flow

    cat["sources"] = {}
    for _, r in x["Nguồn"].iterrows():
        code = _str(r["Mã"])
        if not code:
            continue
        s: dict[str, Any] = {"title": _str(r["Tiêu đề"]), "url": _str(r["Link"]), "tier": _str(r["Cấp"]) or "B",
                             "verified": _str(r["Đã kiểm trang gốc"]).lower() in YES}
        for col, key in (("Tổ chức", "org"), ("Dùng cho", "used_for"), ("Tóm tắt", "info"),
                         ("Ngày kiểm", "checked_at"), ("Ghi chú kiểm chứng", "check_note")):
            if _str(r[col]):
                s[key] = _str(r[col])
        cat["sources"][code] = s

    cat["facts"], cat["pending_quotes"] = [], []
    for _, r in x["Trích dẫn"].iterrows():
        q = _str(r["Câu trích nguyên văn"])
        if not q:
            continue
        f = {"services": _split(r["Dịch vụ áp dụng"]), "source": _str(r["Mã nguồn"]), "quote": q}
        if _str(r["Ngày truy cập"]):
            f["accessed"] = _str(r["Ngày truy cập"])
        st = QUOTE_STATUS.get(_str(r["Trạng thái"]), "pending")
        if st == "approved":
            cat["facts"].append(f)
        elif st == "pending":
            cat["pending_quotes"].append({**f, "status": "pending"})

    cat["internal_required"] = {
        _str(r["Mã"]): {"need": _str(r["Cần có"]), "owner": _str(r["Lấy từ đâu / ai duyệt"]),
                        "why": _str(r["Vì sao cần"]), "status": _str(r["Trạng thái"])}
        for _, r in x["Việc nội bộ"].iterrows() if _str(r["Mã"])
    }
    return cat, approval


def diff_summary(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Tóm tắt thay đổi để ghi nhật ký và cho người duyệt xem."""
    out: list[str] = []
    for key, label in (("services", "Dịch vụ"), ("visit_types", "Loại khám"), ("sources", "Nguồn")):
        a, b = set(old.get(key) or {}), set(new.get(key) or {})
        if b - a:
            out.append(f"{label} thêm: {', '.join(sorted(b - a))}")
        if a - b:
            out.append(f"{label} bỏ: {', '.join(sorted(a - b))}")
        changed = sorted(k for k in a & b if (old[key][k] != new[key][k]))
        if changed:
            out.append(f"{label} sửa: {', '.join(changed)}")
    for key, label in (("abbreviations", "Viết tắt"), ("ga_milestones", "Mốc tuần thai"), ("facts", "Trích dẫn"),
                       ("done_markers", "Dấu hiệu đã làm")):
        a, b = old.get(key) or [], new.get(key) or []
        if a != b:
            out.append(f"{label}: {len(a)} → {len(b)} dòng")
    if (old.get("visit_flow") or {}) != (new.get("visit_flow") or {}):
        out.append("Quy trình buổi khám thay đổi")
    return out or ["Không có thay đổi nội dung"]


def bump_minor(version: str) -> str:
    parts = [int(p) for p in str(version).split(".")[:3]] + [0, 0, 0]
    return f"{parts[0]}.{parts[1] + 1}.0"


HEADER = """# ⚠ FILE SINH TỰ ĐỘNG từ knowledge/danh_muc_kham.xlsx bằng scripts/knowledge_import.py – KHÔNG sửa tay.
# Muốn sửa: xuất sổ Excel (scripts/knowledge_export.py) → khoa sửa + điền Phê duyệt → nạp lại (docs/17).
# Danh mục loại khám → dịch vụ → câu trích nguồn chính thống; AI chỉ dùng trích dẫn đã duyệt của nguồn đã kiểm.
# minutes_early = số dự phòng (chưa có căn cứ); min_minutes = tối thiểu có nguồn; max_minutes_early = giới hạn an toàn.
"""


def dump_yaml(cat: dict[str, Any]) -> str:
    import yaml

    order = ["version", "checked_at", "approval", "visit_flow", "services", "visit_types", "abbreviations",
             "done_markers", "ga_milestones", "sources", "facts", "pending_quotes", "internal_required"]
    body = {k: cat[k] for k in order if k in cat}
    body.update({k: v for k, v in cat.items() if k not in body})
    return HEADER + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=140)
