"""Chọn + xếp câu hỏi cá nhân hóa (docs/13 mục 5c): AI chấm điểm nhưng không bao giờ bỏ được câu bắt buộc."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from core.ai.llm_client import LlmError, MockClient
from core.ai.question_selector import ai_scores, combine_runs, selector_client
from core.config import load_forms, load_rules
from core.rules.personalize import compose_merged
from core.rules.question_select import Context, candidates, select_questions

FORMS = load_forms()
RULES = load_rules()
TWINS30 = Context(ga=(30, 0), flags=["TWINS"], confirmed=["P-SINHNON"])


def codes(chosen) -> list[str]:  # type: ignore[no-untyped-def]
    return [c.code for c in chosen]


def test_mandatory_first_then_condition_questions() -> None:
    got = select_questions(["F5"], TWINS30, RULES, FORMS)
    base = compose_merged(["F5"], ["P-SINHNON"], (30, 0), RULES, FORMS)
    assert set(base) <= set(codes(got))
    groups = [c.group for c in got]
    assert groups == sorted(groups)  # xếp theo khối
    assert got[0].group == 2 and got[0].code in {"C9", "C3", "C1"}  # dấu hiệu theo tình trạng song thai lên đầu
    assert all(c.reasons for c in got)  # câu nào cũng có lý do


def test_carry_over_and_free_text_concept_go_first() -> None:
    ctx = Context(ga=(30, 0), flags=[], confirmed=[], flagged_answers={"ITCH": "Không chắc"},
                  recent_concepts=["DYSPNEA"])
    got = select_questions(["F5"], ctx, RULES, FORMS)
    first = [c for c in got if c.group == 1]
    assert {c.code for c in first} == {"ITCH", "DYSPNEA"}  # DYSPNEA không thuộc F5 nhưng vẫn được hỏi lại
    assert any("Không chắc" in r for c in first for r in c.reasons)


def test_candidates_respect_ga_module_and_quality() -> None:
    c30 = candidates([], Context(ga=(30, 0), flags=[], confirmed=[]), FORMS)
    c10 = candidates([], Context(ga=(10, 0), flags=[], confirmed=[]), FORMS)
    assert "C8" in c30 and "C8" not in c10  # thai máy từ 24 tuần
    assert "NAUSEA" not in c30  # nghén: chỉ đầu thai kỳ (và câu này còn mơ hồ)
    assert not {"BP_AM_SYS", "GLU_FAST", "HYPO"} & set(c30)  # câu số đo chỉ khi đã vào module
    assert "C2" not in c30 and "C6" not in c30  # bị cổng chất lượng loại


def test_ai_can_add_optional_but_never_remove_mandatory() -> None:
    base = set(compose_merged(["F2"], [], (15, 0), RULES, FORMS))
    ctx = Context(ga=(15, 0), flags=[], confirmed=[])
    zero_ai = {c: (0.0, "") for c in FORMS.questions}
    got = codes(select_questions(["F2"], ctx, RULES, FORMS, zero_ai))
    assert base <= set(got)  # AI chấm 0 cũng không bỏ được câu bắt buộc
    boost = {"DYSPNEA": (0.95, "Khó thở cần hỏi")}
    got2 = select_questions(["F2"], ctx, RULES, FORMS, boost)
    assert "DYSPNEA" in codes(got2) and any("AI:" in r for c in got2 if c.code == "DYSPNEA" for r in c.reasons)


@settings(max_examples=120, deadline=None)
@given(st.dictionaries(st.sampled_from(sorted(FORMS.questions)), st.floats(0, 1), max_size=25),
       st.sampled_from(["F1", "F3", "F4", "F5", "F6"]), st.integers(8, 36))
def test_property_ai_never_drops_mandatory_or_breaks_cap(ai: dict[str, float], form: str, weeks: int) -> None:
    ctx = Context(ga=(weeks, 0), flags=["TWINS", "HTN"], confirmed=["P-SINHNON"])
    base = compose_merged([form], ctx.confirmed, ctx.ga, RULES, FORMS)
    got = select_questions([form], ctx, RULES, FORMS, {k: (v, "") for k, v in ai.items()})
    got_codes = codes(got)
    assert set(base) <= set(got_codes)
    assert len(got_codes) == len(set(got_codes))
    cap = RULES["personalization"]["selection"]["max_questions"]
    assert len([c for c in got if not c.mandatory]) <= max(0, cap - len(base))


def test_combine_runs_drops_unstable_and_invented_codes() -> None:
    outs = [{"scores": [{"code": "C8", "score": 0.9}, {"code": "C4", "score": 0.1}, {"code": "BIA", "score": 1}],
             "confidence": 0.8},
            {"scores": [{"code": "C8", "score": 0.8}, {"code": "C4", "score": 0.9}], "confidence": 0.7},
            {"scores": [{"code": "C8", "score": 0.85}, {"code": "C4", "score": 0.5}], "confidence": 0.9}]
    scores, unstable, conf = combine_runs(outs, {"C8", "C4"}, 0.3)
    assert scores["C8"][0] == 0.85 and "C4" in unstable and "BIA" not in scores and conf == 0.8


def test_ai_scores_masks_payload_and_falls_back() -> None:
    sent: list[str] = []

    def spy(prompt: str, schema: dict, seed: int | None) -> dict:
        sent.append(prompt)
        return {"scores": [{"code": "C8", "score": 0.9, "reason": "x"}], "confidence": 0.9}

    ctx = Context(ga=(30, 0), flags=["TWINS"], confirmed=[], flagged_answers={"C1": "Có"})
    r = ai_scores(["F5"], ctx, RULES, FORMS, MockClient(spy))
    assert r.ok and r.scores["C8"][0] == 0.9 and len(sent) == RULES["personalization"]["selection"]["ai_runs"]
    assert r.model and r.prompt_version and r.raw["runs"]  # lưu đủ để truy vết
    assert "R0" not in sent[0] and "09" not in sent[0]  # không mã thai phụ / số điện thoại

    low = ai_scores(["F5"], ctx, RULES, FORMS, MockClient(lambda p, s, d: {"scores": [], "confidence": 0.2}))
    assert not low.ok and "kém tự tin" in (low.error or "")

    def boom(prompt: str, schema: dict, seed: int | None) -> dict:
        raise LlmError("hết ngân sách")

    assert not ai_scores(["F5"], ctx, RULES, FORMS, MockClient(boom)).ok


def test_selector_client_respects_rule5(monkeypatch) -> None:
    monkeypatch.setenv("QUESTION_SELECTOR_PROVIDER", "openai")
    monkeypatch.delenv("SYNTHETIC_DATA_ONLY", raising=False)
    assert selector_client(RULES) is None  # dữ liệu không được xác nhận là GIẢ → không gửi ra ngoài
    monkeypatch.setenv("SYNTHETIC_DATA_ONLY", "1")
    monkeypatch.setenv("APP_ENV", "production")
    assert selector_client(RULES) is None  # production → chặn
    monkeypatch.setenv("QUESTION_SELECTOR_PROVIDER", "rules")
    monkeypatch.delenv("APP_ENV")
    assert selector_client(RULES) is None


def test_snapshot_is_stored_and_reused(db, rules) -> None:
    from datetime import date, timedelta

    from core.forms import create_token, selection_for, snapshot_selection
    from core.models import QuestionSelection
    from core.repo import upsert_patient

    with db.session_scope() as s:
        p = upsert_patient(s, "P300", "Lê Thị Hoa", "0900000001", "Việt Nam")
        p.consent_status, p.risk_groups = "granted", ["P-SINHNON"]
        p.edd = date.today() + timedelta(days=280 - 30 * 7)
        s.flush()
        tok = create_token(s, p.id, "F5", 3)
        s.flush()
        row = snapshot_selection(s, p, tok, ["F5"], rules, FORMS, MockClient())
        s.flush()
        stored = s.get(QuestionSelection, tok)
        assert stored is row and stored.rules_version == rules.rules_version and stored.method == "ai"
        assert stored.model == "mock" and stored.prompt_version and stored.confidence is not None
        assert selection_for(s, tok) == stored.questions
