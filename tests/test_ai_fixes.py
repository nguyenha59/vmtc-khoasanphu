"""Lỗi tìm thấy khi rà soát thuật toán AI (11/11/2026) – dữ liệu giả."""

from __future__ import annotations

from datetime import date, time

import pytest

from core.ai.combine import combine
from core.ai.form_reader import lexicon_findings, read_free_text
from core.ai.llm_client import MockClient
from core.levels import Level


def _llm_patient(concept: str, evidence: str) -> MockClient:
    """LLM giả đọc ĐÚNG: triệu chứng của chính thai phụ, hiện tại."""

    def r(prompt, schema, seed):
        return {"findings": [{"concept": concept, "present": True, "temporality": "current", "experiencer": "patient",
                              "severity": "unknown", "evidence": evidence}], "unmapped_concerns": []}

    return MockClient(responder=r)


def _final(text: str, client, rules) -> str:
    rd = read_free_text(text, 30, client, rules, ner=False)
    return combine(Level.GREEN, Level.GREEN, rd.level, ai_ok=rd.ok, ai_confidence=rd.confidence,
                   min_confidence=rules["arrival"]["ai_confidence_min"]).level.code


# ------------------------------------------------------------------ #1 câu bỏ chủ ngữ không được bị hạ đỏ
SUBJECT_OMITTED = [
    "Chồng đưa đi khám vì ra máu",
    "Bố mẹ lo lắm vì ra máu nhiều",
    "Chồng đi làm từ sáng mà ra máu nhiều",
    "Mẹ chồng bảo nằm nghỉ nhưng vẫn ra máu",
]


@pytest.mark.parametrize("text", SUBJECT_OMITTED)
def test_subject_omitted_is_patient_and_stays_red(rules, text):
    """Trước đây: luật 'người khác bị' hiểu nhầm → phương án A hạ đỏ→vàng dù LLM đọc đúng."""
    lex = {f["concept"]: f for f in lexicon_findings(text, rules=rules)}
    assert lex["BLEEDING"]["experiencer"] == "patient"
    assert _final(text, _llm_patient("BLEEDING", "ra máu"), rules) == "red"


@pytest.mark.parametrize("text,concept", [
    ("chồng em khó thở do hen", "DYSPNEA"),
    ("bố em bị đau đầu suốt", "HEADACHE"),
    ("mẹ em hay bị hoa mắt", "VISUAL"),
    ("chị gái em bị ra máu khi mang thai", "BLEEDING"),
    ("hàng xóm vỡ ối phải đi viện", "FLUID"),
])
def test_other_person_directly_before_symptom_still_other(rules, text, concept):
    lex = {f["concept"]: f for f in lexicon_findings(text, rules=rules)}
    assert lex[concept]["experiencer"] == "other"


def test_patient_reported_by_other_is_patient(rules):
    lex = {f["concept"]: f for f in lexicon_findings("chồng em thấy em thở hụt hơi", rules=rules)}
    assert lex["DYSPNEA"]["experiencer"] == "patient"


# ------------------------------------------------------------------ phủ định phải đứng ngay trước triệu chứng
@pytest.mark.parametrize("text,present", [
    ("không ra máu", False),
    ("không bị ra máu", False),
    ("chưa thấy ra máu", False),
    ("em không hề bị ra máu", False),
    ("hết ra máu rồi", False),
    ("ko ngủ dc ra máu", True),  # trước đây bị hiểu là "không ra máu"
    ("chưa đi khám mà ra máu", True),
])
def test_negation_only_when_adjacent(rules, text, present):
    lex = {f["concept"]: f for f in lexicon_findings(text, rules=rules)}
    assert lex["BLEEDING"]["present"] is present


# ------------------------------------------------------------------ #2 bỏ hẹn: chỉ gán nhãn ngày đã nhập danh sách khách đến
@pytest.fixture()
def three_days(db):
    from core.models import Appointment, Arrival, Patient
    from core.repo import upsert_patient

    days = [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]
    with db.session_scope() as s:
        for i in range(3):
            upsert_patient(s, f"P{i}", "Thai Phụ Giả", f"090000000{i}", "Việt Nam")
        s.flush()
        pts = {p.pid: p for p in s.query(Patient)}
        for d in days:
            for p in pts.values():
                s.add(Appointment(patient_id=p.id, appt_date=d, start_time=time(9, 0), services=["Khám thai"]))
        for pid_ in pts:  # chỉ nhập danh sách khách đến ngày cuối – mọi người đều đến
            s.add(Arrival(pid=pid_, visit_date=days[-1]))
    return days


def test_noshow_labels_only_imported_days(db, three_days):
    from core.ai.noshow import load_visits

    with db.session_scope() as s:
        df = load_visits(s)
    by_day = df.groupby("appt_date")["label"]
    assert by_day.count()[three_days[0]] == 0 and by_day.count()[three_days[1]] == 0  # chưa nhập → không nhãn
    assert by_day.sum()[three_days[2]] == 0  # ngày đã nhập: không ai bỏ hẹn


def test_reinvite_empty_when_arrivals_not_imported(db, rules, three_days):
    from core.ai.noshow import arrivals_imported, reinvite_list

    with db.session_scope() as s:
        assert reinvite_list(s, three_days[1], rules) == []
        assert not arrivals_imported(s, three_days[1]) and arrivals_imported(s, three_days[2])
