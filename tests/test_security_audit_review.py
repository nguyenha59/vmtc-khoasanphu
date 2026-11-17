"""Security review 2026-11-14: isolated synthetic data, real authentication.

Regression checks exercise authentication, permissions and token boundaries.
No demo/live database or .env key is used.
"""

from __future__ import annotations

import io
import shutil
from datetime import time, timedelta
from types import SimpleNamespace

import bcrypt
import pandas as pd
import pytest
import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select


@pytest.fixture()
def review(tmp_path, monkeypatch, db):
    from api.main import create_app
    from api.routers import auth
    from api.throttle import LoginThrottle, RateLimiter
    from core.config import ROOT
    from core.db import now
    from core.forms import create_token
    from core.models import Appointment, Arrival
    from core.repo import upsert_patient

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_COOKIE_SECURE", "0")
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("APP_JOBS", "0")
    rules_path = tmp_path / "rules.yaml"
    shutil.copy(ROOT / "config" / "rules.yaml", rules_path)
    monkeypatch.setenv("APP_RULES_FILE", str(rules_path))
    users_path = tmp_path / "users.yaml"
    monkeypatch.setenv("APP_USERS_FILE", str(users_path))
    password = "Synthetic-review-only-2026!"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    users = [dict(username=role, name=f"Synthetic {role}", role=role, password_hash=hashed)
             for role in ("nurse", "doctor", "admin")]

    def write_users():
        users_path.write_text(yaml.safe_dump({"users": users}), encoding="utf-8")

    write_users()
    monkeypatch.setattr(auth, "login_throttle", LoginThrottle())
    monkeypatch.setattr(auth, "login_ip_throttle", LoginThrottle(max_fails=20))
    monkeypatch.setattr("api.throttle.public_limiter", RateLimiter(60, 600))
    today = now().date()
    yesterday = today - timedelta(days=1)
    with db.session_scope() as s:
        p = upsert_patient(s, "AUDIT-A", "Synthetic Patient A", "0900000001", "Việt Nam")
        p.edd, p.consent_status = today + timedelta(days=56), "granted"
        other = upsert_patient(s, "AUDIT-B", "Synthetic Patient B", "0900000002", "Việt Nam")
        other.edd, other.consent_status = p.edd, "granted"
        s.add(Appointment(patient_id=p.id, appt_date=yesterday, start_time=time(8), services=[]))
        s.add(Arrival(pid=other.pid, visit_date=yesterday))
        demo = create_token(s, p.id, "F6", 7, demo=True)
        normal = create_token(s, p.id, "F6", 7)
        pid, code = p.id, p.code
    with TestClient(create_app(serve_web=False)) as client:
        yield SimpleNamespace(client=client, db=db, users=users, write_users=write_users,
                              password=password, demo=demo, normal=normal, pid=pid, code=code,
                              today=today, tmp_path=tmp_path)


def login(review, role="nurse"):
    review.client.cookies.clear()
    r = review.client.post("/api/auth/login", json={"username": role, "password": review.password})
    assert r.status_code == 200
    return review.client.cookies.get("tl_session")


def audit_count(review):
    from core.models import AuditLog

    with review.db.session_scope() as s:
        return s.scalar(select(func.count(AuditLog.id)))


@pytest.mark.parametrize("path", [
    "/api/patients", "/api/profile?q=R0001", "/api/alerts", "/api/appointments",
    "/api/noshow/reinvite.xlsx", "/api/noshow/call-list.xlsx?d=2026-11-14",
    "/api/appointments/export/test.xlsx", "/api/form-schedule/file/test.xlsx",
])
def test_anonymous_patient_access_denied(review, path):
    assert review.client.get(path).status_code == 401


def test_signed_role_payload_cannot_replace_server_session(review):
    from api.deps import _serializer

    # Even someone able to sign a legacy role payload cannot invent a stored session.
    forged = _serializer().dumps({"username": "doctor", "role": "doctor", "user_id": 1})
    assert review.client.get("/api/profile", params={"q": review.code},
                             headers={"Cookie": f"tl_session={forged}"}).status_code == 401


