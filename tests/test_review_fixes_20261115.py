"""Test cho các bug của lượt rà soát code 15/11/2026 (mỗi test = một tình huống gây lỗi trước khi sửa)."""

from __future__ import annotations

import unicodedata

from core.ai.clinical_facts import blood_pressure_facts
from core.ai.form_reader import read_free_text
from core.ai.llm_client import MockClient
from core.assess import assess_answers
from core.config import load_forms
from core.red_basis import basis_codes


def _llm(concept: str, evidence: str) -> MockClient:
    return MockClient(lambda *_: {"findings": [{"concept": concept, "present": True, "experiencer": "patient",
                                                "temporality": "current", "severity": "unknown",
                                                "evidence": evidence}], "unmapped_concerns": []})


# ---------------------------------------------------------------- 1. lần nhắc khẳng định thắng lần "đọc trên mạng"
def test_affirmed_mention_beats_earlier_info_mention(rules):
    text = "Em đọc trên mạng thấy ra máu là nguy hiểm, giờ em đang ra máu"
    assert read_free_text(text, 30, None, rules, ner=False).level.code == "red"
    assert read_free_text(text, 30, _llm("BLEEDING", "giờ em đang ra máu"), rules, ner=False).level.code == "red"


# ---------------------------------------------------------------- 2. ngày tháng không phải huyết áp
def test_date_after_label_is_not_bp(rules):
    facts = blood_pressure_facts("Huyết áp hôm 14/11 là 160/110")
    assert [(f.systolic, f.diastolic) for f in facts] == [(160, 110)]
    res = assess_answers("F5", {}, "Huyết áp hôm 14/11 là 160/110", 30, rules, load_forms(), client=None,
                         history={}, ner=False)
    assert res.rule.level.code == "red" and not res.rule.implausible


def test_low_blood_sugar_is_not_bp_label():
    assert blood_pressure_facts("em bị hạ đường huyết hôm 12/11") == []
    assert [(f.systolic, f.diastolic) for f in blood_pressure_facts("HA 150/100 sáng nay")] == [(150, 100)]


# ---------------------------------------------------------------- 4. chữ tổ hợp NFD (iPhone / Mac)
def test_nfd_text_keeps_subject_scope():
    text = unicodedata.normalize("NFD", "Em đo huyết áp 165/112 sáng nay, mẹ em huyết áp 120/80")
    facts = blood_pressure_facts(text)
    assert [(f.systolic, f.experiencer, f.usable) for f in facts] == [(165, "patient", True), (120, "other", False)]


# ---------------------------------------------------------------- 3. phủ định của danh mục không xóa cụm khác của AI
def test_lexicon_negation_does_not_erase_llm_finding_on_other_phrase(rules):
    r = read_free_text("Em không ra máu, nhưng quần lót có vệt nâu", 30, _llm("BLEEDING", "quần lót có vệt nâu"),
                       rules, ner=False)
    assert r.level.code == "red"


def test_lexicon_other_site_still_overrides_same_phrase(rules):
    r = read_free_text("tối qua em bị chảy máu cam", 30, _llm("BLEEDING", "chảy máu cam"), rules, ner=False)
    assert r.level.code == "green"


# ---------------------------------------------------------------- 8. nguồn đọc từ cấu hình
def test_basis_for_bp_combo_and_unsure_comes_from_config(rules):
    assert basis_codes("rule", "TXT_HEADACHE_BP", rules) == list(rules["red_basis"]["rules"]["TXT_BP"])
    assert basis_codes("rule", "UNSURE", rules) == list(rules["red_basis"]["rules"]["UNSURE"])


# ---------------------------------------------------------------- 9. lưu model thật
def test_stored_model_is_actual_provider(rules):
    r = read_free_text("em nhìn mờ", 30, _llm("VISUAL", "em nhìn mờ"), rules, ner=False)
    assert r.model and not r.model.startswith("hybrid:")


# ---------------------------------------------------------------- 6. gỡ bộ câu không bị tự thêm lại; xem không ghi
def test_declined_pack_is_not_reapplied(rules, db):
    from datetime import date, datetime

    from core.models import Patient
    from core.patients import auto_apply_for, declined_codes, record_plan_declines

    with db.session_scope() as s:
        p = Patient(pid="T1", code="R9001", full_name_enc="x", phone_enc="x", consent_status="granted",
                    risk_groups=["P-TSG"], proposed_risk_flags=[], edd=date(2027, 1, 20))
        s.add(p)
        s.flush()
        record_plan_declines(s, p, ["P-TSG"], [], None, datetime(2026, 11, 15, 9))
        p.risk_groups = []
        assert "P-TSG" in declined_codes(s, p.id, p.edd)
        assert "P-TSG" not in auto_apply_for(s, p, date(2026, 11, 15), rules, load_forms())
        record_plan_declines(s, p, [], ["P-TSG"], None, datetime(2026, 11, 15, 10))
        assert "P-TSG" not in declined_codes(s, p.id, p.edd)


