import asyncio
import json
import subprocess
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from job_radar.chatgpt_handoff import (
    ChatGPTInputManager,
    application_prompt,
    apply_chatgpt_reply,
    clean_reply_text,
)
from job_radar.drafting import prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


def test_chatgpt_input_uses_selected_application_and_does_not_change_it(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={"company": "Prior Co", "role": "Engineer",
        "dates": "2024–2026", "bullets": ["Built Python search systems."]}).status_code == 201
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Search Engineer",
        "description": "Build Python search systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    before = client.get(f"/api/applications/{draft['id']}").json()
    captured = []

    async def fake_enter(prompt):
        captured.append(prompt)
        return {"status": "entered", "detail": "Prompt entered in ChatGPT."}

    monkeypatch.setattr(client.app.state.chatgpt_input, "enter", fake_enter)
    response = client.post(f"/api/applications/{draft['id']}/chatgpt-input", json={
        "section": "message", "instruction": "Focus on Python search experience"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "entered"
    assert "Search Engineer" in captured[0]
    assert "Built Python search systems" in captured[0]
    assert "Focus on Python search experience" in captured[0]
    after = client.get(f"/api/applications/{draft['id']}").json()
    assert after["package_hash"] == before["package_hash"]
    assert after["message_data"] == before["message_data"]


def test_chatgpt_input_fills_and_submits_composer_without_reading_reply(tmp_path: Path) -> None:
    async def scenario():
        manager = ChatGPTInputManager(Settings(tmp_path), asyncio.Lock())
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("""<div id='prompt-textarea' role='textbox' contenteditable='true'></div>
                    <script>window.submitted = ''; document.querySelector('#prompt-textarea').addEventListener('keydown', e => {
                    if (e.key === 'Enter') { window.submitted = e.target.textContent; e.preventDefault(); }
                    });</script>""")

                async def fake_new_page():
                    return page

                manager._new_page = fake_new_page
                result = await manager.enter("Draft this application")
                assert result["status"] == "entered"
                assert await page.evaluate("window.submitted") == "Draft this application"
            finally:
                await browser.close()

    asyncio.run(scenario())


def test_chatgpt_login_button_opens_shared_browser_profile(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    manager = app.state.chatgpt_input
    assert manager.settings.browser_profile == app.state.settings.browser_profile
    assert manager.browser_lock is app.state.scan_manager.browser_lock

    async def fake_open_login():
        return {"status": "opened", "detail": "ChatGPT opened"}

    monkeypatch.setattr(manager, "open_login", fake_open_login)
    response = client.post("/api/chatgpt/login")
    assert response.status_code == 200
    assert response.json()["status"] == "opened"


def test_chatgpt_login_uses_normal_chrome_until_sign_in_is_complete(tmp_path: Path, monkeypatch) -> None:
    class FakeProcess:
        def __init__(self, args, **_kwargs):
            self.args = args
            self.finished = threading.Event()

        def poll(self):
            return 0 if self.finished.is_set() else None

        def wait(self, timeout=None):
            if not self.finished.wait(timeout):
                raise subprocess.TimeoutExpired(self.args, timeout)
            return 0

        def terminate(self):
            self.finished.set()

        def kill(self):
            self.finished.set()

    processes = []

    def fake_popen(args, **kwargs):
        process = FakeProcess(args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("job_radar.chatgpt_handoff.chrome_executable", lambda: "/fake/chrome")
    monkeypatch.setattr("job_radar.chatgpt_handoff.subprocess.Popen", fake_popen)

    async def scenario():
        browser_lock = asyncio.Lock()
        manager = ChatGPTInputManager(Settings(tmp_path), browser_lock)
        result = await manager.open_login()
        assert result["status"] == "opened"
        assert len(processes) == 1
        args = processes[0].args
        assert f"--user-data-dir={manager.settings.browser_profile}" in args
        assert any(arg.startswith("--remote-debugging-port=") for arg in args)
        assert not any(arg.startswith(("--enable-automation", "--remote-debugging-pipe", "--no-sandbox")) for arg in args)
        assert manager.playwright is None
        assert browser_lock.locked()
        await manager.stop()
        assert not browser_lock.locked()

    asyncio.run(scenario())


def test_settings_select_chatgpt_web_without_calling_cli_for_new_draft(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("job_radar.web.chrome_executable", lambda: "/fake/chrome")
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org",
                    "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024–2026",
                                    "bullets": ["Built Python search systems."]}]})
    assert client.put("/api/profile", json=profile).status_code == 200
    app.state.db.set_setting("auto_apply", {"enabled": True})
    saved = client.put("/api/profile/provider", json={"provider": "chatgpt_web"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["automatic_drafts_paused"] is True
    assert app.state.auto_apply_manager.config()["enabled"] is False
    setup = client.get("/api/setup").json()
    assert setup["selected_provider"] == "chatgpt_web"
    assert setup["capabilities"]["automatic_drafts"]["ready"] is False

    def no_cli(*_args, **_kwargs):
        raise AssertionError("A CLI model must not run in ChatGPT Web mode")

    monkeypatch.setattr("job_radar.drafting._run_provider", no_cli)
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Search Engineer",
        "description": "Build Python search systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(app.state.db, app.state.settings, job["id"], "chatgpt_web")
    assert draft["provider"] == "chatgpt_web"
    assert any("starter draft" in warning for warning in draft["warnings"])

    next_job = client.post("/api/jobs/import", json={"company": "Another Co", "title": "Python Engineer",
        "description": "Build Python APIs for search products.", "apply_url": "https://example.org/second"}).json()
    queued = app.state.auto_apply_manager.queue_manual(next_job["id"], "chatgpt_web", prepare_anyway=True)
    assert queued["status"] == "queued"
    asyncio.run(app.state.auto_apply_manager._process(next_job["id"]))
    attempt = app.state.db.one("SELECT status,draft_id FROM auto_application_attempts WHERE vacancy_id=?", (next_job["id"],))
    assert attempt["status"] == "needs_review"
    assert attempt["draft_id"]


def test_chatgpt_input_receives_reply_from_assistant_message(tmp_path: Path) -> None:
    async def scenario():
        manager = ChatGPTInputManager(Settings(tmp_path), asyncio.Lock())
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("""
                    <div id='prompt-textarea' role='textbox' contenteditable='true'></div>
                    <script>
                    window.submitted = '';
                    document.querySelector('#prompt-textarea').addEventListener('keydown', e => {
                        if (e.key === 'Enter') {
                            window.submitted = e.target.textContent;
                            e.preventDefault();
                            const assistant = document.createElement('div');
                            assistant.setAttribute('data-message-author-role', 'assistant');
                            assistant.innerHTML = '<div class="markdown"><p>Generated response for the job application.</p></div>' +
                                '<button data-testid="copy-turn-action-button" aria-label="Copy">Copy</button>';
                            document.body.appendChild(assistant);
                        }
                    });
                    </script>
                """)

                async def fake_new_page():
                    return page

                manager._new_page = fake_new_page
                result = await manager.enter("Draft this application", timeout=5.0)
                assert result["status"] == "entered"
                assert result.get("reply") == "Generated response for the job application."
                assert "answer received" in result["detail"].lower()
                assert await page.evaluate("window.submitted") == "Draft this application"
            finally:
                await browser.close()

    asyncio.run(scenario())


def test_chatgpt_input_receives_reply_and_updates_draft(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={"company": "Prior Co", "role": "Engineer",
        "dates": "2024–2026", "bullets": ["Built Python search systems."]}).status_code == 201
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Search Engineer",
        "description": "Build Python search systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    before = client.get(f"/api/applications/{draft['id']}").json()

    async def fake_enter_with_reply(prompt, timeout=60.0):
        return {
            "status": "entered",
            "detail": "Prompt entered and answer received from ChatGPT.",
            "reply": "Senior Python search expert with extensive FastAPI experience.",
            "answer": "Senior Python search expert with extensive FastAPI experience.",
        }

    monkeypatch.setattr(client.app.state.chatgpt_input, "enter", fake_enter_with_reply)
    response = client.post(f"/api/applications/{draft['id']}/chatgpt-input", json={
        "section": "summary", "instruction": "Make summary punchy"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "entered"
    assert "applied" in data["detail"]
    assert data["reply"] == "Senior Python search expert with extensive FastAPI experience."

    after = client.get(f"/api/applications/{draft['id']}").json()
    assert after["package_hash"] != before["package_hash"]
    assert after["resume_data"]["summary"] == "Senior Python search expert with extensive FastAPI experience."


def test_apply_chatgpt_reply_handles_various_sections(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    client = TestClient(create_app(settings))
    db = client.app.state.db
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={"company": "Prior Co", "role": "Engineer",
        "dates": "2024–2026", "bullets": ["Built Python search systems."]}).status_code == 201
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Search Engineer",
        "description": "Build Python search systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(db, settings, job["id"], "template")

    # 1. Summary
    updated = apply_chatgpt_reply(db, settings, draft["id"], "summary", "Revised professional summary.")
    assert updated["resume_data"]["summary"] == "Revised professional summary."

    # 2. Message
    updated = apply_chatgpt_reply(db, settings, draft["id"], "message",
        "I built Python search systems at Prior Co and led our query indexing pipeline.")
    assert "Prior Co" in updated["message_data"]["body"]

    # 3. Experience with bullets
    updated = apply_chatgpt_reply(db, settings, draft["id"], "experience",
        "- Scaled distributed search engine\n- Optimized database query throughput")
    assert updated["resume_data"]["experience"][0]["bullets"] == [
        "Scaled distributed search engine",
        "Optimized database query throughput",
    ]

    # 4. Skills
    updated = apply_chatgpt_reply(db, settings, draft["id"], "skills",
        json.dumps({"skills": ["Python", "Elasticsearch"], "skill_groups": {"Backend": ["Python", "Elasticsearch"]}}))
    assert updated["resume_data"]["skills"] == ["Python", "Elasticsearch"]
    assert updated["resume_data"]["skill_groups"] == {"Backend": ["Python", "Elasticsearch"]}

    # 5. Achievements
    updated = apply_chatgpt_reply(db, settings, draft["id"], "achievements",
        "- 1st place in search hackathon\n- Published conference paper")
    assert updated["resume_data"]["achievements"] == [
        "1st place in search hackathon",
        "Published conference paper",
    ]

