import socket
import time
from pathlib import Path
from threading import Event, Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.drafting import ModelDraft, prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


def test_application_tab_reviews_regenerates_sends_and_shows_receipt(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
        "drafting_provider": "codex"})
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda _p, _j, _c, _e, custom_prompt="":
        ModelDraft(summary="Python engineer",
                   fit_text=f"I built Python systems at Prior Co. {custom_prompt or 'Initial draft'}"))
    from fastapi.testclient import TestClient
    client = TestClient(app)
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer",
        "description": "Build Python systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(app.state.db, app.state.settings, job["id"], "codex")
    regeneration_started = Event()
    allow_regeneration = Event()

    def regenerate_message(_provider, _prompt, response_type):
        regeneration_started.set()
        assert allow_regeneration.wait(10)
        return response_type.model_validate({"fit": "I built production search systems at Prior Co."})

    monkeypatch.setattr("job_radar.drafting._provider_json", regenerate_message)
    app.state.auto_apply_manager.register_review(draft)
    client.patch(f"/api/applications/{draft['id']}", json={"destination": {"kind": "email", "email": "jobs@example.org"}})
    assert client.post("/api/setup/smtp", json={"host": "smtp.example.org", "port": 587,
        "user": "alex", "password": "secret", "from_address": "alex@example.org"}).status_code == 200
    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings:
        sent.append(item["id"]) or "SMTP accepted message")
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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/#applications/{draft['id']}")
                page.get_by_role("heading", name="Engineer").wait_for(timeout=5000)
                assert page.get_by_role("button", name="Approve & send").is_visible()
                page.get_by_text("Edit resume details", exact=True).click()
                assert page.get_by_text("Edit LaTeX section", exact=True).count() == 0
                item = page.locator('[data-experience-bullets="0"]')
                assert item.input_value().startswith(r"\item ")
                original_bullets = client.get(f"/api/applications/{draft['id']}").json()["resume_data"]["experience"][0]["bullets"]
                page.locator("#draft-summary").fill("Revised Python engineer summary")
                with page.expect_response(lambda response: response.url.endswith(f"/api/applications/{draft['id']}")
                                          and response.request.method == "PATCH") as detail_response:
                    page.get_by_role("button", name="Save changes").click()
                assert detail_response.value.status == 200, detail_response.value.text()
                page.locator("#application-dirty-state").get_by_text("Saved").wait_for()
                assert client.get(f"/api/applications/{draft['id']}").json()["resume_data"]["experience"][0]["bullets"] == original_bullets
                page.get_by_text("Edit resume details", exact=True).click()
                item = page.locator('[data-experience-bullets="0"]')
                item.fill(item.input_value().replace("Python systems", r"\textbf{Python systems}"))
                with page.expect_response(lambda response: response.url.endswith(f"/api/applications/{draft['id']}")
                                          and response.request.method == "PATCH") as saved_response:
                    page.get_by_role("button", name="Save changes").click()
                assert saved_response.value.status == 200, saved_response.value.text()
                page.locator("#application-dirty-state").get_by_text("Saved").wait_for()
                saved = client.get(f"/api/applications/{draft['id']}").json()
                assert r"\textbf{Python systems}" in saved["resume_data"]["experience"][0]["bullets"][0]
                resume_hash = saved["resume_hash"]
                page.locator('#application-review-resume img[alt="Resume page 1"]').wait_for()
                resume_requests = []
                page.on("request", lambda request: resume_requests.append(request.url)
                        if f"/api/applications/{draft['id']}/resume/preview" in request.url else None)
                page.locator("#draft-body").fill("I am interested in this role. Please review my attached resume.")
                with page.expect_response(lambda response: response.url.endswith(f"/api/applications/{draft['id']}")
                                          and response.request.method == "PATCH") as message_response:
                    page.get_by_role("button", name="Save changes").click()
                assert message_response.value.status == 200, message_response.value.text()
                assert set(message_response.value.request.post_data_json) == {"message_data"}
                page.locator("#application-dirty-state").get_by_text("Saved").wait_for()
                assert client.get(f"/api/applications/{draft['id']}").json()["resume_hash"] == resume_hash
                assert not resume_requests
                assert page.get_by_label("Custom instructions").is_visible()
                page.get_by_label("Custom instructions").fill("Emphasize production search")
                page.locator("#regenerate-section").select_option("message")
                page.get_by_role("button", name="Regenerate selected section").click()
                assert regeneration_started.wait(5)
                page.locator(f'[data-application="{draft["id"]}"] .status-badge').get_by_text("Regenerating").wait_for(timeout=5000)
                page.locator("#application-detail .application-review-header .status-badge").get_by_text("Regenerating").wait_for(timeout=5000)
                allow_regeneration.set()
                page.get_by_text("I built production search systems at Prior Co.").wait_for()
                change_note = page.get_by_text("Only this section changed. Untouched sections kept their reviewed content.")
                change_note.wait_for(state="visible")
                assert change_note.is_visible()
                page.get_by_role("button", name="Approve & send").click()
                page.locator("#application-review-overview").get_by_role("heading", name="Application sent").wait_for()
                assert sent == [draft["id"]]
                assert page.locator("#application-review-overview").get_by_text("SMTP accepted message").is_visible()
                overview = page.locator("#application-review-overview")
                assert overview.get_by_text("Review before sending").count() == 0
                assert overview.get_by_text("Sending is blocked").count() == 0
                assert overview.get_by_text("This application has already been sent").count() == 0
                assert page.locator("#send-draft").is_disabled()
                assert page.locator("#send-draft").inner_text() == "Email sent"
                page.reload()
                page.locator("#application-review-overview").get_by_role("heading", name="Application sent").wait_for()
                assert overview.get_by_text("Review before sending").count() == 0
                assert overview.get_by_text("Sending is blocked").count() == 0
                assert sent == [draft["id"]]
            finally:
                browser.close()
    finally:
        allow_regeneration.set()
        server.should_exit = True
        thread.join(timeout=5)
