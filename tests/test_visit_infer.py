"""Suy loại khám + dịch vụ từ ghi chú (file OH không có cột Dịch vụ) – config/visit_services.yaml."""

from __future__ import annotations

import pytest

from core.config import load_visit_services
from core.rules.note_patterns import find_ga
from core.rules.visit_infer import infer_services, plan_minutes


@pytest.fixture(scope="module")
def cat() -> dict:
    return load_visit_services()


def plan(note: str, cat: dict, **kw):  # noqa: ANN003, ANN201
    return infer_services(note, cat, ga=find_ga(note), **kw)


def test_catalog_sources_are_complete(cat: dict) -> None:
    """Mọi dịch vụ / loại khám / mốc tuần thai đều có nguồn trong danh mục nguồn."""
    srcs = set(cat["sources"])
    for code, svc in cat["services"].items():
        assert svc["source"] in srcs, code
        assert svc["minutes_early"] <= svc["max_minutes_early"], code
    for code, vt in cat["visit_types"].items():
        assert vt["source"] in srcs, code
        assert set(vt["services"]) <= set(cat["services"]), code
    for m in cat["ga_milestones"]:
        assert m["source"] in srcs and set(m["services"]) <= set(cat["services"])


def test_department_confirmed_minutes_kept(cat: dict) -> None:
    """Số khoa chốt 12/11: xét nghiệm 30', siêu âm 20', khám 15'."""
    s = cat["services"]
    assert (
        s["KHAM"]["minutes_early"],
        s["SA_2D"]["minutes_early"],
        s["XN_MAU"]["minutes_early"],
    ) == (15, 20, 30)


@pytest.mark.parametrize(
    ("note", "visit", "must_have", "minutes"),
    [
        ("Chậm kinh", "EARLY_PREG", {"SA_DAU_DO", "BHCG"}, 30),
        ("tkss thường", "POSTPARTUM", {"KHAM"}, 15),
        ("Có khí hư trắng", "GYN", {"SOI_DICH"}, 30),
        ("làm hs mổ u buồng trứng/khám gây mê", "PREOP", {"XN_TIEN_PHAU", "KHAM_GAY_ME"}, 60),
        ("Làm hồ sơ LEEP, KH tới 10h", "PROCEDURE", {"THU_THUAT"}, 60),
        ("tk sau hút buồng/phá thai nội khoa", "POST_ABORTION", {"SA_DAU_DO"}, 20),
        ("39w6d/SA/CTG tới 08h30", "ANC", {"SA_2D", "CTG"}, 45),
        ("17w5d", "ANC", {"TRIPLE_TEST"}, 30),
        ("Thai sớm/Chưa loại trừ chuyển dạ/SA trước", "PRETERM_CHECK", {"SA_2D"}, 20),  # chỉ dịch vụ ghi chú
        ("khám thai", "ANC", {"KHAM"}, 15),
        ("khám thai 28w, đã làm NPDN", "ANC", {"KHAM"}, 15),
    ],
)
def test_infer_from_oh_style_notes(
    cat: dict, note: str, visit: str, must_have: set[str], minutes: int
) -> None:
    p = plan(note, cat)
    assert p.visit_type == visit
    assert must_have <= set(p.codes)
    assert plan_minutes(p, cat, 90)[0] == minutes


def test_done_service_not_added(cat: dict) -> None:
    p = plan("khám thai 28w, đã làm NPDN", cat)
    assert "OGTT" not in p.codes and not p.to_confirm


def test_post_abortion_follow_up_is_not_a_procedure(cat: dict) -> None:
    assert "THU_THUAT" not in plan("tk sau hút buồng/phá thai nội khoa", cat).codes


def test_ga_milestones(cat: dict) -> None:
    assert "DOUBLE_TEST" in plan("12w3d", cat).codes
    assert "TRIPLE_TEST" not in plan("17w double", cat).codes  # đã làm Double → không thêm Triple
    assert "GBS" in plan("36w", cat).codes
    assert "CTG" in plan("38w", cat).codes and "CTG" not in plan("33w", cat).codes
    assert "OGTT" not in plan("Chậm kinh 26w", cat).codes  # mốc tuần thai chỉ áp cho khám thai


def test_inferred_fasting_service_needs_confirmation(cat: dict) -> None:
    p = plan("dky gói thai 27w", cat)
    assert p.visit_type == "ANC_PACKAGE" and p.fasting
    assert [x.code for x in p.to_confirm] == ["OGTT"]
    written = plan("27w NPDN", cat)  # ghi chú ghi rõ → không cần xác nhận lại
    assert "OGTT" in written.codes and not written.to_confirm


def test_ai_can_only_add_services(cat: dict) -> None:
    """AI không bỏ được dịch vụ ghi trong ghi chú; AI được thêm dịch vụ trong danh mục."""
    p = plan("39w6d/SA/CTG tới 08h30", cat, ai_visit_type="ANC", ai_services=["KHAM"])
    assert {"SA_2D", "CTG"} <= set(p.codes)
    p2 = plan("khám thai", cat, ai_services=["DOPPLER", "KHONG_CO"])
    assert "DOPPLER" in p2.codes and "KHONG_CO" not in p2.codes


def test_ai_visit_type_does_not_override_specific_rule_type(cat: dict) -> None:
    p = plan("36w5, sa, ctg, kgm đến 8h30", cat, ai_visit_type="ANC")
    assert p.visit_type == "PREOP" and p.visit_from == "rule"
    p2 = plan("thai 20w, bé hơi nhỏ", cat, ai_visit_type="PRETERM_CHECK")
    assert p2.visit_type == "PRETERM_CHECK" and p2.visit_from == "ai"


def test_cap_depends_on_service(cat: dict) -> None:
    assert plan_minutes(plan("tkss thường", cat), cat, 90)[1] == 90
    assert plan_minutes(plan("39w3d đến 8h", cat), cat, 90)[1] == 180


def test_oh_golden_meets_targets() -> None:
    """Bộ vàng ghi chú OH (quy tắc + AI mock) đạt chỉ tiêu config/eval_targets.yaml mục oh_notes."""
    import yaml

    from core.ai.llm_client import MockClient
    from core.config import CONFIG_DIR
    from scripts.eval_oh_notes import run

    res = run(MockClient())
    targets = yaml.safe_load((CONFIG_DIR / "eval_targets.yaml").read_text(encoding="utf-8"))[
        "oh_notes"
    ]
    for t in targets:
        if not t.get("blocking", True):
            continue
        v = res[t["path"]]
        assert (v >= t["value"]) if t["op"] == ">=" else (v == t["value"]), (
            t["label"],
            v,
            res["errors"],
        )


def test_facts_are_verified_quotes(cat: dict) -> None:
    """AI chỉ được dùng câu trích nguyên văn từ trang đã mở kiểm (verified)."""
    for f in cat["facts"]:
        src = cat["sources"][f["source"]]
        assert src.get("verified") and src.get("url"), f
        assert f.get("quote"), f
    from core.ai.note_extractor import knowledge_block

    block = knowledge_block(cat)
    assert "UOC_LUONG" not in block.split("Thông tin từ nguồn")[1]
