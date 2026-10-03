import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_matching_and_telegram_are_visible_setup_steps_and_facts_render(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    monkeypatch.setattr("job_radar.web.list_local_models", lambda: [{"name": "test:small", "size": 123456789}])
    monkeypatch.setattr("job_radar.web.validate_local_model", lambda model: model)
    from fastapi.testclient import TestClient
    identifier = TestClient(app).post("/api/jobs/import", json={
        "company": "Example", "title": "AI Engineer", "description": "Build Python models in Hanoi.",
    }).json()["id"]
    app.state.db.execute(
        "UPDATE vacancies SET score=82,analysis_status='done',analysis_model='test:small',score_detail=? WHERE id=?",
        (json.dumps({"method": "local_llm", "facts": {"role": "AI Engineer", "seniority": "",
            "required_skills": ["Python"], "preferred_skills": [], "years_required": None,
            "location": "Hanoi", "work_mode": "", "responsibilities": ["Build models"],
            "education": [], "languages": [], "summary": "Builds AI models."},
            "criteria": {"role": {"score": 9, "reason": "Direct fit"}}, "explanation": "Relevant position."}), identifier),
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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{port}/#home")
                page.get_by_role("button", name="Choose a local matching model").click()
                page.locator("#matching-panel[open]").wait_for()
                assert page.locator("#matching-model-form select").is_visible()
                page.get_by_role("button", name="Home", exact=True).click()
                page.get_by_role("button", name="Telegram job alerts").click()
                page.locator("#telegram-panel[open]").wait_for()
                assert page.get_by_role("button", name="Find my chat ID").is_visible()
                page.get_by_role("button", name="Jobs", exact=True).first.click()
                page.get_by_role("button", name="Open AI Engineer at Example").click()
                page.get_by_role("heading", name="Job at a glance").wait_for()
                assert "Python" in page.locator(".job-facts").inner_text()
                assert "Match breakdown" in page.locator("#job-detail").inner_text()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
