import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.drafting import get_draft, prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


def test_collection_failure_replaces_old_inspection_instruction(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("profile", {
        "name": "Alex Example", "email": "alex@example.org", "drafting_provider": "template",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built systems."]}],
    })
    client = TestClient(app)
    job = client.post("/api/jobs/import", json={"title": "AI Engineer", "company": "FiinGroup", "description": "Build AI systems."}).json()
    draft = prepare_draft(db, app.state.settings, job["id"], "template")
    url = "https://www.linkedin.com/jobs/view/123/"
    db.execute("UPDATE application_drafts SET destination=? WHERE id=?", (json.dumps({"kind": "linkedin_easy_apply", "url": url}), draft["id"]))
    endpoint = f"/api/applications/{draft['id']}"
    assert client.get(endpoint).json()["send_blockers"] == ["LinkedIn application questions have not finished loading."]

    async def fail_collection(*_args):
        raise ValueError("This LinkedIn posting is no longer accepting applications.")

    monkeypatch.setattr("job_radar.web.inspect_form", fail_collection)
    assert client.post(endpoint + "/inspect").status_code == 422
    saved = client.get(endpoint).json()
    assert saved["send_blockers"] == ["This LinkedIn posting is no longer accepting applications."]
    assert not saved["send_ready"]

    async def successful_collection(*_args):
        form = {"kind": "linkedin_easy_apply", "complete": True, "signature": "all-pages", "destination_url": url,
                "fields": [], "answers": {}, "attachments": {}, "inspection_blockers": []}
        db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft["id"]))
        return get_draft(db, draft["id"])

    monkeypatch.setattr("job_radar.web.inspect_form", successful_collection)
    assert client.post(endpoint + "/inspect").status_code == 200
    saved = client.get(endpoint).json()
    assert saved["send_ready"]
    assert saved["send_blockers"] == []


def test_linkedin_questions_load_automatically_and_save_without_reinspection(tmp_path: Path, monkeypatch) -> None:
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
    db.execute("UPDATE application_drafts SET destination=? WHERE id=?", (json.dumps({
        "kind": "linkedin_easy_apply", "url": "https://www.linkedin.com/jobs/view/123/",
    }), draft["id"]))
    calls = []

    async def collect(_db, _settings, draft_id):
        calls.append(draft_id)
        form = {
            "kind": "linkedin_easy_apply", "complete": True, "steps_total": 4,
            "signature": "all-pages", "destination_url": "https://www.linkedin.com/jobs/view/123/",
            "fields": [{"index": 0, "position": 0, "step": 3, "type": "text", "label": "Years of experience",
                        "name": "years", "required": True, "options": []}],
            "answers": {"0": ""}, "attachments": {}, "inspection_blockers": ["Answer required: Years of experience"],
        }
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
                assert page.locator("#inspect-form-inline").count() == 0
                assert page.get_by_role("button", name="Approve & send").is_disabled()
                answer.fill("2")
                with page.expect_response(lambda response: response.request.method == "PATCH"):
                    page.get_by_role("button", name="Save changes").click()
                page.wait_for_function("!document.querySelector('#send-draft').disabled")
                saved = client.get(f"/api/applications/{draft['id']}").json()
                assert saved["form_data"]["complete"] and saved["send_ready"]
                assert saved["form_data"]["answers"]["0"] == "2"
                assert calls == [draft["id"]]
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
