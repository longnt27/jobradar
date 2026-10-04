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
    pending_id = TestClient(app).post("/api/jobs/import", json={
        "company": "Pending Example", "title": "Python Engineer", "description": "Build Python systems in Hanoi.",
    }).json()["id"]
    app.state.db.execute("UPDATE vacancies SET analysis_status='pending' WHERE id=?", (pending_id,))
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
                page.get_by_role("button", name="Telegram reviews and job alerts").click()
                page.locator("#telegram-panel[open]").wait_for()
                assert page.get_by_role("button", name="Find my chat ID").is_visible()
                page.locator("#edit-profile-button").click()
                assert page.locator("#profile-edit-panel").get_attribute("open") is not None
                page.locator("#open-experience-button").click()
                assert page.locator("#experience-panel").get_attribute("open") is not None
                assert page.locator("#open-experience-button").get_attribute("aria-expanded") == "true"
                assert page.locator("#position-form").is_visible()
                assert page.url.endswith("#profile")
                page.locator("#open-projects-button").click()
                assert page.locator("#projects-panel").get_attribute("open") is not None
                assert page.locator("#open-experience-button").get_attribute("aria-expanded") == "false"
                assert page.locator("#project-add-form").is_visible()
                assert page.url.endswith("#profile")
                page.goto("about:blank")
                page.goto(f"http://127.0.0.1:{port}/#projects")
                page.locator("#projects-panel[open]").wait_for()
                assert page.url.endswith("#profile")
                page.get_by_role("button", name="Applications", exact=True).first.click()
                page.locator("#auto-apply-panel summary").click()
                assert page.locator("#auto-apply-form input[name='enabled']").is_visible()
                assert page.locator("#auto-apply-status").inner_text() == "Off"
                page.get_by_role("button", name="Jobs", exact=True).first.click()
                pending_card = page.get_by_role("button", name="Open Python Engineer at Pending Example")
                assert "Analyzing" in pending_card.inner_text()
                assert "No score" not in pending_card.inner_text()
                assert pending_card.locator(".score-pending").count() == 1
                page.get_by_role("button", name="Open AI Engineer at Example").click()
                page.get_by_role("heading", name="Job at a glance").wait_for()
                assert "Python" in page.locator(".job-facts").inner_text()
                assert "Match breakdown" in page.locator("#job-detail").inner_text()
                assert page.get_by_role("button", name="Open AI Engineer at Example").locator(".score-high").inner_text() == "82"
                high_color = page.get_by_role("button", name="Open AI Engineer at Example").locator(".score-high").evaluate("node => getComputedStyle(node).backgroundColor")
                pending_color = pending_card.locator(".score-pending").evaluate("node => getComputedStyle(node).backgroundColor")
                assert high_color != pending_color
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_failed_matching_jobs_are_visible_and_retryable_from_jobs(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org", "skills": []})
    db.set_setting("matching_model", "test:small")
    monkeypatch.setattr("job_radar.web.list_local_models", lambda: [{"name": "test:small", "size": 123456789}])
    monkeypatch.setattr("job_radar.web.validate_local_model", lambda model: model)
    monkeypatch.setattr("job_radar.matching.analyze_job", lambda *_args: (75, {"method": "local_llm"}))
    client = TestClient(app)
    for number in (1, 2):
        identifier = client.post("/api/jobs/import", json={"company": "Example", "title": f"Engineer {number}",
            "description": "Build Python services."}).json()["id"]
        db.execute("UPDATE vacancies SET analysis_status='failed',analysis_error=? WHERE id=?",
                   ("Model output was cut off", identifier))
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
                page.goto(f"http://127.0.0.1:{port}/#jobs")
                assert page.locator("#matching-overview .needs-attention strong").inner_text() == "2"
                page.locator("#matching-failures").wait_for(state="visible")
                assert "2 jobs need attention" in page.locator("#matching-failures-title").inner_text()
                assert "Engineer 1" in page.locator("#matching-failure-list").inner_text()
                assert "Model output was cut off" in page.locator("#matching-failure-list").inner_text()
                page.get_by_role("button", name="View job").first.click()
                page.get_by_role("heading", name="Engineer 1").wait_for()
                page.locator("#job-query").fill("no matching title")
                page.evaluate("loadJobs()")
                assert page.get_by_role("heading", name="Engineer 1").is_visible()
                page.locator("#matching-retry-all").click()
                page.locator("#matching-failures").wait_for(state="hidden")
                assert db.one("SELECT COUNT(*) AS n FROM vacancies WHERE analysis_status='failed'")["n"] == 0
                page.get_by_role("button", name="My profile", exact=True).first.click()
                page.locator("#matching-panel summary").click()
                assert "need attention" not in page.locator("#matching-status").inner_text()
                assert page.locator("#matching-panel").get_attribute("open") is not None
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
