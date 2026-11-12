"""Test các lỗ hổng tìm được khi rà soát bảo mật / dữ liệu (2026-11-10)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.ai.llm_client import LlmError, OllamaClient, is_internal_host
from core.ai.masking import mask_text
from core.models import Patient
from core.repo import ga_on, stage_text

NAME, PID = "Phạm Thị Thu", "26001234"


# ---------------------------------------------------------------- che dữ liệu
@pytest.mark.parametrize(
    "text",
    [
        "gọi 84912345678 nhé",  # đầu 84 không có dấu +
        "SĐT +84 912.345.678",
        "zalo 0912-345-678",
        "CCCD 012345678901",
        "cccd 0123 4567 8901",  # CCCD tách nhóm
        "CMND 123456789",  # CMND cũ 9 số
    ],
)
def test_masking_hides_ids(text):
    r = mask_text(text, full_name=NAME, pid=PID, ner=False)
    digits = sum(ch.isdigit() for ch in r.text)
    assert digits <= 4, r.text
    assert r.ok


def test_masking_keeps_clinical_numbers():
    text = "BN 32 tuần, đến lúc 06h30 ngày 12/11/2026, HA 140/90"
    r = mask_text(text, full_name=NAME, pid=PID, ner=False)
    assert r.text == text and r.ok


def test_masking_leftover_long_number_blocks_sending():
    # Dãy số dài lạ (không đúng mẫu SĐT/CCCD) vẫn bị lưới an toàn lần 2 chặn.
    r = mask_text("mã hồ sơ 1234 5678 90123 4", full_name=NAME, pid=PID, ner=False)
    assert not r.ok
    assert all(":" not in x for x in r.leftovers)  # nhãn không chứa giá trị gốc


def test_masking_leftovers_contain_no_values():
    r = mask_text("liên hệ chị Thu", full_name=None, pid=None, ner=lambda t: ["Thu"] if "Thu" in t else [])
    assert r.ok  # NER đã che
    r2 = mask_text("abc", full_name=None, pid=None, ner=lambda t: ["Zed Xy"])
    assert "Zed" not in " ".join(r2.leftovers)


# ---------------------------------------------------------------- tuần thai sau sinh
def _patient(edd: date, delivery: date | None) -> Patient:
    return Patient(pid="1", full_name_enc="", phone_enc="", edd=edd, delivery_date=delivery)


def test_ga_none_after_delivery():
    edd = date(2026, 10, 1)
    p = _patient(edd, delivery=date(2026, 9, 28))
    assert ga_on(p, date(2026, 9, 27)) is not None
    assert ga_on(p, date(2026, 9, 28)) is None
    assert ga_on(p, date(2026, 11, 10)) is None
    assert stage_text(p, date(2026, 10, 3)) == "Hậu sản 5 ngày"


def test_stage_text_pregnant():
    d = date(2026, 11, 10)
    p = _patient(d + timedelta(days=280 - (32 * 7 + 1)), None)
    assert stage_text(p, d) == "32+1"


# ---------------------------------------------------------------- Ollama chỉ chạy nội bộ
@pytest.mark.parametrize("host,ok", [
    ("localhost", True), ("127.0.0.1", True), ("::1", True), ("192.168.1.5", True), ("10.0.0.2", True),
    ("172.16.0.1", True), ("172.31.255.1", True),
    ("172.67.1.1", False),  # Cloudflare – không phải dải nội bộ
    ("8.8.8.8", False), ("ollama.com", False), ("localhost.evil.com", False), ("169.254.1.1", False),
])
def test_internal_host(host, ok):
    assert is_internal_host(host) is ok


def test_ollama_rejects_public_url():
    with pytest.raises(LlmError):
        OllamaClient(model="qwen2.5:3b-instruct", url="http://172.67.1.1:11434")
    OllamaClient(model="qwen2.5:3b-instruct", url="http://[::1]:11434")


# ---------------------------------------------------------------- dữ liệu giả nhất quán
def test_synthetic_forms_match_pregnancy_stage(rules):
    """Mỗi câu trả lời form giả phải đúng giai đoạn: form thai kỳ trước sinh, form hậu sản sau sinh."""
    from pathlib import Path

    import pandas as pd

    root = Path(__file__).resolve().parent.parent
    resp_path = root / "data" / "synthetic" / "form_responses.csv"
    if not resp_path.exists():
        pytest.skip("Chưa sinh dữ liệu giả")
    resp = pd.read_csv(resp_path, dtype=str, keep_default_na=False).set_index("token")
    lab = pd.read_csv(root / "tests" / "golden" / "form_expected.csv", dtype=str, keep_default_na=False)
    pats = pd.read_csv(root / "data" / "synthetic" / "patients.csv", dtype=str, keep_default_na=False).set_index("pid")
    sched = rules["forms"]
    pp_codes = set(sched["postpartum_days"])
    for r in lab.itertuples():
        p = pats.loc[r.pid]
        d = pd.Timestamp(resp.loc[r.token, "submitted_at"]).date()
        delivered = bool(p["delivery_date"]) and d >= date.fromisoformat(p["delivery_date"])
        if delivered:
            assert r.form_code in pp_codes and r.ga_weeks == "", r
        else:
            assert r.form_code not in pp_codes, r
            ga = 280 - (date.fromisoformat(p["edd"]) - d).days
            assert int(r.ga_weeks) == ga // 7
            w = sched["windows"].get(r.form_code)
            if w:
                assert tuple(w["from"]) <= (ga // 7, ga % 7) <= tuple(w["to"]), r


# ---------------------------------------------------------------- tên nước ngoài
@pytest.mark.parametrize("full_name,note,leak", [
    ("Yuki Tanaka", "Chị Yuki xin đến sớm 30p", "Yuki"),        # Nhật: tên gọi trước họ
    ("Emily Smith", "Emily goi lai xac nhan", "Emily"),          # Anh/Mỹ
    ("Camille Dubois", "Mme Dubois đến 8h", "Dubois"),           # chỉ ghi họ
    ("Kim Min Ji", "Chị Min Ji đến 9h", "Min Ji"),               # Hàn: tên gọi hai âm
    ("Wang Xiao Mei", "Xiao Mei can phien dich", "Xiao Mei"),    # Trung
    ("Nguyễn Thị Lan", "chị Lan đến sớm", "Lan"),                # Việt (giữ như cũ)
])
def test_masking_foreign_names(full_name, note, leak):
    r = mask_text(note, full_name=full_name, pid="1", ner=False)
    assert leak not in r.text and "[TÊN]" in r.text and r.ok, r.text


def test_masking_keeps_name_particles():
    # "Thị"/"Văn" không che riêng lẻ để không làm hỏng ghi chú
    r = mask_text("BN tái khám", full_name="Nguyễn Văn Thị", pid="1", ner=False)
    assert r.text == "BN tái khám"


# ---------------------------------------------------------------- tiền tố khách tự đặt
@pytest.mark.parametrize("raw,note,flag", [
    ("Autobooking", "", True),
    ("Autobooking thai moi", "thai moi", True),
    ("AUTOBOOKING: 22w kham va sieu am", "22w kham va sieu am", True),
    ("thai moi ycbs", "thai moi ycbs", False),
    ("Autobooking | Autobooking", "", True),
])
def test_split_self_booking(raw, note, flag):
    from core.ingest.his_reader import split_self_booking

    assert split_self_booking(raw, ("Autobooking",)) == (note, flag)


def test_masking_short_surname_does_not_eat_words():
    r = mask_text("dan do HA hang ngay, ho nhieu", full_name="Đỗ Thị Hồ", pid="1", ner=False)
    assert r.text == "dan do HA hang ngay, ho nhieu"
    r2 = mask_text("chị Đỗ gọi lại", full_name="Đỗ Thị Hồ", pid="1", ner=False)
    assert "Đỗ" not in r2.text
