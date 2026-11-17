"""Synthetic workflow regressions: validation, token expiry and alert state changes."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
import requests
from sqlalchemy import func, select
from test_security_audit_review import login
from test_security_audit_review import review as review


@pytest.mark.parametrize("headers", [
    {"Origin": "http://foreign.testserver"},
    {"Origin": "null"},
    {"Origin": "http://testserver:9000"},
    {"Origin": "https://testserver"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_staff_mutations_reject_foreign_origins(review, headers):
    login(review)
    response = review.client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 403
    assert review.client.get("/api/auth/me").status_code == 200


def test_same_origin_staff_mutations_remain_available(review):
    login(review)
    assert review.client.post("/api/auth/logout", headers={"Origin": "http://testserver:80"}).status_code == 200
    assert review.client.get("/api/auth/me").status_code == 401


def test_empty_form_does_not_call_ai_or_consume_token(review, monkeypatch):
    from core.models import FormResponse, FormToken

    def forbidden(*args, **kwargs):
        pytest.fail("An empty form must not initialize a provider")

    monkeypatch.setattr("api.routers.public_form.make_client_safe", forbidden)
    path = f"/api/public/form/{review.normal}"
    for body in ({"answers": {}}, {"free_text": " \n\t "}, {"answers": {"unknown": "Có"}}):
        assert review.client.post(path, json=body).status_code == 422
    with review.db.session_scope() as s:
        assert s.get(FormToken, review.normal).used_at is None
        assert s.scalar(select(func.count(FormResponse.id))) == 0
    assert review.client.get(path).status_code == 200


@pytest.mark.parametrize("bad", [True, 1, [], {}, "invalid"])
def test_malformed_symptom_answer_is_not_silently_omitted(review, bad):
    path = f"/api/public/form/{review.normal}"
    assert "C1" in {q["key"] for q in review.client.get(path).json()["questions"]}
    response = review.client.post(path, json={"answers": {"C1": bad}, "free_text": "Em thay khoe"})
    assert response.status_code == 422
    assert review.client.get(path).status_code == 200


@pytest.mark.parametrize("bad", [True, "120", [], {}, 10 ** 400])
def test_numeric_answers_require_numbers_not_booleans(review, bad):
    from core.forms import create_token

    with review.db.session_scope() as s:
        token = create_token(s, review.pid, "M-THA", 7)
    path = f"/api/public/form/{token}"
    assert "BP_AM_SYS" in {q["key"] for q in review.client.get(path).json()["questions"]}
    response = review.client.post(path, json={"answers": {"BP_AM_SYS": bad}, "free_text": "Em thay khoe"})
    assert response.status_code == 422


def test_token_expiring_during_assessment_cannot_be_consumed(review, monkeypatch):
    from core import alerts
    from core.db import now
    from core.models import FormResponse, FormToken

    original = alerts.assess_answers

    def expire_then_assess(*args, **kwargs):
        with review.db.session_scope() as s:
            s.get(FormToken, review.normal).expires_at = now() - timedelta(seconds=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(alerts, "assess_answers", expire_then_assess)
    response = review.client.post(f"/api/public/form/{review.normal}", json={"free_text": "Em thay khoe"})
    assert response.status_code == 409
    with review.db.session_scope() as s:
        assert s.get(FormToken, review.normal).used_at is None
        assert s.scalar(select(func.count(FormResponse.id))) == 0


@pytest.mark.parametrize("endpoint", ["tags", "generate", "embed"])
def test_ollama_redirects_are_rejected_at_both_endpoints(monkeypatch, endpoint):
    from core.ai.llm_client import LlmError, OllamaClient

    seen = []

    def transport(adapter, request, **kwargs):
        seen.append(request.url)
        response = requests.Response()
        response.request, response.url, response.status_code = request, request.url, 200
        if request.url.endswith(f"/api/{endpoint}"):
            response.status_code = 307
            response.headers["Location"] = "https://egress.example.invalid/collect"
            response._content = b"{}"
        else:
            response._content = json.dumps({"models": [{"name": "synthetic-local"}]}).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)
    with pytest.raises(LlmError, match="Ollama"):
        if endpoint == "embed":
            from core.ai.reader.semantic import OllamaEmbedder

            OllamaEmbedder("synthetic-local", url="http://127.0.0.1:11434").embed(["synthetic-private-note"])
        else:
            client = OllamaClient("synthetic-local", url="http://127.0.0.1:11434")
            client.generate_json("synthetic-private-note", {"type": "object"}, 0, 1)
    assert seen and all(url.startswith("http://127.0.0.1:11434/") for url in seen)


def test_embedding_model_metadata_cannot_redirect_processing_to_cloud(monkeypatch):
    from core.ai.llm_client import LlmError
    from core.ai.reader.semantic import OllamaEmbedder

    seen = []

    def transport(adapter, request, **kwargs):
        seen.append(request.url)
        response = requests.Response()
        response.request, response.url, response.status_code = request, request.url, 200
        response._content = json.dumps({"models": [{"name": "synthetic-local", "remote_host": "https://ollama.com"}]}).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)
    with pytest.raises(LlmError, match="cục bộ"):
        OllamaEmbedder("synthetic-local", url="http://127.0.0.1:11434").embed(["synthetic-private-note"])
    assert seen == ["http://127.0.0.1:11434/api/tags"]


def test_closed_alert_cannot_be_reopened_by_saving_a_call(review):
    from core.db import now
    from core.models import Alert, Assessment, FormResponse

    with review.db.session_scope() as s:
        response = FormResponse(patient_id=review.pid, form_code="F6", submitted_at=now(), answers={}, source="demo")
        s.add(response)
        s.flush()
        assessment = Assessment(response_id=response.id, level="yellow", rule_level="yellow", trend_level="green",
                                ai_level="green", summary="Synthetic", rules_version="synthetic", created_at=now())
        s.add(assessment)
        s.flush()
        alert = Alert(assessment_id=assessment.id, level="yellow", status="closed", created_at=now())
        s.add(alert)
        s.flush()
        aid = alert.id
    login(review)
    assert review.client.post(f"/api/alerts/{aid}/handle", json={"close": False, "note": "Synthetic"}).status_code == 409
    with review.db.session_scope() as s:
        assert s.get(Alert, aid).status == "closed"


def test_yellow_digest_does_not_send_closed_alerts(review):
    from core.alerts import send_yellow_digest
    from core.db import now
    from core.models import Alert, Assessment, FormResponse

    class Channel:
        def send_digest(self, payloads):
            pytest.fail("A closed alert must not be sent")

    with review.db.session_scope() as s:
        response = FormResponse(patient_id=review.pid, form_code="F6", submitted_at=now(), answers={}, source="demo")
        s.add(response)
        s.flush()
        assessment = Assessment(response_id=response.id, level="yellow", rule_level="yellow", trend_level="green",
                                ai_level="green", summary="Synthetic", rules_version="synthetic", created_at=now())
        s.add(assessment)
        s.flush()
        alert = Alert(assessment_id=assessment.id, level="yellow", status="closed", created_at=now())
        s.add(alert)
        s.flush()
        assert send_yellow_digest(s, Channel()) == 0
        assert alert.sent_at is None
