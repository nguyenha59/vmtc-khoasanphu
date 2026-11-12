"""API FastAPI cho giao diện web: đăng nhập, phân quyền, nhật ký, cảnh báo (chạy trên bản sao CSDL demo)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "app.db"

pytestmark = pytest.mark.skipif(not DEMO_DB.exists() or not (ROOT / ".env").exists(),
                                reason="Chưa chạy scripts/make_key.py + scripts/seed_demo.py")

PASSWORDS = {"nurse": "nurse123", "doctor": "doctor123", "admin": "admin123"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient

    env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    old_key, old_db = os.environ.get("APP_SECRET_KEY"), os.environ.get("APP_DB_URL")
    os.environ["APP_SECRET_KEY"] = env["APP_SECRET_KEY"].strip()
    tmp = tmp_path_factory.mktemp("api")
    shutil.copy(DEMO_DB, tmp / "app.db")
    os.environ["APP_DB_URL"] = f"sqlite:///{(tmp / 'app.db').as_posix()}"
    from core import db as dbmod

    dbmod._engine = None
    from api.main import create_app

    with TestClient(create_app(serve_web=False)) as c:
        yield c
    dbmod._engine = None
    for k, v in (("APP_SECRET_KEY", old_key), ("APP_DB_URL", old_db)):
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _login(client, role: str) -> None:
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": role, "password": PASSWORDS[role]})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == role


def _audit_count(action: str) -> int:
    from sqlalchemy import func, select

    from core.db import session_scope
    from core.models import AuditLog

    with session_scope() as s:
        return s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == action)) or 0


def test_requires_login(client):
    client.cookies.clear()
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/alerts").status_code == 401


def test_wrong_password_is_logged(client):
    before = _audit_count("login_failed")
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "nurse", "password": "sai"})
    assert r.status_code == 401
    assert _audit_count("login_failed") == before + 1


def test_tampered_cookie_rejected(client):
    _login(client, "nurse")
    token = client.cookies.get("tl_session")
    assert client.get("/api/auth/me").status_code == 200
    client.cookies.clear()
    bad = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/api/auth/me", headers={"Cookie": f"tl_session={bad}"}).status_code == 401


def test_logout_revokes_session(client):
    """#6: trước đây cookie còn dùng được 12 giờ sau khi đăng xuất."""
    _login(client, "nurse")
    token = client.cookies.get("tl_session")
    assert client.post("/api/auth/logout").status_code == 200
    client.cookies.clear()
    assert client.get("/api/auth/me", headers={"Cookie": f"tl_session={token}"}).status_code == 401


