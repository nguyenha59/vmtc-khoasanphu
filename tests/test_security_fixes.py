"""Các lỗi tìm thấy khi rà soát kiến trúc / bảo mật (11/11/2026) – mỗi lỗi một nhóm test."""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta

import pytest

from core.ai.llm_client import MockClient


@pytest.fixture()
def patient(db):
    from core.config import load_forms
    from core.models import Patient
    from core.repo import upsert_patient

    with db.session_scope() as s:
        upsert_patient(s, "P0001", "Phạm Thị Thu", "0900000001", "Việt Nam")
        p = s.query(Patient).filter_by(code="R0001").one()
        p.edd = date.today() + timedelta(days=280 - 32 * 7)
        p.consent_status = "granted"
        pid = p.id
    return pid, load_forms()


def _no_answers(forms):
    return {q: "Không" for q in forms.forms["F6"]["questions"] if forms.questions[q]["type"] == "yesno"}


# ------------------------------------------------------------------ #1 khóa CSDL khi chờ AI
def test_waiting_for_ai_does_not_lock_database(db, rules, patient):
    """Trước đây: gửi form giữ khóa ghi suốt lúc chờ AI → thao tác khác lỗi 'database is locked'."""
    from core import audit
    from core.alerts import submit_response
    from core.models import Patient

    pid, forms = patient
    in_llm = threading.Event()

    def slow(prompt, schema, seed):
        in_llm.set()
        time.sleep(3)
        return {"findings": [], "unmapped_concerns": []}

    def submit():
        with db.session_scope() as s:
            submit_response(s, s.get(Patient, pid), "F6", _no_answers(forms), "em hơi mệt", rules=rules,
                            forms_cfg=forms, client=MockClient(responder=slow), alert_channel=None, ner=False)

    t = threading.Thread(target=submit)
    t.start()
    assert in_llm.wait(10)
    t0 = time.time()
    audit.log("view_alert", "alert", 1)  # thao tác ghi khác trong lúc AI đang chạy
    waited = time.time() - t0
    t.join()
    assert waited < 1.5, f"thao tác ghi phải chờ {waited:.1f}s – vẫn đang giữ khóa trong lúc chờ AI"


def test_same_token_cannot_be_submitted_twice(db, rules, patient):
    from core.alerts import FormAlreadySubmitted, submit_response
    from core.forms import create_token
    from core.models import FormResponse, Patient

    pid, forms = patient
    with db.session_scope() as s:
        tok = create_token(s, pid, "F6", 7)
    with db.session_scope() as s:
        submit_response(s, s.get(Patient, pid), "F6", _no_answers(forms), "", rules=rules, forms_cfg=forms,
                        client=MockClient(), alert_channel=None, token=tok, ner=False)
    with pytest.raises(FormAlreadySubmitted), db.session_scope() as s:
        submit_response(s, s.get(Patient, pid), "F6", _no_answers(forms), "", rules=rules, forms_cfg=forms,
                        client=MockClient(), alert_channel=None, token=tok, ner=False)
    with db.session_scope() as s:
        assert s.query(FormResponse).filter_by(token=tok).count() == 1


def test_demo_responses_marked_and_excluded_from_feedback(db, rules, patient):
    """#3: câu trả lời từ Form demo (nhân viên tự điền) ghi source='demo' và không tính vào vòng học."""
    from core.alerts import handle_alert, submit_response
    from core.feedback import outcomes_from_db
    from core.forms import create_token, is_demo_token
    from core.models import Patient

    pid, forms = patient
    red = _no_answers(forms) | {"C1": "Có"}
    with db.session_scope() as s:
        demo_tok, real_tok = create_token(s, pid, "F6", 7, demo=True), create_token(s, pid, "F6", 7)
    assert is_demo_token(demo_tok) and not is_demo_token(real_tok) and len(demo_tok) <= 32
    ids = []
    for tok in (demo_tok, real_tok):
        with db.session_scope() as s:
            _r, _a, alert, _ = submit_response(s, s.get(Patient, pid), "F6", red, "", rules=rules, forms_cfg=forms,
                                               client=MockClient(), alert_channel=None, token=tok, ner=False,
                                               source="demo" if is_demo_token(tok) else "app")
            ids.append(alert.id)
    with db.session_scope() as s:
        for aid in ids:
            handle_alert(s, aid, None, status="closed", note="", final_level="false_alarm")
    with db.session_scope() as s:
        assert [o.alert_id for o in outcomes_from_db(s)] == [ids[1]]


def test_demo_token_disabled_in_production(monkeypatch):
    from fastapi.testclient import TestClient

    import api.main as main_mod
    from api.deps import current_user
    from api.main import create_app
    from core.security import AppUser

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(main_mod, "production_problems", lambda: [])  # điều kiện khởi động: test riêng bên dưới
    from api.deps import CurrentUser

    app = create_app(serve_web=False)
    app.dependency_overrides[current_user] = lambda: CurrentUser(AppUser("nurse", "N", "nurse"), 1)
    with TestClient(app) as c:
        r = c.post("/api/forms/demo-token", json={"code": "R0001", "form": "F6"})
    assert r.status_code == 403


