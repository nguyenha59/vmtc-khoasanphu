"""Opt-in real-browser probes against loopback only, using synthetic records.

Run with RUN_SECURITY_BROWSER=1 to exercise the XSS and CSRF regression checks.
No external pages, credentials or patient data are used.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_security_audit_review import login
from test_security_audit_review import review as review


@pytest.fixture()
def browser():
    if os.environ.get("RUN_SECURITY_BROWSER") != "1":
        pytest.skip("opt in with RUN_SECURITY_BROWSER=1 (local headless browser)")
    sync = pytest.importorskip("playwright.sync_api")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    with sync.sync_playwright() as pw:
        kwargs = {"executable_path": str(edge)} if edge.exists() else {}
        instance = pw.chromium.launch(headless=True, args=[
            "--disable-background-networking", "--no-proxy-server",
            "--host-resolver-rules=MAP *.clinic.test 127.0.0.1",
        ], **kwargs)
        yield instance
        instance.close()


def seed_assessment(review, summary, *, alert=False):
    from core.db import now
    from core.models import Alert, Assessment, FormResponse

    with review.db.session_scope() as s:
        response = FormResponse(patient_id=review.pid, form_code="F6", submitted_at=now(), answers={}, source="demo")
        s.add(response)
        s.flush()
        assessment = Assessment(response_id=response.id, level="red" if alert else "green", rule_level="green",
                                trend_level="green", ai_level="green", summary=summary,
                                rules_version="synthetic-review", created_at=now())
        s.add(assessment)
        s.flush()
        if alert:
            row = Alert(assessment_id=assessment.id, level="red", status="open", created_at=now())
            s.add(row)
            s.flush()
            return row.id
    return None


@contextmanager
def loopback_https(app, tmp_path, hostile_html=None):
    import uvicorn
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from fastapi.responses import HTMLResponse

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic local audit")])
    at = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(at - timedelta(minutes=1))
            .not_valid_after(at + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("staff.clinic.test")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "audit-cert.pem", tmp_path / "audit-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    async def harness(scope, receive, send):
        host = dict(scope.get("headers", [])).get(b"host", b"")
        if hostile_html is not None and host.startswith(b"evil.clinic.test:"):
            await HTMLResponse(hostile_html["html"])(scope, receive, send)
        else:
            await app(scope, receive, send)

    config = uvicorn.Config(harness, host="127.0.0.1", port=port, log_level="critical", access_log=False,
                            lifespan="off", ssl_certfile=str(cert_file), ssl_keyfile=str(key_file))
    server = uvicorn.Server(config)
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 8
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "local HTTPS harness failed to start"
        yield port
    finally:
        server.should_exit = True
        worker.join(timeout=8)
        listener.close()


def test_downloaded_html_executes_only_the_benign_audit_marker(review, browser, monkeypatch):
    marker = '<script>document.documentElement.dataset.auditXss="yes"</script>'
    seed_assessment(review, marker)
    monkeypatch.setattr("core.reports._font", lambda: None)
    login(review)
    response = review.client.get(f"/api/profile/{review.pid}/print")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    artifact = review.tmp_path / "synthetic-report.html"
    artifact.write_bytes(response.content)
    context = browser.new_context()
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort())
    try:
        page = context.new_page()
        page.goto(artifact.as_uri())
        executed = page.evaluate("document.documentElement.dataset.auditXss || null")
    finally:
        context.close()
    assert executed is None


def test_same_site_csrf_can_change_alert_status(review, browser, monkeypatch):
    from core.models import Alert

    alert_id = seed_assessment(review, "Synthetic CSRF review", alert=True)
    monkeypatch.setenv("APP_COOKIE_SECURE", "1")
    cookie = login(review)
    hostile_html = {"html": ""}
    with loopback_https(review.client.app, review.tmp_path, hostile_html) as port:
        staff = f"https://staff.clinic.test:{port}"
        hostile = f"https://evil.clinic.test:{port}/synthetic-attack"
        target = f"{staff}/api/alerts/{alert_id}/escalate"
        hostile_html["html"] = ('<body><script>fetch(' + repr(target)
                + ', {method:"POST",mode:"no-cors",credentials:"include"})'
                + '.then(()=>document.body.dataset.auditDone="yes")'
                + '.catch(e=>document.body.dataset.auditError=String(e))</script></body>')
        context = browser.new_context(ignore_https_errors=True)
        context.add_cookies([{"name": "tl_session", "value": cookie, "url": staff,
                             "httpOnly": True, "secure": True, "sameSite": "Lax"}])
        sent = []

        def only_loopback(route):
            request = route.request
            if urlparse(request.url).hostname in ("staff.clinic.test", "evil.clinic.test"):
                if request.method == "POST":
                    sent.append(request)
                route.continue_()
            else:
                route.abort()

        context.route("**/*", only_loopback)
        try:
            page = context.new_page()
            failures = []
            console = []
            page.on("requestfailed", lambda request: failures.append((request.url, request.failure)))
            page.on("console", lambda message: console.append(message.text) if message.type == "error" else None)
            assert page.goto(f"{staff}/api/auth/me").status == 200
            page.goto(hostile)
            page.wait_for_function("document.body.dataset.auditDone || document.body.dataset.auditError", timeout=10_000)
            assert page.evaluate("document.body.dataset.auditDone") == "yes", (failures, console)
            assert sent and sent[0].headers.get("origin") == f"https://evil.clinic.test:{port}"
        finally:
            context.close()
    with review.db.session_scope() as s:
        assert s.get(Alert, alert_id).status == "open"
