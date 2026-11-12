"""Bảng chỉ tiêu (config/eval_targets.yaml) và bỏ phiếu đa số khi đọc form."""

from __future__ import annotations

from core.ai.form_reader import _majority_merge
from core.ai.scorecard import blocking_failures, load_targets, scorecard


def _f(concept, present=True, experiencer="patient", temporality="current"):
    return {"concept": concept, "present": present, "experiencer": experiencer, "temporality": temporality,
            "severity": "unknown", "evidence": ""}


def test_targets_file_has_red_sensitivity_blocking():
    t = load_targets()
    forms = {x["path"]: x for x in t["forms"]}
    assert forms["rules_plus_ai.red_sensitivity"]["blocking"] and forms["rules_plus_ai.red_sensitivity"]["value"] == 1.0
    assert forms["redteam.red_sensitivity"]["blocking"]


def test_scorecard_pass_fail_and_missing():
    t = {"forms": [
        {"path": "a.sens", "label": "s", "op": "==", "value": 1.0, "blocking": True},
        {"path": "a.fa", "label": "f", "op": "<=", "value": 0.10, "blocking": False},
        {"path": "a.missing", "label": "m", "op": ">=", "value": 0.8, "blocking": True},
    ]}
    checks = scorecard("forms", {"a": {"sens": 1.0, "fa": 0.2}}, t)
    assert [c.passed for c in checks] == [True, False, None]
    # chỉ tiêu bắt buộc chưa đo được = không đạt (an toàn)
    assert [c.label for c in blocking_failures(checks)] == ["m"]


def test_scorecard_ref_comparison():
    t = {"noshow": [{"path": "lgb.auc", "label": "x", "op": ">", "ref": "lr.auc", "blocking": False}]}
    assert scorecard("noshow", {"lgb": {"auc": 0.8}, "lr": {"auc": 0.7}}, t)[0].passed is True
    assert scorecard("noshow", {"lgb": {"auc": 0.6}, "lr": {"auc": 0.7}}, t)[0].passed is False


def test_majority_drops_single_hallucination():
    runs = [{}, {"HYPEREMESIS": _f("HYPEREMESIS")}, {"HYPEREMESIS": _f("HYPEREMESIS", present=False)}]
    out = _majority_merge(runs)
    assert out["HYPEREMESIS"]["present"] is False


def test_majority_keeps_two_of_three():
    runs = [{"BLEEDING": _f("BLEEDING")}, {"BLEEDING": _f("BLEEDING")}, {}]
    assert _majority_merge(runs)["BLEEDING"]["present"] is True


def test_majority_other_person():
    runs = [{"FEVER": _f("FEVER", experiencer="other")}, {"FEVER": _f("FEVER")},
            {"FEVER": _f("FEVER", experiencer="other")}]
    assert _majority_merge(runs)["FEVER"]["experiencer"] == "other"


def test_minority_red_still_yellow_via_confidence(rules):
    """1/3 lần chạy thấy triệu chứng đỏ → không mất hẳn: độ tin cậy thấp → combine tối thiểu VÀNG."""
    from core.ai.form_reader import read_free_text
    from core.ai.llm_client import MockClient
    from core.assess import combine
    from core.levels import Level

    calls = {"n": 0}

    def responder(prompt, schema, seed):
        calls["n"] += 1
        f = [{"concept": "VISUAL", "present": True, "temporality": "current", "experiencer": "patient",
              "severity": "unknown", "evidence": "xyz"}] if calls["n"] == 1 else []
        return {"findings": f, "unmapped_concerns": []}

    r = read_free_text("em thấy là lạ trong người", 30, MockClient(responder), rules, ner=False)
    got = combine(Level.GREEN, Level.GREEN, r.level, ai_ok=r.ok, ai_confidence=r.confidence,
                  min_confidence=rules["arrival"]["ai_confidence_min"]).level
    assert got >= Level.YELLOW


# ---------------------------------------------------------------- luật người trải qua / thời gian
def test_other_person_vs_self_reference(rules):
    from core.ai.form_reader import lexicon_findings

    def eff(text):
        return {f["concept"]: f["experiencer"] for f in lexicon_findings(text, rules=rules)}

    assert eff("chồng em bị sốt mấy hôm")["FEVER"] == "other"
    assert eff("con gái em sốt cao")["FEVER"] == "other"
    assert eff("ông ngoại bị nôn nhiều")["HYPEREMESIS"] == "other"
    # người khác kể lại triệu chứng của chính thai phụ → patient (không được hạ thành người khác)
    assert eff("chồng em thấy em thở hụt hơi")["DYSPNEA"] == "patient"


def test_past_with_resolution_in_next_clause(rules):
    from core.ai.form_reader import lexicon_findings

    f = {x["concept"]: x for x in lexicon_findings("hôm kia bị nhìn mờ, giờ hết rồi", rules=rules)}
    assert f["VISUAL"]["temporality"] == "past_resolved"


# ---------------------------------------------------------------- mối lo ngoài danh mục
def test_unmapped_filters():
    from core.ai.form_reader import _vote_unmapped

    # chép ví dụ trong prompt / lời giải thích → không bám văn bản → loại
    assert _vote_unmapped([["Bà ngoại bị tiểu đường"]] * 3, "con lớn bị sốt") == []
    assert _vote_unmapped([["Bài ghi này không liên quan đến triệu chứng"]] * 3, "cảm ơn bác sĩ") == []
    # phủ định, người thân → loại
    assert _vote_unmapped([["chưa thấy đau gì"]] * 3, "chưa thấy đau gì") == []
    assert _vote_unmapped([["mẹ chồng bị cao huyết áp"]] * 3, "mẹ chồng bị cao huyết áp") == []
    # thiểu số → loại
    assert _vote_unmapped([["hôm nay trời mưa to quá"], [], []], "hôm nay trời mưa to quá") == []
    # mối lo thật của thai phụ, đa số → giữ (→ vàng)
    assert _vote_unmapped([["đau lưng nhiều"], ["em bị đau lưng nhiều"], []], "em bị đau lưng nhiều, khó ngủ")


def test_lexicon_allows_degree_words(rules):
    from core.ai.form_reader import lexicon_findings

    found = {f["concept"] for f in lexicon_findings("chiều nay hoa mắt, mặt hơi sưng", rules=rules)}
    assert {"VISUAL", "FACE_EDEMA"} <= found
    # không khớp lan: "mặt" và "sưng" cách nhau nhiều từ thì không tính
    assert "FACE_EDEMA" not in {f["concept"] for f in lexicon_findings("mặt em hôm qua đi chợ chân sưng", rules=rules)}
