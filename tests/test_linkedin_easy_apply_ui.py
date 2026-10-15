import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
import pytest
from fastapi.testclient import TestClient
from playwright.async_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from job_radar.drafting import get_draft, prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


@pytest.mark.parametrize("kind", ["web", "linkedin_easy_apply"])
@pytest.mark.parametrize("error_type", [ValueError, PlaywrightError])
def test_collection_failure_replaces_old_inspection_instruction(tmp_path: Path, monkeypatch, kind, error_type) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("profile", {
        "name": "Alex Example", "email": "alex@example.org", "drafting_provider": "template",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built systems."]}],
    })
    client = TestClient(app)
    job = client.post("/api/jobs/import", json={"title": "AI Engineer", "company": "FiinGroup", "description": "Build AI systems."}).json()
    draft = prepare_draft(db, app.state.settings, job["id"], "template")
    app.state.auto_apply_manager.register_review(draft)
    db.execute("UPDATE auto_application_attempts SET status='awaiting_review' WHERE draft_id=?", (draft["id"],))
    url = "https://www.linkedin.com/jobs/view/123/" if kind == "linkedin_easy_apply" else "https://example.org/apply"
    db.execute("UPDATE application_drafts SET destination=? WHERE id=?", (json.dumps({"kind": kind, "url": url}), draft["id"]))
    endpoint = f"/api/applications/{draft['id']}"
    loading = "LinkedIn application questions have not finished loading." if kind == "linkedin_easy_apply" else "Application questions have not finished loading."
    assert client.get(endpoint).json()["send_blockers"] == [loading]

    async def fail_collection(*_args):
        raise error_type("Application page loading failed.")

    monkeypatch.setattr("job_radar.web.inspect_form", fail_collection)
    assert client.post(endpoint + "/inspect").status_code == 422
    saved = client.get(endpoint).json()
    assert saved["send_blockers"] == ["Application page loading failed."]
    assert not saved["send_ready"]
    assert saved["review_status"] == "needs_review"
    assert saved["preparation_detail"] == "Application page loading failed."

    async def successful_collection(*_args):
        form = {"kind": kind, "complete": True, "signature": "all-pages", "destination_url": url,
                "fields": [], "answers": {}, "attachments": {}, "inspection_blockers": []}
        db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft["id"]))
        return get_draft(db, draft["id"])

    monkeypatch.setattr("job_radar.web.inspect_form", successful_collection)
    assert client.post(endpoint + "/inspect").status_code == 200
    saved = client.get(endpoint).json()
    assert saved["send_ready"]
    assert saved["send_blockers"] == []
    assert saved["review_status"] == "awaiting_review"
    assert saved["preparation_detail"] == "Review the complete application before approving."


@pytest.mark.parametrize("kind", ["web", "linkedin_easy_apply"])
def test_questions_load_automatically_and_save_without_reinspection(tmp_path: Path, monkeypatch, kind) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    db.set_setting("profile", {
        "name": "Alex Example", "email": "alex@example.org", "drafting_provider": "template",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built systems."]}],
    })
    client = TestClient(app)
    job = client.post("/api/jobs/import", json={
        "title": "AI Engineer", "company": "FiinGroup", "description": "Build AI systems.",
    }).json()
    draft = prepare_draft(db, app.state.settings, job["id"], "template")
    app.state.auto_apply_manager.register_review(draft)
    db.execute("UPDATE auto_application_attempts SET detail='ChatGPT drafting failed earlier' WHERE draft_id=?", (draft["id"],))
    url = "https://www.linkedin.com/jobs/view/123/" if kind == "linkedin_easy_apply" else "https://example.org/apply"
    db.execute("UPDATE application_drafts SET destination=? WHERE id=?", (json.dumps({
        "kind": kind, "url": url,
    }), draft["id"]))
    calls = []

    async def collect(_db, _settings, draft_id):
        calls.append(draft_id)
        form = {
            "kind": kind, "steps_total": 4,
            "signature": "all-pages", "destination_url": url,
            "fields": [{"index": 0, "position": 0, "step": 3, "type": "text", "label": "Years of experience",
                        "name": "years", "required": True, "options": []}],
            "answers": {"0": ""}, "attachments": {},
            "inspection_blockers": ["Answer required: Years of experience"] if kind == "linkedin_easy_apply" else [],
        }
        if kind == "linkedin_easy_apply":
            form["complete"] = True
        db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft_id))
        return get_draft(db, draft_id)

    monkeypatch.setattr("job_radar.web.inspect_form", collect)
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
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/#applications/{draft['id']}")
                page.locator("#btn-next-to-package").click()
                answer = page.locator('[data-answer="0"]')
                answer.wait_for()
                assert answer.input_value() == ""
                if kind == "linkedin_easy_apply":
                    assert page.locator("#inspect-form-inline").count() == 0
                assert page.get_by_role("button", name="Approve & send").is_disabled()
                answer.fill("2")
                with page.expect_response(lambda response: response.request.method == "PATCH"):
                    page.get_by_role("button", name="Save changes").click()
                page.wait_for_function("!document.querySelector('#send-draft').disabled")
                saved = client.get(f"/api/applications/{draft['id']}").json()
                assert saved["send_ready"]
                assert saved["form_data"]["answers"]["0"] == "2"
                assert saved["review_status"] == "needs_review"
                assert "ChatGPT drafting failed earlier" in saved["preparation_detail"]
                assert calls == [draft["id"]]
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