# ---------------------------------------------------------------- lượt rà soát 2
import pytest  # noqa: E402


@pytest.mark.parametrize("text, pair", [
    ("đau đầu, uống thuốc rồi HA xuống còn 150/100", (150, 100)),   # "HA xuống" là nhãn huyết áp
    ("HA thấp 85/50 em hơi choáng", (85, 50)),
    ("huyết áp hôm 14/11/26 là 160/110", (160, 110)),               # năm 2 chữ số
    ("huyết áp hôm 14.11 là 160/110", (160, 110)),                  # ngày dùng dấu chấm
    ("huyết áp em đo sau khi nghỉ ngơi mười lăm phút ở nhà buổi sáng là 165/110", (165, 110)),
])
def test_bp_label_and_date_edge_cases(text, pair):
    facts = blood_pressure_facts(text)
    assert facts and (facts[0].systolic, facts[0].diastolic) == pair and facts[0].usable


def test_decimal_kpa_still_flagged_for_review():
    facts = blood_pressure_facts("HA 21.3/14.7 kPa")
    assert facts and facts[0].assertion_status == "uncertain"


def test_date_like_only_pair_is_flagged_not_dropped():
    facts = blood_pressure_facts("HA 16/11")
    assert facts and facts[0].assertion_status == "uncertain"


def test_llm_quote_of_same_negated_mention_is_corrected(rules):
    r = read_free_text("em không ra chút máu nào", 30, _llm("BLEEDING", "ra chút máu"), rules, ner=False)
    assert r.level.code != "red"


def test_decline_is_scoped_to_pregnancy(rules, db):
    from datetime import date, datetime

    from core.models import Patient
    from core.patients import declined_codes, record_plan_declines

    with db.session_scope() as s:
        p = Patient(pid="T2", code="R9002", full_name_enc="x", phone_enc="x", consent_status="granted",
                    risk_groups=["P-TSG"], proposed_risk_flags=[], edd=date(2026, 6, 1))
        s.add(p)
        s.flush()
        record_plan_declines(s, p, ["P-TSG"], [], None, datetime(2026, 3, 1, 9))
        assert "P-TSG" in declined_codes(s, p.id, p.edd)
        p.edd = date(2027, 9, 1)  # lần mang thai sau
        assert "P-TSG" not in declined_codes(s, p.id, p.edd)


# ---------------------------------------------------------------- lượt rà soát 3
def test_affirmed_beats_severe_info_mention():
    from core.ai.form_reader import lexicon_findings

    fs = lexicon_findings("Em đọc trên mạng thấy đau bụng dữ dội là nguy hiểm, giờ em đang đau bụng")
    ab = next(f for f in fs if f["concept"] == "ABD_PAIN")
    assert ab["assertion_status"] == "affirmed" and ab["present"]


def test_decline_survives_edd_correction_in_same_pregnancy(rules, db):
    from datetime import date, datetime

    from core.models import Patient
    from core.patients import declined_codes, record_plan_declines

    with db.session_scope() as s:
        p = Patient(pid="T3", code="R9003", full_name_enc="x", phone_enc="x", consent_status="granted",
                    risk_groups=["P-TSG"], proposed_risk_flags=[], edd=date(2027, 3, 1))
        s.add(p)
        s.flush()
        assert record_plan_declines(s, p, ["P-TSG"], [], None, datetime(2026, 11, 15, 9)) == ["P-TSG"]
        p.edd = date(2027, 2, 25)  # chỉnh ngày dự sinh sau siêu âm – cùng thai kỳ
        assert "P-TSG" in declined_codes(s, p.id, p.edd)


def test_saving_other_changes_does_not_decline_unseen_packs(rules, db):
    """Chỉ ghi 'gỡ' cho mã điều dưỡng chủ động bỏ (before − after), không suy ra từ lưu thay đổi khác."""
    from datetime import date, datetime

    from core.models import Patient
    from core.patients import declined_codes, record_plan_declines

    with db.session_scope() as s:
        p = Patient(pid="T4", code="R9004", full_name_enc="x", phone_enc="x", consent_status="granted",
                    risk_groups=[], proposed_risk_flags=[], edd=date(2027, 3, 1))
        s.add(p)
        s.flush()
        assert record_plan_declines(s, p, [], ["M-THA"], None, datetime(2026, 11, 15, 9)) == []
        assert declined_codes(s, p.id, p.edd) == set()


