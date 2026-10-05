import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def _prepare(client: TestClient, company: str, title: str, apply_url: str | None = None) -> dict:
    job = client.post("/api/jobs/import", json={
        "company": company,
        "title": title,
        "description": "Build reliable Python systems.",
        "apply_url": apply_url,
    }).json()
    response = client.post(f"/api/jobs/{job['id']}/prepare", json={"provider": "template"})
    assert response.status_code == 200, response.text
    return response.json()


def test_application_workspace_filters_reviews_and_confirms_send(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example",
        "email": "alex@example.org",
        "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024-2026",
                        "bullets": ["Built Python systems."]}],
    })
    client.put("/api/profile", json=profile)

    first = _prepare(client, "Example", "Platform Engineer", "https://example.org/apply")
    second = _prepare(client, "Other Co", "Research Engineer")

    first_form = {
        "fields": [
            {"index": 0, "name": "authorization", "id": "authorization", "type": "select",
             "required": True, "label": "Work authorization",
             "options": [{"value": "yes", "text": "Yes"}, {"value": "no", "text": "No"}],
             "accept": "", "max_length": None},
            {"index": 1, "name": "resume", "id": "resume", "type": "file",
             "required": False, "label": "Resume document", "options": [],
             "accept": "application/pdf", "max_length": None},
        ],
        "answers": {"0": "yes"},
        "attachments": {},
    }
    first = client.patch(f"/api/applications/{first['id']}", json={
        "destination": {"kind": "email", "email": "jobs@example.org", "action_type": "email",
                        "provenance": "manual_override", "confidence": "user_confirmed"},
        "form_data": first_form,
    }).json()
    app.state.db.execute(
        "UPDATE auto_application_attempts SET status='awaiting_review',review_hash=? WHERE draft_id=?",
        (first["package_hash"], first["id"]),
    )
    app.state.db.execute(
        "UPDATE auto_application_attempts SET status='needs_review' WHERE draft_id=?",
        (second["id"],),
    )
    assert client.post("/api/setup/smtp", json={
        "host": "smtp.example.org", "port": 587, "user": "alex",
        "password": "secret", "from_address": "alex@example.org",
    }).status_code == 200
    assert client.get(f"/api/applications/{first['id']}").json()["send_ready"] is True

    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings: sent.append(item["id"]) or "accepted")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.set_default_timeout(5000)
                page.goto(f"http://127.0.0.1:{port}/#applications")
                page.locator("#applications.active").wait_for()

                page.get_by_role("tab", name="Automation").click()
                assert page.locator("#auto-apply-form").is_visible()
                assert page.locator("#applications-drafts-view").is_hidden()
                page.get_by_role("tab", name="Activity").click()
                assert page.locator("#auto-apply-activity").is_visible()
                page.get_by_role("tab", name="Drafts").click()

                assert page.locator("#application-list [data-application]").count() == 2
                page.locator("#application-query").fill("Other")
                assert page.locator("#application-list [data-application]").count() == 1
                assert "Other Co" in page.locator("#application-list").inner_text()
                page.locator("#application-query").fill("")

                page.locator("#application-company-filter").select_option(label="Example")
                assert page.locator("#application-list [data-application]").count() == 1
                page.locator("#application-company-filter").select_option("")

                page.locator("#application-review-filter").select_option("needs_review")
                assert page.locator("#application-list [data-application]").count() == 1
                assert "Research Engineer" in page.locator("#application-list").inner_text()
                page.locator("#application-review-filter").select_option("")

                first_card = page.locator(f'[data-application="{first["id"]}"]')
                first_card.click()
                assert first_card.get_attribute("aria-pressed") == "true"
                assert page.locator("#application-detail").evaluate("node => document.activeElement === node")
                assert "is-selected" in (first_card.get_attribute("class") or "")
                assert "Basic template" in page.locator("#application-detail").inner_text()
                assert "local template; no model inference" not in page.locator("#application-detail").inner_text()

                for section in ("Overview", "Resume", "Message", "Form", "Regenerate"):
                    assert page.get_by_role("button", name=section, exact=True).is_visible()
                preview = page.locator('#application-review-resume iframe[title="Resume PDF preview"]')
                assert preview.is_visible()
                assert f"/api/applications/{first['id']}/resume" in preview.get_attribute("src")

                choice = page.locator('[data-answer="0"]')
                assert choice.evaluate("node => node.tagName") == "SELECT"
                assert choice.input_value() == "yes"

                upload = page.locator('[data-attachment-upload="1"]')
                assert upload.is_hidden()
                page.locator('[data-attachment="1"]').select_option("uploaded")
                assert upload.is_visible()
                page.locator('[data-attachment="1"]').select_option("resume")
                assert upload.is_hidden()

                send = page.get_by_role("button", name="Approve & send", exact=True)
                page.get_by_role("button", name="Save changes", exact=True).click()
                page.wait_for_function("document.querySelector('#application-dirty-state')?.textContent === 'Saved'")
                assert send.is_enabled()

                send.click()
                dialog = page.locator("#application-send-confirm")
                dialog.wait_for(state="visible")
                assert "jobs@example.org" in page.locator("#application-send-confirm-target").inner_text()
                assert "Basic template" in page.locator("#application-send-confirm-summary").inner_text()
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert sent == []

                subject = page.locator("#draft-subject")
                subject.fill("Updated application subject")
                assert page.locator("#application-dirty-state").inner_text() == "Unsaved changes"
                assert "is-dirty" in (subject.locator("xpath=..").get_attribute("class") or "")
                assert page.get_by_role("button", name="Save changes", exact=True).is_enabled()
                assert page.get_by_role("button", name="Approve & send", exact=True).is_disabled()
                page.get_by_role("button", name="Save changes", exact=True).click()
                page.wait_for_function("document.querySelector('#application-dirty-state')?.textContent === 'Saved'")

                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate("window.__applicationDetailScrolled = false; document.querySelector('#application-detail').scrollIntoView = () => { window.__applicationDetailScrolled = true; }")
                page.locator(f'[data-application="{second["id"]}"]').click()
                page.wait_for_function("window.__applicationDetailScrolled === true")
                blocker = page.locator(".application-alert--danger")
                blocker.wait_for()
                assert "destination" in blocker.inner_text().lower()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_application_workspace_static_contract() -> None:
    static = Path(__file__).parents[1] / "job_radar" / "static"
    html = (static / "index.html").read_text()
    css = (static / "app.css").read_text()
    js = (static / "app.js").read_text()

    for label in ("Drafts", "Automation", "Activity"):
        assert f'data-app-view="{label.lower()}"' in html
    for control in ("application-query", "application-review-filter", "application-company-filter", "application-sent-filter"):
        assert f'id="{control}"' in html

    assert "application-card surface-action" in js
    assert 'aria-pressed="' in js
    assert 'role="region" aria-label="Application details" tabindex="-1"' in html
    assert "$('#application-detail').focus({preventScroll:true})" in js
    assert "scrollNodeIntoView($('#application-detail')" in js
    assert "application-review-nav" in js
    assert "application-sticky-actions" in js
    assert "application-dirty-state" in js
    assert "application-alert--danger" in js
    assert "application-alert--warning" in js
    assert "application-debug" in js
    assert "providerLabel(draft.provider_mode" in js
    assert "renderApplicationFormField" in js
    assert "application-attachment-upload" in js
    assert "Resume PDF preview" in js
    assert "confirmApplicationSend(draft)" in js
    assert 'id="edit-draft"' not in js

    assert ".application-card.is-selected" in css
    assert ".application-review-nav" in css
    assert ".application-sticky-actions" in css
    assert ".application-resume-preview iframe" in css
    assert ".application-attachment-upload[hidden]" in css
    assert "label.is-dirty::after" in css
    assert ".confirm-dialog" in css
