"""Đề xuất theo dõi thêm (màn Lịch gửi form): điều dưỡng chỉ xác nhận được khi THẤY hồ sơ + căn cứ (15/11/2026).

- Mỗi đề xuất kèm hồ sơ tóm tắt (tuần thai, lần khám, lần hẹn) và căn cứ từng nhóm nguy cơ (nguồn + nội dung);
  nguồn "AI đọc ghi chú" kèm câu ghi chú ĐÃ CHE, ngày hẹn, độ tin cậy.
- Xác nhận ĐÚNG mã điều dưỡng đã xem; mã không còn chờ → 409 (tải lại).
- Không đồng ý: bắt buộc lý do, ghi nhật ký, không đề xuất lại trong lần mang thai này (kể cả mã đã gỡ ở Hồ sơ).
Chạy trên BẢN SAO CSDL demo.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "data" / "app.db"
pytestmark = pytest.mark.skipif(not DEMO_DB.exists() or not (ROOT / ".env").exists(),
                                reason="Chưa chạy scripts/make_key.py + scripts/seed_demo.py")


@pytest.fixture()
def client(tmp_path) -> Any:
    from fastapi.testclient import TestClient

    dotenv = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.startswith("#"))
    shutil.copy(DEMO_DB, tmp_path / "app.db")
    new_env = {"APP_SECRET_KEY": dotenv["APP_SECRET_KEY"].strip(),
               "APP_DB_URL": f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
               "APP_DATA_DIR": str(tmp_path / "data"), "LLM_PROVIDER": "mock", "APP_JOBS": "0"}
    old = {k: os.environ.get(k) for k in new_env}
    os.environ.update(new_env)
    from core import db as dbmod

    dbmod._engine, dbmod._Session = None, None
    from core.config import load_rules
    from scripts.seed_demo import seed_note_evidence

    with dbmod.session_scope() as s:  # CSDL demo cũ chưa có căn cứ → bổ sung như seed mới
        seed_note_evidence(s, load_rules())
    from api.main import create_app

    with TestClient(create_app()) as c:
        assert c.post("/api/auth/login", json={"username": "nurse", "password": "nurse123"}).status_code == 200
        yield c
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    dbmod._engine, dbmod._Session = None, None


def _proposals(c) -> list[dict]:
    r = c.get("/api/form-schedule")
    assert r.status_code == 200, r.text
    return r.json()["proposals"]


def _audit(action: str, code: str) -> list[dict]:
    from sqlalchemy import select

    from core import db as dbmod
    from core.models import AuditLog

    with dbmod.session_scope() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == action, AuditLog.entity_id == code)).all()
        return [dict(r.details or {}) for r in rows]


def test_every_proposal_shows_profile_and_evidence(client):
    props = _proposals(client)
    assert props, "CSDL demo cần có đề xuất"
    for p in props:
        assert p["stage"] and "next_appt" in p and "last_visit" in p
        for it in p["items"]:
            assert it["schedule"] and it["n_questions"] > 0
            assert it["flags"], f"{p['code']} {it['code']}: đề xuất không có căn cứ"
            for f in it["flags"]:
                assert f["evidence"]
                for e in f["evidence"]:
                    assert e["label"] and e["text"]
                    if e["source"] == "ai_note":  # câu ghi chú gốc (đã che) + ngày + độ tin cậy
                        assert e.get("date") and e.get("confidence") is not None
                        assert not e["text"].startswith("AI gắn cờ")


def test_confirm_only_codes_seen(client):
    p = _proposals(client)[0]
    seen = p["items"][0]["code"]
    r = client.post(f"/api/form-schedule/proposals/{p['patient_id']}/confirm", json={"codes": [seen], "insulin": False})
    assert r.status_code == 200 and r.json()["confirmed"] == [seen]
    assert any(d.get("confirmed") == [seen] for d in _audit("confirm_module", p["code"]))
    assert all(x["patient_id"] != p["patient_id"] or seen not in x["modules"] for x in _proposals(client))


def test_confirm_stale_code_conflicts(client):
    p = _proposals(client)[0]
    r = client.post(f"/api/form-schedule/proposals/{p['patient_id']}/confirm", json={"codes": ["M-KHONG-CO"]})
    assert r.status_code == 409


def test_decline_needs_reason_and_is_not_proposed_again(client):
    p = _proposals(client)[0]
    code = p["items"][0]["code"]
    url = f"/api/form-schedule/proposals/{p['patient_id']}/decline"
    assert client.post(url, json={"codes": [code], "reason": ""}).status_code == 422
    r = client.post(url, json={"codes": [code], "reason": "Đã gọi khách: không có tăng huyết áp"})
    assert r.status_code == 200, r.text
    assert all(x["patient_id"] != p["patient_id"] or code not in x["modules"] for x in _proposals(client))
    assert any(d.get("reason") == "Đã gọi khách: không có tăng huyết áp" for d in _audit("decline_module", p["code"]))
    # đã từ chối → xác nhận lại mã đó bị chặn (409), phải thêm qua Hồ sơ
    assert client.post(f"/api/form-schedule/proposals/{p['patient_id']}/confirm",
                       json={"codes": [code]}).status_code == 409


@pytest.mark.parametrize("env", [{"APP_ENV": "production", "SYNTHETIC_DATA_ONLY": "1"}, {"SYNTHETIC_DATA_ONLY": "0"}])
def test_fake_note_backfill_refuses_outside_synthetic_demo(env):
    """Ghi chú GIẢ không bao giờ được thêm vào hồ sơ thật để hợp thức hóa cờ thiếu căn cứ."""
    import subprocess
    import sys

    run_env = {**os.environ, "APP_DB_URL": "sqlite:///:memory:", **env}
    r = subprocess.run([sys.executable, "scripts/seed_demo.py", "--backfill-notes"], cwd=ROOT, env=run_env,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode != 0 and "Từ chối" in (r.stderr + r.stdout)
