import socket
import time
from pathlib import Path
from threading import Event, Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.drafting import ModelDraft, prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


def test_application_tab_reviews_regenerates_and_deep_links_to_draft(tmp_path: Path, monkeypatch) -> None:
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
            finally:
                browser.close()
    finally:
        allow_regeneration.set()
        server.should_exit = True
        thread.join(timeout=5)
