from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest

from core.ai.llm_client import LlmError, MockClient
from core.ai.masking import mask_text
from core.ai.note_extractor import NOTE_SCHEMA, extract
from core.ingest.his_reader import read_raw
from core.pipeline import process_his

GOLDEN = Path(__file__).parent / "golden"


# ---------------- masking
def test_mask_phone_cccd_pid_email():
    r = mask_text("SĐT 0912345678, CCCD 001203004567, mã 26001234, mail a.b@x.vn", pid="26001234", ner=False)
    assert r.ok
    assert "[SĐT]" in r.text and "[CCCD]" in r.text and "[PID]" in r.text and "[EMAIL]" in r.text
    assert "0912345678" not in r.text and "001203004567" not in r.text


def test_mask_phone_with_spaces():
    r = mask_text("gọi 0912 345 678 nhé", ner=False)
    assert "[SĐT]" in r.text and "678" not in r.text


def test_mask_customer_full_and_given_name():
    r = mask_text("Chị Nguyễn Thị Lan hẹn lại, Lan muốn đến sớm", full_name="Nguyễn Thị Lan", ner=False)
    assert "Lan" not in r.text and r.text.count("[TÊN]") == 2


def test_mask_name_without_diacritics():
    r = mask_text("chi lan goi lai", full_name="Nguyễn Thị Lan", ner=False)
    assert "lan" not in r.text.lower().replace("[tên]", "")


def test_mask_doctor_name():
    r = mask_text("BS Khải dặn tái khám", doctor_names=["BS. Phạm Văn Khải"], ner=False)
    assert "[BS]" in r.text and "Khải" not in r.text


def test_mask_leftover_marks_not_ok():
    # NER giả: luôn thấy một tên mà các bước trước không che được
    r = mask_text("Gặp Hồng ở quầy", ner=lambda t: ["Hồng"] if "Hồng" in t or "[TÊN]" in t else [])
    assert r.text.count("[TÊN]") == 1
    leaky = mask_text("Gặp Hồng ở quầy", ner=lambda t: ["Ai Đó"])
    assert not leaky.ok


def test_mask_with_underthesea_ner():
    pytest.importorskip("underthesea")
    r = mask_text("Gặp chị Nguyễn Thị Mai ở quầy, ĐTĐTK")
    assert "Mai" not in r.text and "ĐTĐTK" in r.text


# ---------------- note extractor
def test_extract_with_mock_golden(rules):
    client = MockClient()
    e = extract("BS hẹn đến 7h30 lấy máu", ["Xét nghiệm máu"], time(8, 0), client, rules)
    assert e.ai and e.ai.explicit_arrival_time == time(7, 30)
    assert e.confidence == 1.0
    assert e.prompt_version.startswith("note_extractor")
    assert client.calls == 5  # có từ khóa thời gian → 5 lần


def test_extract_economy_single_call(rules):
    client = MockClient()
    e = extract("Song thai, theo dõi sát", ["Khám thai"], time(8, 0), client, rules)
    assert client.calls == 1 and e.ai and e.ai.risk_flags == ["TWINS"]


def test_extract_empty_note_no_call(rules):
    client = MockClient()
    e = extract("", ["Khám thai"], time(8, 0), client, rules)
    assert not e.attempted and client.calls == 0


def test_self_consistency_low_confidence(rules):
    answers = iter(["07:30", "07:30", "07:00", "08:00", None])

    def responder(prompt, schema, seed):
        t = next(answers)
        return {"explicit_arrival_time": t, "minutes_early": None, "needs_fasting": False, "ga_weeks": None,
                "ga_days": None, "risk_flags": [], "asks_confirmation": False, "evidence": "x"}

    e = extract("đến 7h30?", ["Khám thai"], time(8, 0), MockClient(responder), rules)
    assert e.ai and e.ai.explicit_arrival_time == time(7, 30)
    assert e.confidence == pytest.approx(0.4)


def test_schema_violation_counts_as_error(rules):
    def bad(prompt, schema, seed):
        return {"explicit_arrival_time": "7h30"}

    e = extract("đến 7h30", ["Khám thai"], time(8, 0), MockClient(bad), rules)
    assert e.ai is None and e.attempted and e.confidence == 0.0


def test_mock_validates_schema():
    with pytest.raises(LlmError):
        MockClient(lambda p, s, seed: {"x": 1}).generate_json("p", NOTE_SCHEMA, 0.0, None)


def test_pii_left_not_sent_to_llm(rules, his_cfg):
    from core.pipeline import plan_appointment
    from core.rules.merge import MergedAppt

    m = MergedAppt(pid="P9", appt_date=date(2026, 11, 10), start_time=time(9, 0), end_time=None,
                   services=["Khám thai"], note="hỏi ông Quang", full_name="Trần Thị Hoa", phone="0900123456",
                   phone_raw="0900123456", nationality="Việt Nam", department="x", doctor="",
                   booking_source="", booked_at=None)
    client = MockClient()
    p = plan_appointment(m, rules, client, [], ner=lambda t: ["Quang"] if "Quang" in t else ["Ai Đó"])
    assert client.calls == 0
    assert "PII_LEFT" in p.decision.reasons