# ------------------------------------------------------------------ #2 bề mặt công khai, đoán mật khẩu, production
def test_login_throttle_blocks_after_max_fails():
    from api.throttle import LoginThrottle

    th = LoginThrottle(max_fails=3, window=60)
    k = th.key("1.2.3.4", "Nurse ")
    assert k == th.key("1.2.3.4", "nurse")
    for i in range(3):
        assert th.retry_after(k, t=100 + i) == 0
        th.failed(k, t=100 + i)
    assert th.retry_after(k, t=105) > 0  # khóa
    assert th.retry_after(th.key("5.6.7.8", "nurse"), t=105) == 0  # máy khác không bị ảnh hưởng
    assert th.retry_after(k, t=100 + 61) == 0  # hết cửa sổ
    th.failed(k, t=200)
    th.succeeded(k)
    assert th.retry_after(k, t=201) == 0


def test_login_endpoint_returns_429(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from api.main import create_app
    from api.throttle import LoginThrottle

    monkeypatch.setenv("APP_DB_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    import api.routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "login_throttle", LoginThrottle(max_fails=2, window=60))
    with TestClient(create_app(serve_web=False)) as c:
        codes = [c.post("/api/auth/login", json={"username": "nurse", "password": "sai"}).status_code for _ in range(3)]
        ok = c.post("/api/auth/login", json={"username": "nurse", "password": "nurse123"}).status_code
    assert codes == [401, 401, 429] and ok == 429  # đang khóa thì mật khẩu đúng cũng phải chờ
    from core import db as dbmod

    dbmod._engine = None
    dbmod._Session = None


def test_production_refuses_demo_passwords_and_http(monkeypatch):
    from api.main import production_problems

    monkeypatch.delenv("APP_COOKIE_SECURE", raising=False)
    probs = production_problems()
    assert any("APP_COOKIE_SECURE" in p for p in probs)
    assert sum("mật khẩu demo" in p for p in probs) == 3  # nurse, doctor, admin trong users.yaml mẫu


def test_public_app_exposes_only_patient_form(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from api.main import WEB_DIST
    from api.public_main import create_public_app

    monkeypatch.setenv("APP_DB_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    with TestClient(create_public_app()) as c:
        for path in ("/api/auth/me", "/api/alerts", "/api/forms/catalog", "/api/docs", "/api/openapi.json"):
            assert c.get(path).status_code == 404, path
        assert c.post("/api/auth/login", json={"username": "nurse", "password": "nurse123"}).status_code in (404, 405)
        assert c.get("/api/public/form/khong-co").status_code == 404  # có route, token sai
        if (WEB_DIST / "index.html").exists():
            assert c.get("/form?token=x").status_code == 200
            assert c.get("/").status_code == 404 and c.get("/canh-bao").status_code == 404
        big = c.post("/api/public/form/x", content=b"{" + b" " * 70_000 + b"}",
                     headers={"Content-Type": "application/json"})
        assert big.status_code == 413
    from core import db as dbmod

    dbmod._engine = None
    dbmod._Session = None


# ------------------------------------------------------------------ #5 bằng chứng lưu rõ
def test_lexicon_evidence_is_masked_before_storage(rules):
    """Trước đây bằng chứng của lớp danh mục là câu gốc (có thể chứa SĐT / tên) và lưu không mã hóa."""
    from core.ai.form_reader import read_free_text

    text = "Chị Thu ơi em ra máu nhiều, gọi em số 0912345678 nhé"
    r = read_free_text(text, 32, None, rules, patient_name="Phạm Thị Thu", pid="P0001", ner=False)
    ev = [f.evidence for f in r.findings if f.concept == "BLEEDING"]
    assert ev, "phải vẫn phát hiện ra máu"
    for e in ev:
        assert "0912345678" not in e and "Thu" not in e


def test_mask_fragment_drops_when_unmaskable():
    from core.ai.masking import mask_fragment

    # dãy 10 số không khớp mẫu SĐT/CCCD/CMND → lần quét 2 vẫn thấy số dài → không lưu
    assert mask_fragment("mã của em 1234567890", full_name=None, pid=None, persons=[]) == ""
    assert mask_fragment("em bị ra máu", full_name=None, pid=None, persons=[]) == "em bị ra máu"
    assert "[TÊN]" in mask_fragment("anh Tuấn bảo em đi khám", full_name=None, pid=None, persons=["Tuấn"])


def test_alert_channel_failure_keeps_response(db, rules, patient):
    """Kênh gửi lỗi (Teams sập) không được làm mất câu trả lời; ghi nhật ký để gửi lại."""
    from core.alerts import submit_response
    from core.models import Alert, AuditLog, FormResponse, Patient

    pid, forms = patient

    class Broken:
        def send(self, payload):
            raise ConnectionError("teams down")

    answers = _no_answers(forms) | {"C1": "Có"}
    with db.session_scope() as s:
        _r, _a, alert, _res = submit_response(s, s.get(Patient, pid), "F6", answers, "", rules=rules,
                                              forms_cfg=forms, client=MockClient(), alert_channel=Broken(), ner=False)
        aid = alert.id
    with db.session_scope() as s:
        assert s.query(FormResponse).count() == 1
        assert s.get(Alert, aid).sent_at is None
        assert s.query(AuditLog).filter_by(action="alert_send_failed").count() == 1