# ---------------------------------------------------------------- lượt rà soát 4 (code cũ)
def test_waiting_slot_with_minutes_only_note_does_not_crash(rules):
    from datetime import time

    from core.rules.arrival import compute_arrival

    res = compute_arrival(time(12, 15), ["Khám thai"], "đến trước 30 phút", None, rules)
    assert res.waiting and any("dặn đến trước 30 phút" in x for x in res.details)


def test_invalid_phone_is_masked_in_validation_report():
    from core.ingest.validators import _mask_raw_phone

    assert _mask_raw_phone("091234541") == "••••••541"
    assert _mask_raw_phone("không có") == "không có"


def test_teams_without_url_is_not_silently_mocked(rules, monkeypatch):
    from core.channels import AlertChannelMisconfigured, get_alert_channel

    class Cfg(dict):
        def __getitem__(self, k):  # type: ignore[no-untyped-def]
            return {**rules["channels"], "alert": "teams_webhook"} if k == "channels" else rules[k]

    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    ch = get_alert_channel(Cfg())
    with pytest.raises(AlertChannelMisconfigured):
        ch.send(None)
    with pytest.raises(AlertChannelMisconfigured):
        ch.send_digest([])


@pytest.mark.parametrize("text, leak", [
    ("Tên Hùng gọi hỏi lịch", "Hùng"),
    ("Đường Láng, Đống Đa", "Láng"),
    ("chuyển hồ sơ BN:12345678", "12345678"),
    ("mã 12345678, gọi lại", "12345678"),
])
def test_masking_capitalised_and_punctuated(text, leak):
    from core.ai.masking import mask_text

    m = mask_text(text, ner=False)
    assert leak not in m.text


def test_invalid_new_phone_does_not_overwrite_valid(db):
    from core.repo import patient_phone, upsert_patient

    with db.session_scope() as s:
        p = upsert_patient(s, "PX1", "Nguyễn A", "0912345678", "Việt Nam")
        upsert_patient(s, "PX1", "Nguyễn A", "không có", "Việt Nam")
        assert patient_phone(p) == "0912345678"


def test_pack_without_frequency_does_not_crash(rules):
    from core.rules.form_scheduler import schedule_text

    assert isinstance(schedule_text("P-TSG", rules["forms"]["modules"]["P-TSG"]), str)


# ---------------------------------------------------------------- lượt rà soát 5
@pytest.mark.parametrize("key, value", [
    ("personalization.selection", None),
    ("forms.windows", None),
    ("personalization.concept_questions", None),
])
def test_write_rule_multiline_dict_keys(tmp_rules, key, value):
    import yaml

    from core.rule_config import write_rule

    data = yaml.safe_load(tmp_rules.read_text(encoding="utf-8"))
    node = data
    for p in key.split("."):
        node = node[p]
    new = dict(node)
    first = next(iter(new))
    new[first] = new[first]  # cùng dạng; chỉ cần ghi được
    v = write_rule(tmp_rules, key, new)
    again = yaml.safe_load(tmp_rules.read_text(encoding="utf-8"))
    cur = again
    for p in key.split("."):
        cur = cur[p]
    assert cur == new and str(again["rules_version"]) == v


def test_bp_max_is_one_real_reading():
    from datetime import datetime

    from api.routers.profile import _bp_max

    hist = {"bp_sys": [(datetime(2026, 11, 9, 8), 150.0), (datetime(2026, 11, 12, 8), 120.0)],
            "bp_dia": [(datetime(2026, 11, 9, 8), 80.0), (datetime(2026, 11, 12, 8), 105.0)]}
    assert _bp_max(hist, datetime(2026, 11, 1)) == "150/80"


def test_same_day_weights_do_not_trigger_jump(rules):
    from datetime import datetime

    from core.ai.trends import weight_trend

    data = {**rules.data, "thresholds": {**rules["thresholds"], "weight_jump_kg_per_week": 1.0}}

    class R(dict):
        def __getitem__(self, k):  # type: ignore[no-untyped-def]
            return data[k]

        def get(self, k, d=None):  # type: ignore[no-untyped-def]
            return data.get(k, d)

    res = weight_trend([(datetime(2026, 11, 15, 7), 60.0), (datetime(2026, 11, 15, 20), 60.3)], R())
    assert res.level.code == "green"