def test_tampered_cookie_denied(review):
    token = login(review)
    review.client.cookies.clear()
    assert review.client.get("/api/auth/me", headers={"Cookie": f"tl_session={token}invalid"}).status_code == 401


def test_logout_invalidates_copied_cookie(review):
    token = login(review)
    assert review.client.post("/api/auth/logout").status_code == 200
    review.client.cookies.clear()
    assert review.client.get("/api/auth/me", headers={"Cookie": f"tl_session={token}"}).status_code == 401


def test_revoke_all_sessions_requires_fresh_login(review):
    from api.deps import revoke_user_sessions

    first, second = login(review), login(review)
    assert revoke_user_sessions("nurse") == 2
    review.client.cookies.clear()
    for token in (first, second):
        assert review.client.get("/api/auth/me", headers={"Cookie": f"tl_session={token}"}).status_code == 401
    login(review)
    assert review.client.get("/api/auth/me").status_code == 200


def test_role_reduction_and_account_removal_take_effect(review):
    login(review)
    review.users[0]["role"] = "admin"
    review.write_users()
    assert review.client.get("/api/profile", params={"q": review.code}).status_code == 403
    review.users.pop(0)
    review.write_users()
    assert review.client.get("/api/auth/me").status_code == 401


def test_login_attempts_are_rate_limited(review):
    statuses = [review.client.post("/api/auth/login", json={"username": "nurse", "password": "wrong"})
                .status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]


def test_profile_response_is_not_cacheable_and_masks_phone(review):
    login(review)
    response = review.client.get("/api/profile", params={"q": review.code})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "0900000001" not in response.text


def test_public_app_does_not_expose_staff_routes(review):
    from api.public_main import create_public_app

    with TestClient(create_public_app()) as public:
        for path in ("/api/patients", "/api/profile", "/api/auth/me", "/api/docs", "/api/openapi.json"):
            assert public.get(path).status_code == 404
        assert public.get(f"/api/public/form/{review.normal}").status_code == 200


def test_password_change_must_invalidate_previous_session(review):
    login(review)
    review.users[0]["password_hash"] = bcrypt.hashpw(b"Replacement-Password!", bcrypt.gensalt(rounds=4)).decode()
    review.write_users()
    assert review.client.get("/api/auth/me").status_code == 401


def test_restored_account_must_not_revive_old_session(review):
    login(review)
    removed = review.users.pop(0)
    review.write_users()
    assert review.client.get("/api/auth/me").status_code == 401
    review.users.append(removed)
    review.write_users()
    assert review.client.get("/api/auth/me").status_code == 401


def test_admin_must_not_export_patient_phone_numbers(review):
    login(review, "admin")
    assert review.client.get("/api/profile", params={"q": review.code}).status_code == 403
    response = review.client.get("/api/noshow/reinvite.xlsx")
    if response.status_code == 200:
        # Establish real disclosure, not merely an accessible empty route.
        sheet = pd.read_excel(io.BytesIO(response.content), dtype=str)
        assert sheet.loc[0, "Số điện thoại"] == "0900000001"
    assert response.status_code == 403


def test_denied_patient_access_must_be_audited(review):
    login(review, "admin")
    before = audit_count(review)
    assert review.client.get("/api/profile", params={"q": review.code}).status_code == 403
    assert audit_count(review) > before


def test_existing_demo_token_must_be_disabled_in_production(review, monkeypatch):
    from api.public_main import create_public_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_COOKIE_SECURE", "1")
    # A new public app executes the real production startup checks.
    with TestClient(create_public_app()) as public:
        assert public.get(f"/api/public/form/{review.normal}").status_code == 200
        assert public.get(f"/api/public/form/{review.demo}").status_code in (403, 404, 409)


def test_authorized_exports_currently_have_no_request_quota(review):
    """Observation only: 30 successes do not certify protection against mass exports."""
    login(review)
    for _ in range(30):
        response = review.client.get("/api/noshow/reinvite.xlsx")
        assert response.status_code == 200
    assert audit_count(review) == 31  # login + 30 exports, no incident/threshold event