def _golden_days() -> list[str]:
    return sorted(p.stem.replace("his_day_", "") for p in GOLDEN.glob("his_day_*.xlsx"))


@pytest.mark.parametrize("day", _golden_days())
def test_golden_with_mock_ai(day, his_cfg, rules):
    raw = read_raw(GOLDEN / f"his_day_{day}.xlsx")
    res = process_his(raw, his_cfg, rules, date.fromisoformat(day), client=MockClient(), ner=False)
    exp = pd.read_csv(GOLDEN / f"expected_arrivals_{day}.csv", dtype=str, keep_default_na=False)
    got = {p.merged.pid: p for p in res.planned}
    for _, e in exp.iterrows():
        p = got[e["pid"]]
        from core.rules.arrival import arrival_text

        assert arrival_text(p.arrival, rules["arrival"]) == e["arrival_true"], (e.to_dict(), p.arrival)
        assert ",".join(p.decision.reasons) == e["reasons_ai"], (e.to_dict(), p.decision.reasons)
    assert res.auto_rate >= 0.85


# ---------------------------------------------------------------- Luật 5: chặn mô hình cloud của Ollama
def test_ollama_rejects_cloud_model_name() -> None:
    import pytest

    from core.ai.llm_client import LlmError, OllamaClient

    with pytest.raises(LlmError):
        OllamaClient(model="gpt-oss:120b-cloud")


def test_ollama_rejects_remote_host_tag(monkeypatch) -> None:
    import pytest

    from core.ai.llm_client import LlmError, OllamaClient

    c = OllamaClient(model="qwen2.5:7b-instruct")
    monkeypatch.setattr(c, "_tags", lambda: {"qwen2.5:7b-instruct": {"remote_host": "https://ollama.com:443"}})
    assert c.available() is False

    def boom(*a, **k):  # không được gọi mạng khi mô hình không cục bộ
        raise AssertionError("đã gọi generate")

    monkeypatch.setattr("core.ai.llm_client.requests.post", boom)
    with pytest.raises(LlmError):
        c.generate_json("x", {"type": "object"}, 0.0, None)


def test_ollama_rejects_non_local_url() -> None:
    import pytest

    from core.ai.llm_client import LlmError, OllamaClient

    with pytest.raises(LlmError):
        OllamaClient(model="qwen2.5:7b-instruct", url="https://ollama.com")


def test_cloud_allowed_only_with_eval_flag(monkeypatch) -> None:
    from core.ai.llm_client import OllamaClient, make_client
    from core.config import load_rules

    c = OllamaClient(model="gpt-oss:120b-cloud", allow_cloud_eval=True)
    monkeypatch.setattr(c, "_tags", lambda: {"gpt-oss:120b-cloud": {"remote_host": "https://ollama.com:443"}})
    assert c.available() is True
    # make_client (đường app dùng) không bao giờ bật cờ này
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    client = make_client(load_rules())
    assert getattr(client, "allow_cloud_eval", False) is False


# ---------------------------------------------------------------- AI không được chép giờ hẹn / bịa giờ đến
def test_time_in_note_grounding() -> None:
    from core.rules.note_patterns import time_in_note

    assert time_in_note("11:45", "Hẹn đến 11h45 làm NPDN")
    assert not time_in_note("12:15", "Hẹn đến 11h45 làm NPDN")
    assert time_in_note("15:30", "den 15g30 nhin an lay mau")
    assert time_in_note("15:00", "đến lúc 3h chiều")  # quy đổi chiều
    assert not time_in_note("09:00", "Thai 23 w, tái khám")


def test_copied_appt_time_vote_is_discarded(rules) -> None:
    from core.ai.llm_client import MockClient
    from core.ai.note_extractor import extract

    base = {"explicit_arrival_time": None, "minutes_early": None, "needs_fasting": True, "ga_weeks": None,
            "ga_days": None, "risk_flags": [], "asks_confirmation": False, "evidence": "x"}
    answers = iter(["12:15", "11:45", "12:15", "11:45", "11:45"])

    def responder(prompt, schema, seed):
        return {**base, "explicit_arrival_time": next(answers)}

    e = extract("Hẹn đến 11h45 làm NPDN", ["Khám thai"], time(12, 15), MockClient(responder), rules)
    assert e.ai is not None and e.ai.explicit_arrival_time == time(11, 45)
    assert e.confidence == pytest.approx(3 / 5)  # 2 phiếu chép giờ hẹn bị loại → độ tin cậy giảm
