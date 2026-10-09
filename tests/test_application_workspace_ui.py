import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.drafting import prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


def _prepare(client: TestClient, company: str, title: str, apply_url: str | None = None) -> dict:
    job = client.post("/api/jobs/import", json={
        "company": company,
        "title": title,
        "description": "Build reliable Python systems.",
        "apply_url": apply_url,
    }).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    client.app.state.auto_apply_manager.register_review(draft)
    return draft


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
    app.state.db.execute("UPDATE vacancies SET decision_state='shortlisted' WHERE id=?", (second["vacancy_id"],))
    for index in range(26):
        filler = client.post("/api/jobs/import", json={
            "company": "List fixture", "title": f"Engineer {index}",
            "description": "Build reliable Python systems.",
        }).json()
        app.state.db.execute(
            "UPDATE vacancies SET analysis_status='done',score=95 WHERE id=?", (filler["id"],)
        )

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
                auto_apply_requests = []
                page.on("request", lambda request: auto_apply_requests.append(request.url) if "/api/auto-apply?summary_only=true" in request.url else None)
                resume_requests = []
                page.on("request", lambda request: resume_requests.append(request.url) if f"/api/applications/" in request.url and "/resume" in request.url else None)
                page.goto(f"http://127.0.0.1:{port}/#applications")
                page.locator("#applications.active").wait_for()
                page.locator("#application-list [data-application]").first.wait_for()
                assert not auto_apply_requests

                with page.expect_response(lambda response: "/api/auto-apply?summary_only=true" in response.url, timeout=30000):
                    page.get_by_role("tab", name="Automation").click()
                assert page.locator("#auto-apply-form").is_visible()
                assert auto_apply_requests
                assert page.locator("#applications-drafts-view").is_hidden()
                page.get_by_role("tab", name="Activity").click()
                assert page.locator("#auto-apply-activity").is_visible()
                page.get_by_role("tab", name="Drafts").click()

                assert page.locator("#application-list [data-application]").count() == 2
                page.locator("#application-query").fill("Other")
                page.wait_for_function("document.querySelectorAll('#application-list [data-application]').length === 1")
                assert "Other Co" in page.locator("#application-list").inner_text()
                page.locator("#application-query").fill("")
                page.wait_for_function("document.querySelectorAll('#application-list [data-application]').length === 2")

                page.locator("#application-company-filter").select_option(label="Example")
                page.wait_for_function("document.querySelectorAll('#application-list [data-application]').length === 1")
                page.locator("#application-company-filter").select_option("")
                page.wait_for_function("document.querySelectorAll('#application-list [data-application]').length === 2")

                page.locator("#application-review-filter").select_option("needs_review")
                page.wait_for_function("document.querySelectorAll('#application-list [data-application]').length === 1")
                assert "Research Engineer" in page.locator("#application-list").inner_text()
                page.locator("#application-review-filter").select_option("")
                page.wait_for_function("document.querySelectorAll('#application-list [data-application]').length === 2")

                first_card = page.locator(f'[data-application="{first["id"]}"]')
                first_card.click()
                assert first_card.locator(".item-title").inner_text() == "Platform Engineer"
                assert first_card.locator(".application-card-status").inner_text() == "Ready for review"
                assert first_card.get_attribute("aria-pressed") == "true"
                page.wait_for_function("document.activeElement === document.querySelector('#application-detail')")
                assert "is-selected" in (first_card.get_attribute("class") or "")
                assert "Basic template" in page.locator("#application-detail").inner_text()
                assert "local template; no model inference" not in page.locator("#application-detail").inner_text()
                assert "Why this application was prepared" in page.locator("#application-detail").inner_text()
                assert "the saved job" in page.locator("#application-detail").inner_text()
                assert page.get_by_role("button", name="View job in Jobs").is_visible()

                for section in ("Changes & risks", "Resume", "Email", "Regenerate"):
                    assert page.get_by_role("button", name=section, exact=True).is_visible()
                page.get_by_role("button", name="Regenerate", exact=True).click()
                assert page.locator("#regenerate-section option").all_text_contents() == [
                    "Professional summary", "Experience bullets", "Selected projects and bullets",
                        "Education wording", "Achievements", "Skills", "Application experience and project fit", "Full draft · uses more quota",
                ]
                assert page.get_by_role("button", name="Form", exact=True).count() == 0
                preview = page.locator('#application-review-resume img[alt="Resume page 1"]')
                preview.wait_for()
                assert preview.is_visible()
                assert page.locator(".application-cv-details").get_attribute("open") is None
                assert "Claims worth verifying" not in page.locator("#application-detail").inner_text()
                assert "Why these projects were selected" not in page.locator("#application-detail").inner_text()
                page.wait_for_function("document.querySelector('#application-review-resume img')?.naturalWidth > 0")
                assert f"/api/applications/{first['id']}/resume/preview" in preview.get_attribute("src")
                assert resume_requests
                assert all('/resume/preview' in url for url in resume_requests)

                assert page.locator("#application-review-form").is_hidden()
                assert page.locator('[data-answer="0"]').count() == 1
                assert page.locator('[data-attachment="1"]').count() == 1

                send = page.get_by_role("button", name="Approve & send", exact=True)
                assert send.is_enabled()

                subject = page.locator("#draft-subject")
                subject.fill("Updated application subject")
                assert page.locator("#application-dirty-state").inner_text() == "Unsaved changes"
                assert "is-dirty" in (subject.locator("xpath=..").get_attribute("class") or "")
                assert page.get_by_role("button", name="Approve & send", exact=True).is_disabled()
                page.get_by_role("button", name="Save changes", exact=True).click()
                page.wait_for_function("document.querySelector('#application-dirty-state')?.textContent === 'Saved'")
                send = page.get_by_role("button", name="Approve & send", exact=True)
                send.click()
                page.wait_for_function("document.querySelector('#application-dirty-state')?.textContent === 'Sent'")
                assert sent == [first["id"]]

                page.set_viewport_size({"width": 390, "height": 844})
                page.evaluate("window.__applicationDetailScrolled = false; document.querySelector('#application-detail').scrollIntoView = () => { window.__applicationDetailScrolled = true; }")
                page.locator(f'[data-application="{second["id"]}"]').click()
                page.wait_for_function("window.__applicationDetailScrolled === true")
                page.get_by_role("heading", name="Research Engineer").wait_for()
                blocker = page.locator("#application-detail .application-alert--danger")
                blocker.wait_for()
                assert "destination" in blocker.inner_text().lower()
                page.set_viewport_size({"width": 1280, "height": 900})
                page.get_by_role("button", name="View job in Jobs").click()
                page.get_by_role("heading", name="Research Engineer").wait_for()
                assert page.locator("#jobs.active").is_visible()
                assert f"job={second['vacancy_id']}" in page.url
                assert "inbox=all" in page.url
                assert page.get_by_role("tab", name="All jobs").get_attribute("aria-selected") == "true"
                selected_card = page.locator(f'#job-list [data-job-card="{second["vacancy_id"]}"]')
                selected_card.wait_for()
                assert selected_card.get_by_text("Research Engineer").is_visible()
                assert page.locator("#job-list [data-job-card]").count() == 10
                assert page.locator("#job-list [data-job-card]").first.get_attribute("data-job-card") == second["vacancy_id"]
                page.reload()
                selected_card.wait_for()
                assert page.get_by_role("tab", name="All jobs").get_attribute("aria-selected") == "true"
                assert page.locator("#job-list [data-job-card]").count() == 10
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_quota_failure_appears_in_drafts_and_can_be_retried(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org",
                    "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024-2026",
                                    "bullets": ["Built reliable Python systems."]}]})
    client.put("/api/profile", json=profile)
    job = client.post("/api/jobs/import", json={
        "company": "SETA", "title": "AI Engineer", "description": "Build AI systems.",
    }).json()
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=91 WHERE id=?", (job["id"],))
    app.state.db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,detail,requested_by,requested_provider,created_at,updated_at) "
        "VALUES(?,'needs_review',NULL,?,'automation','template','2026-10-08T00:00:00Z','2026-10-08T00:00:00Z')",
        (job["id"], "Automatic preparation stopped: codex drafting failed: workspace is out of credits"),
    )

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
                page.set_default_timeout(20000)
                page.goto(f"http://127.0.0.1:{port}/#applications")
                issue = page.locator(f'[data-preparation="{job["id"]}"]')
                issue.wait_for()
                assert "Out of credits" in issue.inner_text()
                issue.click()
                detail = page.locator("#application-detail")
                assert "No draft was created" in detail.inner_text()
                detail.get_by_role("button", name="Retry preparation").click()
                confirmation = page.locator("#job-prepare-confirm-dialog")
                confirmation.wait_for(state="visible")
                confirmation.get_by_role("button", name="Prepare anyway").click()
                page.locator(f'[data-application] .item-title', has_text="AI Engineer").wait_for()
                assert app.state.db.one("SELECT id FROM application_drafts WHERE vacancy_id=?", (job["id"],))
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
    assert "applicationAlert('danger', 'Sending is blocked'" in js
    assert "applicationAlert('warning', 'Review before sending'" in js
    assert "application-debug" in js
    assert "providerLabel(draft.provider_mode" in js
    assert "renderApplicationFormField" in js
    assert "application-attachment-upload" in js
    assert "Resume page" in js
    assert "const approved = await confirmApplicationSend(draft)" not in js
    assert "What changed and what needs attention" in js
    assert "Telegram review delivery" in js
    assert "Regenerate selected section" in js
    assert 'id="edit-draft"' not in js

    assert ".application-card.is-selected" in css
    assert ".application-review-nav" in css
    assert ".application-sticky-actions" in css
    assert ".application-resume-preview img" in css
    assert ".application-attachment-upload[hidden]" in css
    assert "label.is-dirty::after" in css
    assert ".confirm-dialog" in css
