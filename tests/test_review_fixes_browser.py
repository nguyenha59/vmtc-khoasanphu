"""Hồi quy trình duyệt cho F02, F04, Y06 (reports/*-2026-11-16). Dữ liệu giả, HTTPS loopback, chặn mạng ngoài.

Cần bản build web (web/dist) – không có thì bỏ qua."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import urlparse

import pytest
from test_review_fixes_final import add_alert
from test_security_audit_review import login
from test_security_audit_review import review as review  # noqa: F401
from test_security_browser_review import browser as browser  # noqa: F401
from test_security_browser_review import loopback_https


@pytest.fixture()
def seen(record_property):
    def emit(**values):
        record_property("observed", json.dumps(values, ensure_ascii=False, default=str))
    return emit


def open_context(review, browser, base):
    cookie = login(review)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
    context.add_cookies([{"name": "tl_session", "value": cookie, "url": base,
                         "httpOnly": True, "secure": True, "sameSite": "Lax"}])
    context.route("**/*", lambda route: route.continue_()
                  if urlparse(route.request.url).hostname == "staff.clinic.test" else route.abort())
    return context


def app_for_browser():
    from api.main import WEB_DIST, create_app

    if not (WEB_DIST / "index.html").exists():
        pytest.skip("Existing build required; the review does not rebuild application assets")
    return create_app(serve_web=True)


def test_revealing_phone_must_not_erase_unsaved_handling_note(review, browser, seen):
    from playwright.sync_api import expect

    aid = add_alert(review)
    with loopback_https(app_for_browser(), review.tmp_path) as port:
        base = f"https://staff.clinic.test:{port}"
        context = open_context(review, browser, base)
        try:
            page = context.new_page()
            page.goto(f"{base}/canh-bao/{aid}")
            note = page.get_by_label("Ghi xử trí", exact=True)
            note.fill("Synthetic unsaved handling note")
            page.get_by_role("button", name="Hiện số & gọi", exact=True).click()
            expect(page.get_by_role("link", name="Gọi 0900000001", exact=True)).to_be_visible()
            page.wait_for_timeout(500)
            value = note.input_value()
            seen(before="Synthetic unsaved handling note", after=value, action="reveal-phone")
            page.screenshot(path=str(review.tmp_path / "draft-after-reveal.png"), full_page=True)
            assert value == "Synthetic unsaved handling note"
        finally:
            context.close()


def test_switching_cached_profiles_must_not_reuse_another_patients_delivery_date(review, browser, seen):
    from playwright.sync_api import expect

    from core.models import Patient

    recorded_date = (review.today - timedelta(days=2)).isoformat()
    draft_date = (review.today - timedelta(days=5)).isoformat()
    with review.db.session_scope() as s:
        other = s.query(Patient).filter(Patient.id != review.pid).one()
        other.delivery_date = review.today - timedelta(days=2)
        other_code = other.code
    with loopback_https(app_for_browser(), review.tmp_path) as port:
        base = f"https://staff.clinic.test:{port}"
        context = open_context(review, browser, base)
        try:
            page = context.new_page()
            page.goto(f"{base}/ho-so?q={other_code}")
            expect(page.get_by_role("heading", name=other_code, exact=True)).to_be_visible()
            page.get_by_role("tab", name=re.compile("^Sau sinh")).click()
            expect(page.locator('input[type="date"]')).to_have_value(recorded_date)
            search = page.get_by_role("textbox", name="Mã thai phụ", exact=True)
            search.fill(review.code)
            page.get_by_role("button", name="Mở hồ sơ", exact=True).click()
            expect(page.get_by_role("heading", name=review.code, exact=True)).to_be_visible()
            page.get_by_role("tab", name=re.compile("^Sau sinh")).click()
            page.locator('input[type="date"]').fill(draft_date)
            search.fill(other_code)
            page.get_by_role("button", name="Mở hồ sơ", exact=True).click()
            expect(page.get_by_role("heading", name=other_code, exact=True)).to_be_visible()
            page.get_by_role("tab", name=re.compile("^Sau sinh")).click()
            value = page.locator('input[type="date"]').input_value()
            seen(first_patient_draft=draft_date, second_patient_stored_date=recorded_date,
                 second_patient_input=value, current_code=other_code)
            page.screenshot(path=str(review.tmp_path / "cached-profile-date.png"), full_page=True)
            assert value == recorded_date
        finally:
            context.close()


def test_switching_token_in_spa_must_not_submit_previous_patients_text(review, browser, seen):
    from playwright.sync_api import expect
    from sqlalchemy import select

    from core.forms import create_token
    from core.models import FormResponse, Patient
    from core.security import decrypt

    with review.db.session_scope() as s:
        other = s.scalar(select(Patient).where(Patient.id != review.pid))
        other_id, other_code = other.id, other.code
        other_token = create_token(s, other.id, "F6", 7)
    with loopback_https(app_for_browser(), review.tmp_path) as port:
        base = f"https://staff.clinic.test:{port}"
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 1000})
        context.route("**/*", lambda route: route.continue_()
                      if urlparse(route.request.url).hostname == "staff.clinic.test" else route.abort())
        try:
            page = context.new_page()
            page.goto(f"{base}/form?token={review.normal}")
            expect(page.get_by_text(review.code, exact=True)).to_be_visible()
            text = page.get_by_role("textbox", name=re.compile("^Chị muốn nói thêm"))
            original = "Synthetic text belongs to patient A"
            text.fill(original)
            # A React Router location change, as with in-app navigation / browser history.
            page.evaluate("path => { history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate')); }",
                          f"/form?token={other_token}")
            expect(page.get_by_text(other_code, exact=True)).to_be_visible()
            retained = text.input_value()
            saved = None
            if retained:
                with page.expect_response(lambda r: r.request.method == "POST" and other_token in r.url) as posted:
                    page.get_by_role("button", name="Gửi", exact=True).click()
                assert posted.value.status == 200
                with review.db.session_scope() as s:
                    response = s.scalar(select(FormResponse).where(FormResponse.patient_id == other_id))
                    saved = decrypt(response.free_text_enc) if response else None
            seen(case="Y06", navigation="same SPA instance, different token", displayed_code=other_code,
                   previous_patient_text=original, retained_text=retained, saved_for_other_patient=saved)
            assert retained == "" and saved is None, "Switching token retains and submits the preceding patient's free text"
        finally:
            context.close()
