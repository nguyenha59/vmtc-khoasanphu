"""Quy trình nạp danh mục tri thức khám: Excel ↔ YAML, kiểm tra, phê duyệt, viết tắt chưa xác nhận (docs/17)."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest
import yaml

from core.config import ROOT, load_visit_services
from core.knowledge import diff_summary, from_workbook, to_workbook, validate


@pytest.fixture(scope="module")
def cat() -> dict:
    return load_visit_services()


def test_current_catalog_is_valid(cat: dict) -> None:
    rep = validate(cat)
    assert rep.ok, rep.errors


def test_round_trip_is_lossless(cat: dict, tmp_path: Path) -> None:
    x = tmp_path / "k.xlsx"
    to_workbook(cat, x)
    new, _ = from_workbook(x, cat)
    assert diff_summary(cat, new) == ["Không có thay đổi nội dung"]


@pytest.mark.parametrize(("mutate", "needle"), [
    (lambda c: c["facts"].append({"services": ["CTG"], "source": "HB_GOI_27", "quote": "x"}), "chưa kiểm trang gốc"),
    (lambda c: c["abbreviations"].append({"term": "zz", "maps_to": ["KHONG_CO"], "status": "guess"}), "không tồn tại"),
    (lambda c: c["services"]["CTG"].update(min_source="UOC_LUONG"), "nguồn đã kiểm"),
    (lambda c: c["services"]["KHAM"].update(minutes_early=500), "giới hạn an toàn"),
    (lambda c: c["visit_types"]["ANC"]["services"].append("KHONG_CO"), "không tồn tại"),
])
def test_validate_rejects_bad_catalog(cat: dict, mutate, needle: str) -> None:  # noqa: ANN001
    c = copy.deepcopy(cat)
    mutate(c)
    rep = validate(c)
    assert not rep.ok and any(needle in e for e in rep.errors), rep.errors


def _run_import(x: Path, tmp: Path, apply: bool) -> subprocess.CompletedProcess:
    target = tmp / "visit_services.yaml"
    if not target.exists():
        target.write_text((ROOT / "config" / "visit_services.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    env = {**os.environ, "APP_VISIT_SERVICES_FILE": str(target), "APP_KNOWLEDGE_DIR": str(tmp / "knowledge"),
           "APP_DATA_DIR": str(tmp / "data"), "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, str(ROOT / "scripts" / "knowledge_import.py"), str(x)] + (["--apply"] if apply else [])
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)


def _edit(x: Path, sheet: str, fn) -> None:  # noqa: ANN001
    book = {k: v.fillna("") for k, v in pd.read_excel(x, sheet_name=None, dtype=str).items()}
    book[sheet] = fn(book[sheet])
    with pd.ExcelWriter(x, engine="openpyxl") as w:
        for name, df in book.items():
            df.to_excel(w, index=False, sheet_name=name)


def test_import_requires_approval_then_bumps_version(cat: dict, tmp_path: Path) -> None:
    x = tmp_path / "k.xlsx"
    to_workbook(cat, x)

    def confirm_tkss(df: pd.DataFrame) -> pd.DataFrame:
        df.loc[df["Viết tắt"] == "tkss", ["Trạng thái", "Người xác nhận", "Ngày"]] = ["Đã xác nhận", "ĐD Test", "17/11/2026"]
        return df

    _edit(x, "Viết tắt", confirm_tkss)
    r = _run_import(x, tmp_path, apply=True)
    assert r.returncode == 1 and "Phê duyệt" in r.stdout  # chưa điền người duyệt → không nạp

    def approve(df: pd.DataFrame) -> pd.DataFrame:
        vals = {"Người duyệt": "BS Test", "Chức danh": "Trưởng khoa", "Ngày duyệt": "17/11/2026",
                "Nội dung thay đổi": "Xác nhận tkss = tái khám sau sinh"}
        df["Giá trị"] = [vals.get(m, v) for m, v in zip(df["Mục"], df["Giá trị"], strict=True)]
        return df

    _edit(x, "Phê duyệt", approve)
    r = _run_import(x, tmp_path, apply=True)
    assert r.returncode == 0, r.stdout + r.stderr
    new = yaml.safe_load((tmp_path / "visit_services.yaml").read_text(encoding="utf-8"))
    assert new["version"] != cat["version"] and new["approval"]["by"] == "BS Test"
    assert next(a for a in new["abbreviations"] if a["term"] == "tkss")["status"] == "confirmed"
    assert (tmp_path / "knowledge" / "history" / f"visit_services_{cat['version']}.yaml").exists()
    assert "BS Test" in (tmp_path / "knowledge" / "CHANGELOG.md").read_text(encoding="utf-8")


def test_import_rejects_invalid_workbook(cat: dict, tmp_path: Path) -> None:
    x = tmp_path / "k.xlsx"
    to_workbook(cat, x)
    _edit(x, "Mốc tuần thai", lambda df: df.assign(**{"Dịch vụ": ["KHONG_CO"] * len(df)}))
    r = _run_import(x, tmp_path, apply=False)
    assert r.returncode == 1 and "KHÔNG nạp" in r.stdout


def test_guessed_abbreviation_goes_to_review(rules, monkeypatch) -> None:  # noqa: ANN001
    import core.pipeline as pipeline
    from core.pipeline import plan_appointment

    cat = copy.deepcopy(load_visit_services())
    for a in cat["abbreviations"]:
        if a["term"] == "tkss":
            a["status"] = "guess"
    monkeypatch.setattr(pipeline, "load_visit_services", lambda: cat)
    from core.rules.merge import MergedAppt, Slot

    def plan(note: str):  # noqa: ANN202
        m = MergedAppt(pid="SYN1", appt_date=date(2026, 11, 17), start_time=time(9, 0), end_time=None, services=[],
                       note=note, full_name="Khách Giả", phone="0901000001", phone_raw="", nationality="Việt Nam",
                       department="", doctor="", booking_source="", booked_at=None, slots=[Slot(time(9, 0), "", note)])
        return plan_appointment(m, rules, None, [], ner=False)

    p = plan("tkss thường")
    assert p.service_plans[0].visit_type == "POSTPARTUM"  # vẫn hiểu theo nghĩa đoán
    assert "ABBR_GUESS" in p.decision.reasons and any("tkss" in d for d in p.arrival.details)
    assert "ABBR_GUESS" not in plan("tái khám sau sinh thường").decision.reasons
