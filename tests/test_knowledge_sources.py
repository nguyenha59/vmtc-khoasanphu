"""Bản chụp trang nguồn, câu trích nguyên văn, quyết định nội bộ cấp N, kiểm tra lại nguồn (knowledge/README.md mục 6)."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from core import knowledge_sources as ks
from core.config import ROOT, TZ, load_visit_services
from core.knowledge import minutes_in_quote, validate
from core.rules.visit_infer import ServicePlan, source_titles

KNOW = ROOT / "knowledge"
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=TZ)
PAGE = ("<html><head><title>Bài &amp; viết</title><script>var x='15 phút';</script></head><body><nav>Menu</nav>"
        "<p>Thời gian khám dao động từ ​​15 phút đến <b>1</b> giờ.</p><p>Thai nhi 11 - 13 tuần tuổi <i>,</i>"
        " vào tuần thứ 12.</p><footer>© 2026</footer></body></html>")


@pytest.fixture(scope="module")
def cat() -> dict:
    return load_visit_services()


# ---------------------------------------------------------------- so câu trích
def test_normalize_handles_invisible_chars_spacing_and_trailing_period() -> None:
    text = ks.html_to_text(PAGE)
    assert ks.quote_in_text("Thời gian khám dao động từ 15 phút đến 1 giờ.", text)  # ký tự vô hình, thẻ <b>
    assert ks.quote_in_text("Thai nhi 11 - 13 tuần tuổi, vào tuần thứ 12", text)  # "tuổi , vào"
    assert ks.quote_in_text("thai nhi 11 – 13 tuần tuổi", text)  # gạch ngang dài, chữ hoa/thường
    assert not ks.quote_in_text("Thời gian khám dao động từ 15 phút đến 2 giờ", text)
    assert not ks.quote_in_text("", text)


def test_html_to_text_drops_scripts_menus_and_footer() -> None:
    text = ks.html_to_text(PAGE)
    assert "var x" not in text and "Menu" not in text and "© 2026" not in text
    assert ks.html_title(PAGE) == "Bài & viết"


def test_minutes_in_quote_accepts_hours() -> None:
    assert minutes_in_quote(30, "khoảng 15-30 phút")
    assert minutes_in_quote(120, "xét nghiệm lại sau 1 giờ và 2 giờ.")
    assert not minutes_in_quote(20, "120 phút")
    assert not minutes_in_quote(60, "1,5 giờ")


# ---------------------------------------------------------------- bản chụp
def test_save_snapshot_dedupes_and_records_hash(tmp_path: Path) -> None:
    a = ks.save_snapshot(tmp_path, "SRC_A", "https://x.vn/a", "https://x.vn/a", 200, "text/html; charset=utf-8",
                         PAGE.encode(), NOW)
    assert a.path == "sources/snapshots/SRC_A/2026-11-20.txt" and a.text_ok and a.title == "Bài & viết"
    assert ks.file_sha(tmp_path, a.path) == a.sha256
    meta = json.loads((tmp_path / "sources/snapshots/SRC_A/2026-11-20.meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == "https://x.vn/a" and meta["sha256"] == a.sha256
    again = ks.save_snapshot(tmp_path, "SRC_A", "https://x.vn/a", "https://x.vn/a", 200, "text/html", PAGE.encode(),
                             NOW.replace(day=21))
    assert again == a and len(list((tmp_path / "sources/snapshots/SRC_A").glob("*.txt"))) == 1  # trùng → không lưu thêm
    b = ks.save_snapshot(tmp_path, "SRC_A", "https://x.vn/a", "https://x.vn/a", 200, "text/html",
                         PAGE.replace("1</b> giờ", "2</b> giờ").encode(), NOW.replace(day=22))
    assert b.path.endswith("2026-11-22.txt") and ks.latest_snapshot(tmp_path, "SRC_A") == b


def test_pdf_snapshot_is_not_usable_as_text(tmp_path: Path) -> None:
    s = ks.save_snapshot(tmp_path, "SRC_PDF", "https://x.vn/a.pdf", "https://x.vn/a.pdf", 200, "application/pdf",
                         b"%PDF-1.4 ...", NOW)
    assert s.kind == "pdf" and not s.text_ok and s.path.endswith(".pdf")
    assert ks.read_snapshot(tmp_path, s.path) is None


def test_snapshot_paths_cannot_escape_snapshot_dir(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    assert ks.read_snapshot(tmp_path, "../secret.txt") is None
    assert ks.read_snapshot(tmp_path, "secret.txt") is None
    assert ks.file_sha(tmp_path, "secret.txt") is None
    with pytest.raises(ValueError):
        ks.save_snapshot(tmp_path, "../X", "https://x.vn", "https://x.vn", 200, "text/html", b"x", NOW)


def test_fetch_rejects_non_http_links() -> None:
    with pytest.raises(ValueError):
        ks.fetch("file:///etc/passwd")


def test_compare_statuses() -> None:
    text = ks.html_to_text(PAGE)
    sha = ks.sha256_bytes(text.encode())
    q = ["Thời gian khám dao động từ 15 phút đến 1 giờ."]
    assert ks.compare(sha, text, q) == "UNCHANGED"
    assert ks.compare("khac", text + "Thêm dòng\n", q) == "CHANGED_OK"
    assert ks.compare("khac", "Trang mới hoàn toàn", q) == "QUOTE_GONE"
    assert ks.compare(sha, None, q) == "DEAD"
    assert ks.compare(None, text, q) == "NO_SNAPSHOT"


# ---------------------------------------------------------------- kiểm tra khi nạp
def test_every_approved_quote_is_verbatim_in_its_snapshot(cat: dict) -> None:
    for f in cat["facts"]:
        assert f.get("snapshot") and f.get("sha256"), f
        text = ks.read_snapshot(KNOW, f["snapshot"])
        assert text is not None and ks.quote_in_text(f["quote"], text), f["quote"]


@pytest.mark.parametrize(("mutate", "needle"), [
    (lambda c: c["facts"][0].pop("snapshot"), "chưa có bản chụp"),
    (lambda c: c["facts"][0].update(quote="Đến trước giờ hẹn từ 60 phút."), "không có nguyên văn"),
    (lambda c: c["facts"][0].update(sha256="0" * 64), "mã băm không khớp"),
    (lambda c: c["facts"][0].update(snapshot="sources/snapshots/KHONG_CO/2026-01-01.txt"), "không thấy bản chụp"),
    (lambda c: c["facts"].append({**c["facts"][0], "source": "NGUOI_DUNG_1811"}), "quyết định nội bộ"),
    (lambda c: c["sources"]["NGUOI_DUNG_1811"].update(tier="A"), "phải để cấp N"),
    (lambda c: c["sources"]["NGUOI_DUNG_1811"].pop("decided_by"), "Người quyết định"),
    (lambda c: c["sources"]["NGUOI_DUNG_1811"].pop("review_by"), "Hạn xem lại"),
    (lambda c: c["sources"]["NGUOI_DUNG_1811"].update(decision_status="?"), "Tình trạng"),
    (lambda c: c["sources"]["VMC_CTG"].update(url=""), "không có link"),
])
def test_validate_rejects_unsourced_knowledge(cat: dict, mutate, needle: str) -> None:  # noqa: ANN001
    c = copy.deepcopy(cat)
    mutate(c)
    rep = validate(c)
    assert not rep.ok and any(needle in e for e in rep.errors), rep.errors


def test_provisional_internal_decision_and_failed_check_are_warned(cat: dict) -> None:
    c = copy.deepcopy(cat)
    c["sources"]["VMC_CTG"]["auto_check"] = {"status": "QUOTE_GONE", "at": "2026-12-20"}
    rep = validate(c)
    assert rep.ok, rep.errors
    assert any("NGUOI_DUNG_1811" in w and "TẠM" in w for w in rep.warnings)
    assert any("VMC_CTG" in w and "không còn" in w for w in rep.warnings)
    c["sources"]["NGUOI_DUNG_1811"]["decision_status"] = "final"
    assert not any("NGUOI_DUNG_1811" in w for w in validate(c).warnings)


def test_basis_column_marks_internal_decisions(cat: dict) -> None:
    plan = ServicePlan(visit_type="ANC", visit_name="Khám thai", services=[])
    plan.floor_sources = ["VMC_DEN_TRUOC", "NGUOI_DUNG_1811", "UOC_LUONG"]
    titles = source_titles(plan, cat)
    assert titles[0] == cat["sources"]["VMC_DEN_TRUOC"]["title"]
    assert titles[1].startswith("Nội bộ – ") and titles[1].endswith("(tạm)")
    assert len(titles) == 2  # cấp X không in


# ---------------------------------------------------------------- kiểm tra lại định kỳ
def test_check_source_reports_gone_quote_and_keeps_new_snapshot(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    sys.path.insert(0, str(ROOT / "scripts"))
    import knowledge_check_sources as chk

    ks.save_snapshot(tmp_path, "SRC_A", "https://x.vn/a", "https://x.vn/a", 200, "text/html", PAGE.encode(), NOW)
    quote = ["Thời gian khám dao động từ 15 phút đến 1 giờ."]
    pages = {"same": PAGE.encode(), "changed": b"<p>Trang viet lai, khong con cau cu.</p>"}
    monkeypatch.setattr(ks, "fetch", lambda url: (200, url, "text/html", pages[url.rsplit("/", 1)[-1]]))
    assert chk.check_one(tmp_path, "SRC_A", "https://x.vn/same", quote, NOW)[0] == "UNCHANGED"
    st, detail = chk.check_one(tmp_path, "SRC_A", "https://x.vn/changed", quote, NOW.replace(day=21))
    assert st == "QUOTE_GONE" and "1 câu không còn" in detail
    assert ks.latest_snapshot(tmp_path, "SRC_A").path.endswith("2026-11-21.txt")  # giữ bằng chứng trang đã đổi

    def dead(url: str) -> tuple:
        raise ConnectionError("down")

    monkeypatch.setattr(ks, "fetch", dead)
    assert chk.check_one(tmp_path, "SRC_A", "https://x.vn/a", quote, NOW)[0] == "DEAD"