def test_removed_user_loses_access_immediately(client, tmp_path, monkeypatch):
    """#6: vai trò / tài khoản đọc lại từ users.yaml mỗi yêu cầu."""
    import yaml

    _login(client, "nurse")
    assert client.get("/api/auth/me").status_code == 200
    users = yaml.safe_load((ROOT / "config" / "users.yaml").read_text(encoding="utf-8"))
    users["users"] = [u for u in users["users"] if u["username"] != "nurse"]
    p = tmp_path / "users.yaml"
    p.write_text(yaml.safe_dump(users, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("APP_USERS_FILE", str(p))
    assert client.get("/api/auth/me").status_code == 401


def test_role_change_applies_immediately(client, tmp_path, monkeypatch):
    import yaml

    _login(client, "nurse")
    assert client.get("/api/appointments").status_code == 200
    users = yaml.safe_load((ROOT / "config" / "users.yaml").read_text(encoding="utf-8"))
    for u in users["users"]:
        if u["username"] == "nurse":
            u["role"] = "doctor"
    p = tmp_path / "users.yaml"
    p.write_text(yaml.safe_dump(users, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("APP_USERS_FILE", str(p))
    assert client.get("/api/appointments").status_code == 403  # bác sĩ không có quyền tải HIS


def test_me_and_overview(client):
    _login(client, "nurse")
    me = client.get("/api/auth/me").json()
    assert "upload_his" in me["permissions"] and "approve_rule" not in me["permissions"]
    ov = client.get("/api/overview").json()
    assert set(ov["metrics"]) >= {"appointments_tomorrow", "open_alerts"}
    assert len(ov["steps"]) == 5


def test_admin_cannot_view_alerts(client):
    _login(client, "admin")
    assert client.get("/api/alerts").status_code == 403
    # kể cả qua màn Tổng quan (trước đây trả 5 cảnh báo mới nhất cho mọi vai trò)
    assert client.get("/api/overview").json()["latest_alerts"] == []
    _login(client, "doctor")
    assert client.get("/api/overview").json()["latest_alerts"]


def test_alert_detail_masks_phone_and_logs_view(client):
    _login(client, "doctor")
    items = client.get("/api/alerts").json()["items"]
    if not items:
        pytest.skip("CSDL demo không có cảnh báo")
    before = _audit_count("view_alert")
    d = client.get(f"/api/alerts/{items[0]['id']}").json()
    assert _audit_count("view_alert") == before + 1
    assert "•" in d["phone_masked"]
    assert "phone" not in d  # số đầy đủ chỉ qua reveal-phone
    assert {"rule", "trend", "ai"} <= set(d["layers"])


def test_reveal_phone_logged_and_handle(client):
    _login(client, "nurse")
    items = client.get("/api/alerts").json()["items"]
    if not items:
        pytest.skip("CSDL demo không có cảnh báo")
    aid = items[0]["id"]
    before = _audit_count("reveal_phone")
    r = client.post(f"/api/alerts/{aid}/reveal-phone")
    assert r.status_code == 200 and r.json()["phone"].startswith("0")
    assert _audit_count("reveal_phone") == before + 1
    assert client.post(f"/api/alerts/{aid}/handle", json={"note": "x", "final_level": "sai"}).status_code == 422
    h_before = _audit_count("handle_alert")
    r = client.post(f"/api/alerts/{aid}/handle", json={"note": "Đã gọi, ổn", "final_level": "down_yellow", "close": True})
    assert r.status_code == 200
    assert _audit_count("handle_alert") == h_before + 1
    closed = client.get("/api/alerts", params={"include_closed": True}).json()["items"]
    assert next(i for i in closed if i["id"] == aid)["status"] == "closed"


def _open_red(client) -> list[dict]:
    return [i for i in client.get("/api/alerts").json()["items"] if i["level"] == "red" and i["status"] in ("open", "called")]


def test_red_policy_via_api(client):
    """Giữ đỏ: điều dưỡng → Chờ bác sĩ; bác sĩ đóng bằng quyết định soạn sẵn; điều dưỡng không được chọn quyết định."""
    _login(client, "nurse")
    reds = _open_red(client)
    if not reds:
        pytest.skip("CSDL demo không có cảnh báo đỏ đang mở")
    aid = reds[0]["id"]
    assert client.post(f"/api/alerts/{aid}/handle", json={"final_level": "keep_red", "decision": "visit_today"}).status_code == 403
    assert client.post(f"/api/alerts/{aid}/handle", json={"note": "x"}).status_code == 422  # đỏ phải có mức sau gọi
    r = client.post(f"/api/alerts/{aid}/handle", json={"note": "Còn ra máu", "final_level": "keep_red", "close": True})
    assert r.status_code == 200 and r.json()["status"] == "awaiting_doctor"
    assert client.get("/api/alerts/reviews").status_code == 403  # danh sách xem lại: chỉ bác sĩ

    _login(client, "doctor")
    lst = client.get("/api/alerts").json()
    assert lst["counts"]["awaiting_doctor"] >= 1 and set(lst["decisions"]) == {"visit_today", "emergency", "monitor"}
    assert client.post(f"/api/alerts/{aid}/handle", json={"final_level": "keep_red"}).status_code == 422
    r = client.post(f"/api/alerts/{aid}/handle", json={"final_level": "keep_red", "decision": "emergency"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    d = client.get(f"/api/alerts/{aid}").json()
    assert d["doctor_decision"]["label"] == "Chuyển cấp cứu"
    assert _audit_count("doctor_decision") >= 1


def test_downgrade_review_list_and_ack(client):
    _login(client, "nurse")
    reds = _open_red(client)
    if not reds:
        pytest.skip("CSDL demo không có cảnh báo đỏ đang mở")
    aid = reds[0]["id"]
    assert client.post(f"/api/alerts/{aid}/handle", json={"final_level": "false_alarm", "note": "đọc nhầm"}).status_code == 200
    _login(client, "doctor")
    items = client.get("/api/alerts/reviews").json()["items"]
    mine = [i for i in items if i["alert_id"] == aid]
    if not mine:
        pytest.skip("ca này không rơi vào mẫu xem lại (tỷ lệ mẫu < 100%)")
    assert client.post("/api/alerts/reviews/ack", json={"review_ids": [mine[0]["review_id"]]}).json()["acked"] == 1
    assert aid not in {i["alert_id"] for i in client.get("/api/alerts/reviews").json()["items"]}


def test_joint_handling_only_same_patient(client):
    _login(client, "nurse")
    items = [i for i in client.get("/api/alerts").json()["items"] if i["level"] == "yellow"]
    other = next((j for j in items if items and j["code"] != items[0]["code"]), None)
    if not items or other is None:
        pytest.skip("cần cảnh báo của 2 thai phụ khác nhau")
    r = client.post(f"/api/alerts/{items[0]['id']}/handle", json={"note": "x", "also_ids": [other["id"]]})
    assert r.status_code == 422


def test_record_delivery(client):
    from datetime import date, timedelta

    _login(client, "nurse")
    codes = client.get("/api/profile/recent").json()["codes"]
    if not codes:
        pytest.skip("CSDL demo chưa có hồ sơ")
    pid = client.get("/api/profile", params={"q": codes[0]}).json()["patient_id"]
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post(f"/api/profile/{pid}/delivery", json={"delivery_date": future}).status_code == 422
    before = _audit_count("record_delivery")
    d = (date.today() - timedelta(days=3)).isoformat()
    assert client.post(f"/api/profile/{pid}/delivery", json={"delivery_date": d}).status_code == 200
    assert _audit_count("record_delivery") == before + 1
    p = client.get("/api/profile", params={"q": codes[0]}).json()
    assert p["delivery_date"] == d and "Hậu sản" in p["stage"]
    _login(client, "admin")
    assert client.post(f"/api/profile/{pid}/delivery", json={"delivery_date": d}).status_code == 403


# ---------------------------------------------------------------- các màn còn lại
SCREENS = [
    ("nurse", "/api/appointments"), ("nurse", "/api/form-schedule"), ("nurse", "/api/profile/recent"),
    ("nurse", "/api/noshow"), ("nurse", "/api/config"), ("doctor", "/api/quality"), ("doctor", "/api/config"),
    ("admin", "/api/quality"), ("admin", "/api/noshow"), ("nurse", "/api/forms/catalog"),
]
FORBIDDEN = [("doctor", "/api/appointments"), ("doctor", "/api/form-schedule"), ("nurse", "/api/quality"),
             ("admin", "/api/profile/recent"), ("doctor", "/api/noshow")]


@pytest.mark.parametrize("role,path", SCREENS)
def test_screen_endpoints_ok(client, role, path):
    _login(client, role)
    r = client.get(path)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("role,path", FORBIDDEN)
def test_screen_endpoints_forbidden(client, role, path):
    _login(client, role)
    assert client.get(path).status_code == 403


def test_profile_masks_phone_and_logs(client):
    _login(client, "nurse")
    codes = client.get("/api/profile/recent").json()["codes"]
    if not codes:
        pytest.skip("CSDL demo chưa có câu trả lời form")
    before = _audit_count("view_profile")
    d = client.get("/api/profile", params={"q": codes[0]}).json()
    assert _audit_count("view_profile") == before + 1
    assert "•" in d["phone_masked"] and "phone" not in d
    assert client.get("/api/profile", params={"q": "KHONG_CO"}).status_code == 404


def test_upload_his_sample_then_confirm_validation(client):
    _login(client, "nurse")
    samples = client.get("/api/appointments").json()["samples"]
    name = next(n for n in samples if n.startswith("his_export_"))
    d = name.removeprefix("his_export_").removesuffix(".xlsx")
    r = client.post("/api/appointments/upload", data={"d": d, "use_ai": "false", "sample": name})
    assert r.status_code == 200 and r.json()["ok"], r.text
    bad = client.post("/api/appointments/upload", data={"d": d, "use_ai": "false", "sample": "his_error_swapped_cols.xlsx"})
    assert bad.json()["ok"] is False and any(x["code"] == "E02" for x in bad.json()["report"])
    data = client.get("/api/appointments", params={"d": d}).json()
    assert data["items"]
    it = data["items"][0]
    assert "•" in it["phone_masked"]
    d = client.get(f"/api/appointments/plan/{it['plan_id']}", params={"d": data["date"]}).json()
    assert "•" in d["phone_masked"] and "phone" not in d
    r = client.post(f"/api/appointments/plan/{it['plan_id']}/confirm", json={"d": data["date"], "arrival_time": "23:59"})
    assert r.status_code == 422  # sau giờ hẹn


def test_export_names_cannot_escape_folder(client):
    _login(client, "nurse")
    assert client.get("/api/appointments/export/..%2F..%2F.env").status_code == 404


def test_public_form_flow(client):
    _login(client, "nurse")
    codes = client.get("/api/profile/recent").json()["codes"]
    if not codes:
        pytest.skip("CSDL demo chưa có thai phụ đã đồng thuận")
    r = client.post("/api/forms/demo-token", json={"code": codes[0], "form": "F6"})
    if r.status_code == 409:
        pytest.skip("Thai phụ chưa đồng thuận")
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    client.cookies.clear()  # trang công khai: không đăng nhập
    f = client.get(f"/api/public/form/{token}").json()
    assert "full_name" not in f and "phone" not in str(f).lower()
    ans = {q["key"]: "Không" for q in f["questions"] if q["type"] == "yesno"}
    res = client.post(f"/api/public/form/{token}", json={"answers": ans, "free_text": ""})
    assert res.status_code == 200, res.text
    assert res.json()["level"] in {"green", "yellow", "red"}
    # đường dẫn chỉ dùng một lần
    assert client.post(f"/api/public/form/{token}", json={"answers": ans}).status_code == 409
    assert client.get("/api/public/form/khong-hop-le").status_code == 404


def test_print_summary(client):
    _login(client, "nurse")
    codes = client.get("/api/profile/recent").json()["codes"]
    if not codes:
        pytest.skip("CSDL demo chưa có câu trả lời form")
    pid = client.get("/api/profile", params={"q": codes[0]}).json()["patient_id"]
    before = _audit_count("print_profile")
    r = client.get(f"/api/profile/{pid}/print")
    assert r.status_code == 200 and r.headers["content-type"] in {"application/pdf", "text/html"}
    assert _audit_count("print_profile") == before + 1


# ---------------------------------------------------------------- chống tấn công (rà soát 12/11/2026)
def test_public_form_rate_limited(client):
    from api.throttle import public_limiter

    public_limiter._hits.clear()
    try:
        codes = [client.get("/api/public/form/khong-co-token").status_code for _ in range(61)]
        assert codes[:60] == [404] * 60 and codes[60] == 429
    finally:
        public_limiter._hits.clear()


def test_login_password_spraying_blocked_per_ip(client):
    from api.throttle import login_ip_throttle, login_throttle

    login_ip_throttle._fails.clear()
    try:
        for i in range(20):
            client.post("/api/auth/login", json={"username": f"user{i}", "password": "sai"})
        r = client.post("/api/auth/login", json={"username": "nurse", "password": PASSWORDS["nurse"]})
        assert r.status_code == 429
    finally:
        login_ip_throttle._fails.clear()
        login_throttle._fails.clear()


def test_chunked_body_without_length_rejected(client):
    r = client.post("/api/auth/login", content=(b for b in [b'{"username":"a",', b'"password":"b"}']),
                    headers={"content-type": "application/json"})
    assert r.status_code == 411


def test_zip_bomb_upload_rejected(client):
    import io
    import zipfile

    _login(client, "nurse")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (210 * 1024 * 1024))
    r = client.post("/api/appointments/upload", data={"d": "2026-11-13", "use_ai": "false"},
                    files={"file": ("bom.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 422 and "bất thường" in r.json()["detail"]


# ---------------------------------------------------------------- ổn định khi nhiều người dùng (thử tải 12/11/2026)
def test_concurrent_first_login_creates_one_user(client):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import func, select

    from core.db import session_scope
    from core.models import User
    from core.repo import ensure_user
    from core.security import AppUser

    u = AppUser(username="dong_thoi", name="Thử", role="nurse")

    def go(_):  # noqa: ANN001
        with session_scope() as s:
            return ensure_user(s, u)

    with ThreadPoolExecutor(8) as ex:
        ids = set(ex.map(go, range(16)))
    assert len(ids) == 1
    with session_scope() as s:
        assert s.scalar(select(func.count(User.id)).where(User.username == "dong_thoi")) == 1


def test_corrupt_upload_returns_422_not_500(client):
    _login(client, "nurse")
    r = client.post("/api/appointments/upload", data={"d": "2026-11-13", "use_ai": "false"},
                    files={"file": ("hong.xlsx", b"PK\x03\x04 khong phai excel", "application/octet-stream")})
    assert r.status_code == 422


def test_backup_script(tmp_path):
    import sqlite3

    from scripts.backup_db import backup

    src = tmp_path / "src.db"
    with sqlite3.connect(src) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("create table t(x)")
        c.execute("insert into t values (1)")
    out = backup(src, tmp_path / "bk", keep=2)
    with sqlite3.connect(out) as c:
        assert c.execute("select x from t").fetchall() == [(1,)]


def test_his_upload_updates_patient_name(client):
    """Lần tải HIS mới có tên khác cho cùng PID → tin nhắn dùng tên mới (trước đây giữ tên cũ)."""
    from core.db import session_scope
    from core.repo import patient_name, upsert_patient

    with session_scope() as s:
        p = upsert_patient(s, "99555001", "Nguyễn Thị Cũ", "0900000001", "Việt Nam")
    with session_scope() as s:
        p = upsert_patient(s, "99555001", "Nguyễn Thị Mới", "0900000001", "Việt Nam")
        assert patient_name(p) == "Nguyễn Thị Mới"
    assert _audit_count("patient_name_updated_from_his") >= 1


def test_profile_visit_history(client):
    """Lịch sử khám: mỗi lần khám có trạng thái đến; phần kết quả khám để trống (chưa nối HIS/LIS), không bịa."""
    _login(client, "doctor")
    code = client.get("/api/profile/recent").json()["codes"][0]
    d = client.get("/api/profile", params={"q": code}).json()
    assert d["visits"], "thai phụ demo phải có lịch hẹn"
    v = d["visits"][0]
    for k in ("date", "time", "ga", "services", "doctor", "note", "attend", "attend_label", "next_date", "results"):
        assert k in v
    assert v["attend"] in {"came", "no_show", "unknown", "upcoming"}
    for x in d["visits"]:  # kết quả khám: None (chưa có) hoặc đủ các phần của phiếu khám thai
        if x["results"] is not None:
            assert {"vitals", "labs", "diagnoses", "plan"} <= set(x["results"])
            assert x["results"]["source"] == "synthetic"
    assert "program" in d
    assert [x["date"] for x in d["visits"]] == sorted((x["date"] for x in d["visits"]), reverse=True)



# ---------------------------------------------------------------- quản lý hồ sơ (tạo, theo gói, chỉ định ngoài gói)
def _new(pid: str, **kw) -> dict:
    return {"pid": pid, "full_name": "Thử Thị Hồ Sơ", "phone": "0912345678", "nationality": "Việt Nam",
            "edd": "2027-03-01", **kw}


def test_create_patient_validation_and_audit(client):
    _login(client, "nurse")
    before = _audit_count("create_patient")
    r = client.post("/api/patients", json=_new("TEST9001", in_package=True, package_name="Gói thai sản trọn gói"))
    assert r.status_code == 201, r.text
    assert _audit_count("create_patient") == before + 1
    assert client.post("/api/patients", json=_new("TEST9001")).status_code == 409  # trùng PID
    assert client.post("/api/patients", json=_new("TEST9002", phone="0912")).status_code == 422
    assert client.post("/api/patients", json=_new("TEST9003", in_package=True)).status_code == 422  # thiếu tên gói
    assert client.post("/api/patients", json=_new("TEST9004", full_name="An")).status_code == 422
    d = client.get("/api/profile", params={"q": "TEST9001"}).json()
    assert d["program"]["in_package"] and d["program"]["package_name"] == "Gói thai sản trọn gói"


def test_admin_cannot_create_patient(client):
    _login(client, "admin")
    assert client.post("/api/patients", json=_new("TEST9010")).status_code == 403


def test_override_monitoring_doctor_only_with_reason(client):
    _login(client, "nurse")
    pid = client.post("/api/patients", json=_new("TEST9020")).json()["patient_id"]
    body = {"in_package": False, "override_monitoring": True, "override_reason": "Tiền sản giật, cần theo dõi sát"}
    assert client.put(f"/api/patients/{pid}/program", json=body).status_code == 403  # điều dưỡng không chỉ định được
    _login(client, "doctor")
    short = {**body, "override_reason": "THA"}
    assert client.put(f"/api/patients/{pid}/program", json=short).status_code == 422  # lý do quá ngắn
    before = _audit_count("update_patient_program")
    r = client.put(f"/api/patients/{pid}/program", json=body)
    assert r.status_code == 200 and r.json()["override_monitoring"] is True
    assert _audit_count("update_patient_program") == before + 1
    _login(client, "nurse")  # điều dưỡng vẫn đổi được theo gói, không làm mất chỉ định của bác sĩ
    r = client.put(f"/api/patients/{pid}/program", json={**body, "in_package": True, "package_name": "Gói thai sản trọn gói"})
    assert r.status_code == 200 and r.json()["in_package"] and r.json()["override_monitoring"]


def test_patient_list_filters(client):
    _login(client, "nurse")
    all_ = client.get("/api/patients", params={"size": 100}).json()
    pkg = client.get("/api/patients", params={"program": "package", "size": 100}).json()
    assert pkg["total"] > 0 and pkg["total"] < all_["total"]
    assert all(i["in_package"] for i in pkg["items"])
    assert all("••" in i["phone_masked"] or i["phone_masked"] == "•••" for i in all_["items"])  # SĐT luôn che
    one = client.get("/api/patients", params={"q": "TEST9001"}).json()
    assert [i["pid"] for i in one["items"]] == ["TEST9001"]


def test_form_schedule_only_eligible_patients(client):
    """Luật package_only: lịch gửi form chỉ gồm khách theo gói hoặc được bác sĩ chỉ định ngoài gói."""
    from sqlalchemy import select

    from core.db import session_scope
    from core.models import Patient, PatientProgram

    _login(client, "nurse")
    d = client.get("/api/form-schedule").json()
    codes = {x["code"] for x in d["send"] + d["skip"]}
    with session_scope() as s:
        ok = {code for code, pr in s.execute(select(Patient.code, PatientProgram).join(
            PatientProgram, PatientProgram.patient_id == Patient.id)) if pr.in_package or pr.override_monitoring}
    assert codes and codes <= ok
