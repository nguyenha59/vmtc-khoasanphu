"""Luồng end-to-end qua HTTP (đúng các lệnh gọi giao diện web dùng), chạy trên bản sao CSDL + bản sao rules.yaml.

Thứ tự theo một ngày làm việc:
  1. Điều dưỡng tải file HIS → xác nhận hết ca cần xem → xuất WorldSMS → tải file.
  2. Lịch gửi form → xuất SMS → tải file → nhắc lại.
  3. Đồng thuận → tạo đường dẫn form → thai phụ (không đăng nhập) trả lời → cảnh báo đỏ
     → bác sĩ xem → điều dưỡng hiện số (ghi nhật ký) → báo bác sĩ → ghi xử trí, đóng → hồ sơ thấy câu trả lời + cảnh báo → in.
  4. Rút đồng thuận → không tạo được đường dẫn; đường dẫn cũ chưa dùng bị vô hiệu.
  5. Đề xuất quy tắc (điều dưỡng) → bác sĩ duyệt → rules_version tăng (chỉ trên bản sao).
  6. Bỏ hẹn: xem, tải danh sách gọi / mời đặt lại, nhập danh sách khách đến.
  7. Chất lượng AI (xem), trang SPA, đăng xuất.
AI dùng `mock` để không phụ thuộc Ollama.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "app.db"
SYNTH = ROOT / "data" / "synthetic"
WEB_BUILT = (ROOT / "web" / "dist" / "index.html").exists()  # bước 7 README (npm run build); clone mới chưa có

pytestmark = pytest.mark.skipif(not DEMO_DB.exists() or not (ROOT / ".env").exists(),
                                reason="Chưa chạy scripts/make_key.py + scripts/seed_demo.py")

PASSWORDS = {"nurse": "nurse123", "doctor": "doctor123", "admin": "admin123"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def env(tmp_path_factory) -> Any:
    from fastapi.testclient import TestClient

    tmp = tmp_path_factory.mktemp("e2e")
    dotenv = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.startswith("#"))
    shutil.copy(DEMO_DB, tmp / "app.db")
    shutil.copy(ROOT / "config" / "rules.yaml", tmp / "rules.yaml")
    new_env = {"APP_SECRET_KEY": dotenv["APP_SECRET_KEY"].strip(),
               "APP_DB_URL": f"sqlite:///{(tmp / 'app.db').as_posix()}",
               "APP_RULES_FILE": str(tmp / "rules.yaml"),
               "APP_DATA_DIR": str(tmp / "data"),  # uploads / exports / outbox vào thư mục tạm
               "LLM_PROVIDER": "mock"}
    old = {k: os.environ.get(k) for k in new_env}
    os.environ.update(new_env)

    from core import db as dbmod

    dbmod._engine = None
    from api.main import create_app

    with TestClient(create_app(serve_web=True)) as c:
        yield {"client": c, "tmp": tmp, "state": {}}

    dbmod._engine = None
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _login(c: Any, role: str) -> None:
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"username": role, "password": PASSWORDS[role]})
    assert r.status_code == 200, r.text


def _audit(action: str, entity_id: str | None = None) -> int:
    from sqlalchemy import func, select

    from core.db import session_scope
    from core.models import AuditLog

    q = select(func.count(AuditLog.id)).where(AuditLog.action == action)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == str(entity_id))
    with session_scope() as s:
        return s.scalar(q) or 0


# ------------------------------------------------------------------ 1. Lịch hẹn
def test_1_appointments_upload_confirm_export(env):
    c = env["client"]
    _login(c, "nurse")
    samples = c.get("/api/appointments").json()["samples"]
    name = sorted(n for n in samples if n.startswith("his_export_"))[-1]
    d = name.removeprefix("his_export_").removesuffix(".xlsx")

    up = c.post("/api/appointments/upload", data={"d": d, "use_ai": "false", "sample": name})
    assert up.status_code == 200 and up.json()["ok"], up.text
    data = c.get("/api/appointments", params={"d": d}).json()
    assert data["date"] == d and data["items"], "Tải file HIS xong nhưng danh sách lịch hẹn trống"
    assert all("•" in i["phone_masked"] for i in data["items"])

    need = [i for i in data["items"] if i["status"] == "needs_review" or (i["arrival_time"] is None and i["method"] != "waiting")]
    if need:  # còn ca cần xác nhận → khóa xuất file
        assert c.post("/api/appointments/export", params={"d": d}).status_code == 409

    for it in need:
        det = c.get(f"/api/appointments/plan/{it['plan_id']}", params={"d": d})
        assert det.status_code == 200, det.text
        det = det.json()
        assert "phone" not in det
        t = None if det["method"] == "waiting" else det["default_time"]
        r = c.post(f"/api/appointments/plan/{it['plan_id']}/confirm", json={"d": d, "arrival_time": t})
        assert r.status_code == 200, (it, r.text)

    # nút "Đã gọi bác sĩ" ghi nhật ký
    pid = data["items"][0]["plan_id"]
    before = _audit("called_doctor", pid)
    assert c.post(f"/api/appointments/plan/{pid}/called-doctor", params={"d": d}).status_code == 200
    assert _audit("called_doctor", pid) == before + 1

    after = c.get("/api/appointments", params={"d": d}).json()
    assert after["counts"]["need"] == 0

    ex = c.post("/api/appointments/export", params={"d": d})
    assert ex.status_code == 200, ex.text
    files = c.get("/api/appointments", params={"d": d}).json()["exports"]
    assert files, "Đã xuất nhưng màn Lịch hẹn không thấy file"
    dl = c.get(f"/api/appointments/export/{files[0]}")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"  # file xlsx (zip)

    ov = c.get("/api/overview")
    assert ov.status_code == 200


# ------------------------------------------------------------------ 2. Lịch gửi form
def test_2_form_schedule_export_and_reminders(env):
    c = env["client"]
    _login(c, "nurse")
    fs = c.get("/api/form-schedule").json()
    r = c.post("/api/form-schedule/export")
    if not fs["send"]:
        assert r.status_code == 409
    else:
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["channel"] in {"excel", "mock"}
        if body["file"]:
            dl = c.get(f"/api/form-schedule/file/{body['file']}")
            assert dl.status_code == 200 and dl.headers["content-type"].startswith(XLSX)
        # đã lưu lịch → gửi lại hôm nay không còn ai trong danh sách gửi
        again = c.get("/api/form-schedule").json()
        assert len(again["send"]) <= len(fs["send"])
    rem = c.post("/api/form-schedule/reminders")
    assert rem.status_code == 200, rem.text
    for p in fs["proposals"][:1]:
        assert c.post(f"/api/form-schedule/proposals/{p['patient_id']}/confirm", json={"insulin": False}).status_code == 200
        assert _audit("confirm_module", p["code"]) >= 1


# ------------------------------------------------------------------ 3. Form → cảnh báo → xử trí → hồ sơ
def _pick_patient() -> tuple[int, str]:
    from sqlalchemy import select

    from core.db import session_scope
    from core.models import Patient

    with session_scope() as s:
        p = s.scalars(select(Patient).where(Patient.delivery_date.is_(None)).order_by(Patient.id)).first()
        assert p is not None
        return p.id, p.code


def test_3_form_to_red_alert_to_closed(env):
    c = env["client"]
    pid, code = _pick_patient()
    env["state"]["patient"] = (pid, code)

    _login(c, "nurse")
    prof = c.get("/api/profile", params={"q": code})
    assert prof.status_code == 200, prof.text
    if prof.json()["consent"]["status"] != "granted":
        assert c.post(f"/api/profile/{pid}/consent/grant", json={"note": "Đồng ý tại quầy"}).status_code == 200
    assert c.get("/api/profile", params={"q": code}).json()["consent"]["status"] == "granted"

    tok = c.post("/api/forms/demo-token", json={"code": code, "form": "F6"})
    assert tok.status_code == 200, tok.text
    token, path = tok.json()["token"], tok.json()["path"]
    assert path == f"/form?token={token}"

    # Thai phụ mở đường dẫn: không đăng nhập; trang SPA /form trả index.html
    c.cookies.clear()
    page = c.get(path)
    if WEB_BUILT:
        assert page.status_code == 200 and "<div id=\"root\">" in page.text
    f = c.get(f"/api/public/form/{token}")
    assert f.status_code == 200, f.text
    form = f.json()
    assert form["code"] == code and "full_name" not in form
    keys = {q["key"] for q in form["questions"]}
    assert "C1" in keys
    answers = {q["key"]: "Không" for q in form["questions"] if q["type"] == "yesno"}
    answers["C1"] = "Có"  # ra máu âm đạo
    sub = c.post(f"/api/public/form/{token}", json={"answers": answers, "free_text": "Sáng nay em thấy ra máu đỏ tươi"})
    assert sub.status_code == 200, sub.text
    assert sub.json()["level"] == "red"
    assert c.post(f"/api/public/form/{token}", json={"answers": answers}).status_code == 409  # dùng một lần

    # Bác sĩ thấy cảnh báo đỏ mới
    _login(c, "doctor")
    lst = c.get("/api/alerts").json()
    mine = [i for i in lst["items"] if i["code"] == code and i["form"] == "F6" and i["status"] == "open"]
    assert mine, "Câu trả lời đỏ không tạo cảnh báo trên màn Cảnh báo"
    alert = max(mine, key=lambda i: i["id"])
    assert alert["level"] == "red"
    assert alert["demo"] is True  # tạo từ màn Form demo → đánh dấu, không lẫn với câu trả lời thật
    aid = alert["id"]
    det = c.get(f"/api/alerts/{aid}").json()
    assert det["layers"]["rule"]["level"] == "red"
    # màn chi tiết nói rõ "cảnh báo về cái gì" và lấy từ câu trả lời nào
    assert alert["reasons"], "Danh sách cảnh báo không ghi lý do"
    red_why = [w for w in det["why"] if w["level"] == "red"]
    assert red_why and det["why"][0]["level"] == "red"  # đỏ xếp trước
    assert any(w["source"] == "Câu trả lời trong form" and "trả lời: Có" in w["detail"] for w in red_why)
    assert "ra máu" in det["free_text"]
    assert "•" in det["phone_masked"] and "phone" not in det
    assert _audit("view_alert", aid) >= 1

    # Điều dưỡng gọi: hiện số → trạng thái "Đã gọi"; báo bác sĩ; ghi xử trí và đóng
    _login(c, "nurse")
    ph = c.post(f"/api/alerts/{aid}/reveal-phone")
    assert ph.status_code == 200 and ph.json()["phone"].startswith("0")
    assert _audit("reveal_phone", code) >= 1
    status = {i["id"]: i["status"] for i in c.get("/api/alerts").json()["items"]}
    assert status[aid] == "called"
    assert c.post(f"/api/alerts/{aid}/escalate").status_code == 200
    h = c.post(f"/api/alerts/{aid}/handle", json={"note": "Đã gọi, hẹn vào khám ngay", "final_level": "keep_red", "close": True})
    assert h.status_code == 200 and h.json()["status"] == "awaiting_doctor"  # giữ đỏ → chờ bác sĩ, chưa đóng
    status = {i["id"]: i["status"] for i in c.get("/api/alerts").json()["items"]}
    assert status[aid] == "escalated"

    # Bác sĩ quyết định bằng 1 nút soạn sẵn → đóng
    _login(c, "doctor")
    r = c.post(f"/api/alerts/{aid}/handle", json={"note": "Đã gọi, hẹn vào khám ngay", "final_level": "keep_red",
                                                  "decision": "visit_today"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert aid not in {i["id"] for i in c.get("/api/alerts").json()["items"]}
    closed = {i["id"]: i for i in c.get("/api/alerts", params={"include_closed": True}).json()["items"]}
    assert closed[aid]["status"] == "closed"
    _login(c, "nurse")

    # Hồ sơ: thấy câu trả lời + cảnh báo đã xử trí; in tóm tắt
    p = c.get("/api/profile", params={"q": code}).json()
    assert any(r["form"] == "F6" and r["level"] == "red" for r in p["responses"])
    a = next(x for x in p["alerts"] if x["id"] == aid)
    assert a["final_level"] == "Giữ đỏ" and "khám ngay" in a["note"]
    pr = c.get(f"/api/profile/{pid}/print")
    assert pr.status_code == 200 and pr.content[:4] in (b"%PDF", b"<!DO", b"<htm")

    # Tổng quan đếm lại sau khi xử lý
    assert c.get("/api/overview").status_code == 200


# ------------------------------------------------------------------ 4. Rút đồng thuận
def test_4_withdraw_consent_blocks_forms(env):
    c = env["client"]
    pid, code = env["state"]["patient"]
    _login(c, "nurse")
    tok = c.post("/api/forms/demo-token", json={"code": code, "form": "F6"}).json()["token"]
    w = c.post(f"/api/profile/{pid}/consent/withdraw", json={"note": "Thai phụ không muốn nhận tin"})
    assert w.status_code == 200, w.text
    assert c.post("/api/forms/demo-token", json={"code": code, "form": "F6"}).status_code == 409
    c.cookies.clear()
    assert c.get(f"/api/public/form/{tok}").status_code == 404  # đường dẫn chưa dùng bị vô hiệu
    _login(c, "nurse")
    assert c.post(f"/api/profile/{pid}/consent/grant", json={"note": "Đồng ý lại"}).status_code == 200


# ------------------------------------------------------------------ 5. Cấu hình quy tắc
def test_5_propose_then_doctor_approves(env):
    c = env["client"]
    _login(c, "nurse")
    cfg = c.get("/api/config").json()
    v0 = cfg["rules_version"]
    rules = {r["key"]: r for g in cfg["groups"] for r in g["rules"]}
    key = next(k for k, r in rules.items() if r["requires_doctor"] and r["editable"]
               and isinstance(r["value"], int) and not isinstance(r["value"], bool))
    assert c.post("/api/config/propose", json={"key": key, "value": str(rules[key]["value"]), "reason": ""}).status_code == 422
    new_value = rules[key]["value"] + 1
    r = c.post("/api/config/propose", json={"key": key, "value": str(new_value), "reason": "E2E thử"})
    assert r.status_code == 200 and r.json()["status"] == "pending", r.text
    # khóa an toàn không sửa được
    locked = c.post("/api/config/propose", json={"key": "combine.ai_only_escalates", "value": "false", "reason": "x"})
    assert locked.status_code == 422
    # điều dưỡng không duyệt được
    pending = c.get("/api/config").json()["pending"]
    ch = next(p for p in pending if p["key"] == key)
    assert c.post(f"/api/config/changes/{ch['id']}/approve").status_code == 403

    _login(c, "doctor")
    ap = c.post(f"/api/config/changes/{ch['id']}/approve")
    assert ap.status_code == 200, ap.text
    v1 = ap.json()["rules_version"]
    assert v1 != v0
    cfg2 = c.get("/api/config").json()
    assert cfg2["rules_version"] == v1
    assert {r["key"]: r for g in cfg2["groups"] for r in g["rules"]}[key]["value"] == new_value
    # chỉ sửa bản sao, file thật giữ nguyên
    from core.config import load_rules

    assert load_rules(ROOT / "config" / "rules.yaml").rules_version == v0


# ------------------------------------------------------------------ 6. Bỏ hẹn
def test_6_noshow_lists_and_arrivals(env):
    c = env["client"]
    _login(c, "nurse")
    ns = c.get("/api/noshow")
    assert ns.status_code == 200, ns.text
    data = ns.json()
    assert all("phone" not in r for r in data["rows"])
    cl = c.get("/api/noshow/call-list.xlsx", params={"d": data["date"]})
    assert cl.status_code == 200 and cl.content[:2] == b"PK"
    ri = c.get("/api/noshow/reinvite.xlsx")
    assert ri.status_code == 200 and ri.content[:2] == b"PK"
    arr = sorted(SYNTH.glob("arrivals_*.xlsx"))
    if arr:
        with open(arr[-1], "rb") as fh:
            up = c.post("/api/noshow/arrivals", files={"file": (arr[-1].name, fh, XLSX)})
        assert up.status_code == 200 and up.json()["rows"] > 0, up.text
    _login(c, "nurse")
    assert c.post("/api/noshow/retrain").status_code == 403  # chỉ admin


# ------------------------------------------------------------------ 7. Chất lượng, SPA, đăng xuất
def test_7_quality_spa_logout(env):
    c = env["client"]
    _login(c, "doctor")
    q = c.get("/api/quality")
    assert q.status_code == 200 and "cards" in q.json()
    fb = q.json()["feedback"]  # vòng học từ kết quả gọi (ca demo ở bước 3 không được tính – xem test_security_fixes)
    assert {"overall", "reasons", "by_rules_version", "review"} <= set(fb)
    assert c.post("/api/quality/run/forms").status_code == 403  # chạy đánh giá: chỉ admin
    for path in ("/", "/alerts", "/appointments", "/profile", "/config") if WEB_BUILT else ():
        r = c.get(path)
        assert r.status_code == 200 and "<div id=\"root\">" in r.text, path
    # header bảo mật: chống nhúng iframe, không rò token form qua Referer, không lưu bộ nhớ đệm dữ liệu API
    page, api_r = c.get("/form?token=abc"), c.get("/api/auth/me")
    for r in (page, api_r):
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert api_r.headers["Cache-Control"] == "no-store"
    assert c.get("/api/khong-co").status_code == 404  # API gõ sai: 404 JSON, không trả trang HTML
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401
