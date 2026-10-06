import socket
import time
from pathlib import Path
from threading import Thread

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
        ModelDraft(summary="Python engineer", email_subject="Engineer application",
                   email_body=f"Dear team. {custom_prompt or 'Initial draft'}"))
    from fastapi.testclient import TestClient
    client = TestClient(app)
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer",
        "description": "Build Python systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(app.state.db, app.state.settings, job["id"], "codex")
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
                assert page.get_by_label("Custom instructions for regeneration").is_visible()
                page.get_by_label("Custom instructions for regeneration").fill("Emphasize production search")
                page.get_by_role("button", name="Regenerate draft").click()
                page.get_by_text("Dear team. Emphasize production search").wait_for()
                assert page.get_by_role("button", name="Approve & send").is_disabled()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
