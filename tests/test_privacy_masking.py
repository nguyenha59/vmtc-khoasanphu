"""Che dữ liệu cá nhân trước khi gửi AI (bổ sung 12/11/2026) và phát hiện ghi chú nhạy cảm. Toàn dữ liệu BỊA."""

from __future__ import annotations

from datetime import date, time

import pytest

from core.ai.masking import mask_text
from core.rules.merge import MergedAppt
from core.rules.sensitive import find_sensitive

NAME, PID = "Nguyễn Thị Lan Anh", "26000482"


def m(text: str):
    return mask_text(text, full_name=NAME, pid=PID, doctor_names=["BS. Phạm Văn Khải"], ner=False)


@pytest.mark.parametrize(
    "text, secret",
    [
        ("lh (+84) 912-345-678", "912"),
        ("BHYT DN4010123456789", "4010123456789"),
        ("hộ chiếu C1234567", "C1234567"),
        ("passport no. B9876543", "B9876543"),
        ("mã BN 26001234 là mẹ của khách", "26001234"),
        ("sinh ngày 12/03/1995", "1995"),
        ("fb.com/lananh.nguyen", "lananh"),
        ("nhà số 12 ngõ 34 Láng Hạ, Đống Đa", "Láng"),
        ("ở 45 Nguyễn Trãi, P.Thượng Đình, Q.Thanh Xuân", "Thanh Xuân"),
        ("chong ten hung, sdt sau", "hung"),
        ("chồng tên Hùng đi cùng", "Hùng"),
    ],
)
def test_pii_masked(text, secret):
    r = m(text)
    assert secret not in r.text and r.ok, r.text


@pytest.mark.parametrize(
    "text",
    ["đường huyết đói 5.6", "tiểu đường thai kỳ", "HPV duong tinh 16", "quan trọng: nhịn ăn", "duong huyet cao",
     "ở nhà nghỉ ngơi", "HA 140/90", "kết quả XN ngày 05/11/2026", "TK 20/11", "thai 33+2", "khám lúc 7h30"],
)
def test_medical_text_not_masked(text):
    assert m(text).text == text


def test_name_keeps_following_words():
    assert m("chồng tên Hùng đi cùng").text == "chồng tên [TÊN] đi cùng"


TOPICS = [{"label": "Bệnh lây truyền", "keywords": ["HIV", "giang mai"]},
          {"label": "Yêu cầu giữ kín", "keywords": ["không muốn chồng biết"]}]


def test_find_sensitive():
    assert find_sensitive("HIV duong tinh, khong muon chong biet", TOPICS) == ["Bệnh lây truyền", "Yêu cầu giữ kín"]
    assert find_sensitive("thai 20w kham dinh ky", TOPICS) == []


def _appt(note: str) -> MergedAppt:
    return MergedAppt(pid="1", appt_date=date(2026, 11, 13), start_time=time(9, 0), end_time=None,
                      services=["Khám thai"], note=note, full_name="A", phone="0900000001", phone_raw="",
                      nationality="Việt Nam", department="", doctor="", booking_source="", booked_at=None)


def test_sensitive_note_not_sent_to_external_api(rules):
    from core.pipeline import plan_appointment

    calls: list[str] = []

    class FakeOpenAI:
        name = "openai:test"

        def generate_json(self, prompt, schema, temperature=0.0, seed=None):  # noqa: ANN001
            calls.append(prompt)
            raise RuntimeError("không được gọi")

    p = plan_appointment(_appt("song thai, HIV dương tính, giữ bí mật"), rules, FakeOpenAI(), [], ner=False)
    assert not calls
    assert any("nhạy cảm" in d and "API bên ngoài" in d for d in p.arrival.details)


def test_sensitive_note_flagged_without_ai(rules):
    from core.pipeline import plan_appointment

    p = plan_appointment(_appt("TK sau đình chỉ thai"), rules, None, [], ner=False)
    assert any("Đình chỉ / phá thai" in d for d in p.arrival.details)


# ---------------------------------------------------------------- thuật ngữ y khoa trùng tên người (12/11/2026)
@pytest.mark.parametrize("note,name,term", [
    ("HPV duong tinh 16, ctc den 9g15", "Trần Thị Dương", "duong tinh"),
    ("HPV dương tính 16", "Lê Dương", "dương tính"),
    ("đường huyết đói 5.6", "Nguyễn Văn Đường", "đường huyết"),
    ("hong cau thap", "Phạm Thị Hồng", "hong cau"),
])
def test_medical_terms_not_masked_as_name(note, name, term):
    r = mask_text(note, full_name=name, ner=False)
    assert term in r.text and "[TÊN]" not in r.text and r.ok


def test_name_alone_still_masked_next_to_medical_term():
    r = mask_text("Chị Dương xét nghiệm HPV dương tính", full_name="Trần Thị Dương", ner=False)
    assert r.text.startswith("Chị [TÊN]") and "dương tính" in r.text and r.ok
