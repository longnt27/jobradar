import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.local_analysis import ANALYSIS_VERSION
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
                page.locator('#home-optional [data-setup-panel="provider-panel"]').click()
                page.locator("#provider-panel[open]").wait_for()
                assert page.locator("#provider-form select").is_visible()
                assert page.locator("#matching-model-form select").is_visible()
                page.get_by_role("button", name="Home", exact=True).click()
                page.locator('#home-optional [data-setup-panel="telegram-panel"]').click()
                page.locator("#telegram-panel[open]").wait_for()
                assert page.get_by_role("button", name="Find my chat ID").is_visible()
                page.get_by_role("button", name="My profile", exact=True).first.click()
                page.locator("#profile.active").wait_for()
                page.locator("#edit-profile-button").click()
                page.locator("#personal.active #profile-form").wait_for()
                assert page.url.endswith("#personal")
                page.locator("#personal [data-tab='profile']").click()
                assert page.locator("#profile-edit-panel").count() == 0
                page.locator("#open-experience-button").click()
                page.locator("#experience.active #position-form").wait_for()
                assert page.locator("#position-form").is_visible()
                assert page.url.endswith("#experience")
                page.locator("#experience [data-tab='profile']").click()
                page.locator("#open-projects-button").click()
                page.locator("#projects.active #project-add-form").wait_for()
                assert page.locator("#project-add-form").is_visible()
                assert page.url.endswith("#projects")
                page.goto("about:blank")
                page.goto(f"http://127.0.0.1:{port}/#projects")
                page.locator("#projects.active #project-add-form").wait_for()
                assert page.url.endswith("#projects")
                page.get_by_role("button", name="Applications", exact=True).first.click()
                page.get_by_role("tab", name="Automation").click()
                assert page.locator("#auto-apply-form input[name='enabled']").is_visible()
                assert page.locator("#auto-apply-status").inner_text() == "Off"
                assert page.locator("#queue-existing-drafts").is_disabled()
                page.get_by_role("button", name="Jobs", exact=True).first.click()
                pending_card = page.get_by_role("button", name="Open Python Engineer at Pending Example")
                pending_card.wait_for()
                assert "Analyzing" in pending_card.inner_text()
                assert "No score" not in pending_card.inner_text()
                assert pending_card.locator(".score-pending").count() == 1
                page.get_by_role("button", name="Open AI Engineer at Example").click()
                page.locator("#job-detail summary").filter(has_text="Full extracted requirements").wait_for()
                page.locator("#job-detail summary").filter(has_text="Full extracted requirements").click()
                assert "Python" in page.locator("#job-detail .fact-grid").inner_text()
                assert "Not stated" in page.locator("#job-detail .decision-basics-grid").inner_text()
                breakdown = page.locator("#job-detail summary").filter(has_text="Detailed match breakdown")
                assert "82/100" in breakdown.inner_text()
                breakdown.click()
                assert "not included in the match score" in page.locator("#job-detail").inner_text().lower()
                assert page.locator("#job-detail .score").count() == 0
                assert page.locator("#job-detail .criterion-weight").count() >= 1
                assert page.get_by_role("button", name="Open AI Engineer at Example").locator(".score-high").inner_text() == "82"
                high_color = page.get_by_role("button", name="Open AI Engineer at Example").locator(".score-high").evaluate("node => getComputedStyle(node).backgroundColor")
                pending_color = pending_card.locator(".score-pending").evaluate("node => getComputedStyle(node).backgroundColor")
                assert high_color != pending_color
                page.get_by_role("button", name="My profile", exact=True).first.click()
                page.locator("#edit-profile-button").click()
                page.locator("#personal.active #profile-form").wait_for()
                page.locator("#profile-form input[name='name']").fill("Alex Example")
                page.locator("#profile-form input[name='email']").fill("alex@example.org")
                page.get_by_role("button", name="Save details").click()
                page.get_by_role("status").filter(has_text="Personal details saved").wait_for()
                assert page.url.endswith("#personal")
                assert app.state.db.get_setting("profile", {})["name"] == "Alex Example"
                page.locator("#personal [data-tab='profile']").click()
                page.locator("#resume-status.status-badge--success").wait_for()
                page.get_by_role("button", name="Settings", exact=True).click()
                page.locator("#smtp-panel summary").click()
                page.locator("#smtp-panel[open]").wait_for()
                page.locator("#smtp-gmail-preset").click()
                page.wait_for_function("document.querySelector('#setup-smtp-form input[name=host]').value === 'smtp.gmail.com'")
                assert page.locator("#setup-smtp-form input[name='host']").input_value() == "smtp.gmail.com"
                assert page.locator("#setup-smtp-form select[name='port']").input_value() == "465"
                assert page.locator("#setup-smtp-form input[name='user']").input_value() == "alex@example.org"
                page.locator("#setup-smtp-form input[name='password']").fill("test-app-password")
                page.locator("#setup-smtp-form button[type='submit']").click()
                page.locator("#setup-smtp-status.status-badge--success").wait_for()
                assert json.loads((tmp_path / "smtp.json").read_text())["host"] == "smtp.gmail.com"
                app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org", "drafting_provider": "codex", "experience": []})
                app.state.db.set_setting("matching_model", "test:small")
                monkeypatch.setattr(app.state.login_manager, "status", lambda: {"sites": [], "connected_sites": ["linkedin", "facebook"], "state": "idle", "error": None, "last_saved_at": None})
                monkeypatch.setattr("job_radar.web.telegram_config", lambda _settings: {"token": "test-token", "chat_id": "123"})
                page.goto("about:blank")
                page.goto(f"http://127.0.0.1:{port}/#settings")
                page.locator("#provider-panel summary").click()
                for selector in ("#provider-status", "#matching-status", "#social-sign-in-status", "#setup-telegram-status"):
                    page.locator(selector).wait_for()
                    page.wait_for_function(
                        "(selector) => { const node = document.querySelector(selector); return node && !node.classList.contains('warning'); }",
                        arg=selector,
                    )
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
    db.set_setting("analysis_version", ANALYSIS_VERSION)
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
                page.locator("#matching-failures").wait_for(state="visible")
                assert "2 jobs need attention" in page.locator("#matching-failures-title").inner_text()
                assert "Engineer 1" in page.locator("#matching-failure-list").inner_text()
                assert "Model output was cut off" in page.locator("#matching-failure-list").inner_text()
                page.locator("#matching-failure-list .matching-failure-row").filter(has_text="Engineer 1").get_by_role("button", name="View job").click()
                page.get_by_role("heading", name="Engineer 1").wait_for()
                page.locator("#job-query").fill("no matching title")
                page.evaluate("loadJobs()")
                assert page.get_by_role("heading", name="Engineer 1").is_visible()
                page.locator("#matching-failure-list .matching-failure-row").filter(has_text="Engineer 1").get_by_role("button", name="Dismiss").click()
                page.wait_for_function("document.querySelectorAll('#matching-failure-list .matching-failure-row').length === 1")
                assert db.one("SELECT analysis_status FROM vacancies WHERE title='Engineer 1'")["analysis_status"] == "dismissed"
                page.locator("#job-detail").get_by_role("button", name="Run match review").wait_for()
                page.locator("#matching-retry-all").click()
                page.locator("#matching-failures").wait_for(state="hidden")
                assert db.one("SELECT COUNT(*) AS n FROM vacancies WHERE analysis_status='failed'")["n"] == 0
                page.locator("#job-analysis-settings").click()
                page.locator("#settings.active").wait_for()
                page.locator("#provider-panel[open]").wait_for()
                assert "need attention" not in page.locator("#matching-status").inner_text()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