def test_late_ga_label_follows_config(rules):
    from core.rules.review import reason_label

    assert reason_label("LATE_GA", rules) == f"Tuần thai ≥ {rules['arrival']['late_ga_weeks']}"


def test_risk_names_come_from_config(rules):
    from core.ai.summary import _risk_name

    names = rules["personalization"]["flag_names"]
    assert _risk_name("PRETERM_RISK") == names["PRETERM_RISK"]
    assert _risk_name("M-THA") == names["HTN"]


def test_fresh_clone_without_users_file_gets_setup_message_not_500(db, tmp_path, monkeypatch):
    """Clone mới chưa chạy make_key.py (config/users.yaml không lên GitHub): đăng nhập báo cách sửa, không lỗi 500."""
    from fastapi.testclient import TestClient

    from api.main import create_app

    monkeypatch.setenv("APP_USERS_FILE", str(tmp_path / "users.yaml"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_JOBS", "0")
    with TestClient(create_app(serve_web=False), raise_server_exceptions=False) as c:
        r = c.post("/api/auth/login", json={"username": "nurse", "password": "nurse123"})
    assert r.status_code == 503
    assert "make_key.py" in r.json()["detail"]


@pytest.mark.parametrize("text,level", [
    ("Nếu đứng lâu là em bị ra máu", "yellow"),          # kể việc lặp lại, không phải giả định → xác minh
    ("Khi nào đi tiểu em cũng thấy ra máu", "yellow"),
    ("nếu đi lại nhiều em lại bị gò bụng", "yellow"),
    ("nếu ra máu thì em phải làm sao", "green"),          # câu hỏi giả định thật
    ("Nếu bé đạp ít thì em phải làm gì", "green"),
    ("Em không chắc có ra máu không nhưng quần lót có vết đỏ", "red"),
    ("Em đi vệ sinh thấy giấy có vết máu", "red"),
    ("chảy máu chân răng có vết máu trên bàn chải", "green"),
])
def test_habitual_conditional_and_stain_phrases(rules, text, level):
    r = read_free_text(text, 32, MockClient(), rules)
    assert r.level.name.lower() == level, (text, r.level)
    if level == "yellow":
        assert r.conflicts  # phải kèm "cần gọi xác minh"


@pytest.mark.parametrize("text,level", [
    ("e đau bụng quá trời", "red"), ("bé ko đạp từ sáng tới giờ", "red"), ("e bị ra nc âm đạo", "red"),
    ("đau đầu kinh khủng", "red"), ("chan bi sung mot ben va dau", "red"), ("không phải là không ra máu", "red"),
    ("em không thể thở được", "red"), ("em không đi lại được vì đau bụng", "red"),
    ("em chẳng thấy bé đạp gì cả", "red"), ("không thấy bé đạp", "red"), ("không còn đạp nhiều như trước", "red"),
    ("tay chân em co quắp giật", "red"), ("sau sinh em ra sản dịch nhiều, thấm đẫm băng mỗi giờ", "red"),
    ("chưa bao giờ ra máu", "green"), ("con gái lớn của em bị sốt cao", "green"),
    ("Em bị ra máu tuần trước, đã đi khám rồi, giờ hết hẳn.", "yellow"),
    ("Em bị ra máu tuần trước, đã đi khám rồi. Giờ vẫn còn ra", "red"),
])
def test_more_customer_phrasings_20261115(rules, text, level):
    """Cách nói thường gặp mà OpenAI/Ollama hiểu đúng nhưng từ điển chưa có → Đỏ bị hạ Vàng (ai_only_red_cap)."""
    assert read_free_text(text, 32, MockClient(), rules).level.name.lower() == level, text


@pytest.mark.parametrize("text,level", [
    ("Em chóng mặt muốn xỉu", "yellow"),                 # gần ngất: Vàng (người dùng xác nhận 15/11/2026)
    ("tuần trước em co giật một lần", "red"),            # quá khứ chưa chứng minh đã hết: giữ Đỏ
    ("ra máu báo thai có nguy hiểm không ạ", "yellow"),  # câu hỏi kiến thức: Vàng + gọi xác minh
])
def test_confirmed_levels_20261115(rules, text, level):
    r = read_free_text(text, 32, MockClient(), rules)
    assert r.level.name.lower() == level, text
    if "nguy hiểm" in text:
        assert r.conflicts  # câu hỏi: kèm "cần gọi xác minh"
