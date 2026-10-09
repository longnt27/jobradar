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
                result = await manager.enter("Draft this application", timeout=0.5)
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
    assert saved.json()["automatic_drafts_paused"] is False
    assert app.state.auto_apply_manager.config()["enabled"] is True
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


def test_apply_chatgpt_reply_handles_projects_json_and_markdown(tmp_path: Path) -> None:
    from job_radar.drafting import update_draft
    settings = Settings(tmp_path)
    client = TestClient(create_app(settings))
    db = client.app.state.db

    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={"company": "Prior Co", "role": "Engineer",
        "dates": "2024–2026", "bullets": ["Built Python search systems."]}).status_code == 201

    p_wham = client.post("/api/evidence", json={"kind": "project", "title": "WHAM on iPhone",
        "claim": "On-device 3D human pose reconstruction",
        "repository_url": "https://github.com/longnt27/whamoniphone"}).json()["id"]
    assert client.patch(f"/api/evidence/{p_wham}", json={
        "approved": True,
        "details": {
            "tech_stack": ["Core ML", "PyTorch", "Swift"],
            "bullets": ["Built on-device 3D human-motion reconstruction.", "Measured PA-MPJPE of 51.80 mm."],
            "results": [{"id": "r1"}],
        }
    }).status_code == 200

    p_khanh = client.post("/api/evidence", json={"kind": "project", "title": "Khanh Scanner: Color-Preserving iOS Document Scanner",
        "claim": "Document scanner pipeline",
        "repository_url": "https://github.com/longnt27/khanh-scanner"}).json()["id"]
    assert client.patch(f"/api/evidence/{p_khanh}", json={
        "approved": True,
        "details": {
            "tech_stack": ["iOS", "VisionKit"],
            "bullets": ["Built iOS document scanner.", "Preserved blue signatures and red stamps."],
            "results": [{"id": "r1"}],
        }
    }).status_code == 200

    p_caus = client.post("/api/evidence", json={"kind": "project", "title": "CausClass: Auditable Classroom Behavior Analysis",
        "claim": "Classroom behavior analysis",
        "repository_url": "https://github.com/longnt27/CausClass"}).json()["id"]
    assert client.patch(f"/api/evidence/{p_caus}", json={
        "approved": True,
        "details": {
            "tech_stack": ["Python", "PyTorch", "YOLO"],
            "bullets": ["Built classroom analysis pipeline.", "Raised recall to 0.8745."],
            "results": [{"id": "r1"}],
        }
    }).status_code == 200

    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Search Engineer",
        "description": "Build Python search systems.", "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(db, settings, job["id"], "template")

    initial_resume = {**draft["resume_data"], "projects": [
        {"id": p_caus, "title": "CausClass: Auditable Classroom Behavior Analysis", "repository_url": "https://github.com/longnt27/CausClass",
         "tech_stack": ["Python"], "bullets": ["Built classroom analysis pipeline.", "Raised recall to 0.8745."]},
        {"id": p_khanh, "title": "Khanh Scanner: Color-Preserving iOS Document Scanner", "repository_url": "https://github.com/longnt27/khanh-scanner",
         "tech_stack": ["iOS"], "bullets": ["Built iOS document scanner.", "Preserved blue signatures."]},
    ]}
    draft = update_draft(db, settings, draft["id"], {"resume_data": initial_resume})
    assert any(p["id"] == p_khanh for p in draft["resume_data"]["projects"])
    assert not any(p["id"] == p_wham for p in draft["resume_data"]["projects"])

    # 1. Test applying markdown reply replacing khanhscanner with whamoniphone
    md_reply = (
        "Certainly! Here is the revised project:\n\n"
        "### WHAM on iPhone\n"
        "- Built on-device 3D human-motion reconstruction from video.\n"
        "- Measured camera-relative PA-MPJPE of 51.80 mm on 3DPW.\n"
    )
    updated = apply_chatgpt_reply(db, settings, draft["id"], "projects", md_reply, instruction="change khanhscanner to whamoniphone")
    project_titles = [p["title"] for p in updated["resume_data"]["projects"]]
    assert "WHAM on iPhone" in project_titles
    assert "Khanh Scanner: Color-Preserving iOS Document Scanner" not in project_titles
    assert updated["resume_data"]["projects"][1]["bullets"] == [
        "Built on-device 3D human-motion reconstruction from video.",
        "Measured camera-relative PA-MPJPE of 51.80 mm on 3DPW.",
    ]

    # 2. Test applying JSON reply wrapped in markdown fences
    json_reply = (
        "Here are the updated projects in JSON format:\n\n"
        "```json\n"
        "[\n"
        '  {"id": "' + p_caus + '", "title": "CausClass: Auditable Classroom Behavior Analysis", "bullets": ["Updated CausClass 1", "Updated CausClass 2"]},\n'
        '  {"id": "' + p_khanh + '", "title": "Khanh Scanner: Color-Preserving iOS Document Scanner", "bullets": ["Updated Khanh 1", "Updated Khanh 2"]}\n'
        "]\n"
        "```\n\n"
        "Let me know if you need anything else!"
    )
    updated2 = apply_chatgpt_reply(db, settings, draft["id"], "projects", json_reply, instruction="re-add khanh scanner")
    project_titles2 = [p["title"] for p in updated2["resume_data"]["projects"]]
    assert "Khanh Scanner: Color-Preserving iOS Document Scanner" in project_titles2
    assert updated2["resume_data"]["projects"][1]["bullets"] == ["Updated Khanh 1", "Updated Khanh 2"]


def test_chatgpt_logged_in_detection_from_cookie_store(tmp_path: Path) -> None:
    from job_radar.chatgpt_handoff import CHROME_EPOCH_OFFSET, chatgpt_logged_in
    import sqlite3
    import time

    profile = tmp_path / "browser-profile"
    cookies_dir = profile / "Default"
    cookies_dir.mkdir(parents=True)
    cookies_db = cookies_dir / "Cookies"

    # 1. No cookies file -> False
    assert chatgpt_logged_in(profile) is False

    # 2. Cookies file with unexpired session token -> True
    conn = sqlite3.connect(cookies_db)
    conn.execute("""
        CREATE TABLE cookies (
            host_key TEXT, name TEXT, path TEXT, value TEXT,
            expires_utc INTEGER, is_secure INTEGER, has_expires INTEGER
        )
    """)
    current_chrome = int((time.time() + CHROME_EPOCH_OFFSET) * 1_000_000)
    conn.execute(
        "INSERT INTO cookies VALUES ('.chatgpt.com', '__Secure-next-auth.session-token.0', '/', 'token_val', ?, 1, 1)",
        (current_chrome + 10_000_000,),
    )
    conn.commit()
    conn.close()

    assert chatgpt_logged_in(profile) is True

    # 3. Expired token -> False
    conn = sqlite3.connect(cookies_db)
    conn.execute(
        "UPDATE cookies SET expires_utc=?",
        (current_chrome - 10_000_000,),
    )
    conn.commit()
    conn.close()

    assert chatgpt_logged_in(profile) is False


def test_chatgpt_open_login_auto_detects_already_logged_in_and_sets_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("job_radar.web.chrome_executable", lambda: "/fake/chrome")
    settings = Settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    # Mock chatgpt_logged_in to True
    monkeypatch.setattr("job_radar.chatgpt_handoff.chatgpt_logged_in", lambda _p: True)

    # Initial provider is not chatgpt_web
    profile = client.get("/api/profile").json()
    assert profile.get("drafting_provider") != "chatgpt_web"

    # Calling login endpoint auto-detects and saves provider
    res = client.post("/api/chatgpt/login")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "already_logged_in"
    assert data["logged_in"] is True

    # Profile in DB now has chatgpt_web
    updated_profile = client.get("/api/profile").json()
    assert updated_profile["drafting_provider"] == "chatgpt_web"

    # Setup endpoint reports chatgpt_logged_in
    setup = client.get("/api/setup").json()
    assert setup["chatgpt_logged_in"] is True
    assert setup["chatgpt"]["logged_in"] is True

    # Status endpoint works
    status = client.get("/api/chatgpt/status").json()
    assert status["logged_in"] is True


def test_chatgpt_input_uses_priority_browser(tmp_path: Path) -> None:
    from contextlib import asynccontextmanager
    priority_entered = False

    @asynccontextmanager
    async def fake_priority():
        nonlocal priority_entered
        priority_entered = True
        yield

    settings = Settings(tmp_path)
    browser_lock = asyncio.Lock()
    manager = ChatGPTInputManager(settings, browser_lock, priority_browser=fake_priority)

    async def scenario():
        # Mock _new_page to avoid opening real chrome
        async def fake_new_page():
            raise RuntimeError("Stop after browser start")

        manager._new_page = fake_new_page
        result = await manager.enter("prompt")
        assert "Stop after browser start" in result["detail"]
        assert priority_entered is True

    asyncio.run(scenario())


def test_chatgpt_input_receives_reply_from_modern_dom(tmp_path: Path) -> None:
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
                            assistant.className = 'agent-turn DilResponseRoot_abc123';
                            assistant.innerHTML = '<div class="MarkdownRoot_xyz456"><p>Modern ChatGPT generated reply.</p></div>' +
                                '<button aria-label="Copy">Copy</button>';
                            document.body.appendChild(assistant);
                        }
                    });
                    </script>
                """)

                async def fake_new_page(background=False, temporary=True):
                    return page

                manager._new_page = fake_new_page
                result = await manager.enter("Draft this application", timeout=5.0)
                assert result["status"] == "entered"
                assert result.get("reply") == "Modern ChatGPT generated reply."
                assert "answer received" in result["detail"].lower()
                assert await page.evaluate("window.submitted") == "Draft this application"
            finally:
                await browser.close()

    asyncio.run(scenario())


def test_chatgpt_start_browser_background_and_temporary_chat(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr("job_radar.chatgpt_handoff.hide_chrome", lambda: None)
    monkeypatch.setattr("job_radar.chatgpt_handoff.activate_app", lambda _b: None)
    monkeypatch.setattr("job_radar.chatgpt_handoff.launch_background_browser", lambda _b, _a: None)
    monkeypatch.setattr("job_radar.chatgpt_handoff.subprocess.Popen", fake_popen)

    async def scenario():
        browser_lock = asyncio.Lock()
        manager = ChatGPTInputManager(Settings(tmp_path), browser_lock)

        # 1. Background launch with temporary chat
        await manager._start_browser(background=True, temporary=True)
        assert len(processes) == 1
        args = processes[0].args
        assert "--window-position=-2400,-2400" in args
        assert "--window-size=1280,800" in args
        assert "https://chatgpt.com/?temporary-chat=true" in args
        assert browser_lock.locked()

        await manager.stop()
        assert not browser_lock.locked()
        assert manager.process is None

        # 2. Foreground launch for login (temporary=False)
        processes.clear()
        result = await manager.open_login()
        assert result["status"] == "opened"
        assert len(processes) == 1
        login_args = processes[0].args
        assert "--window-position=-2400,-2400" not in login_args
        assert "https://chatgpt.com/" in login_args
        assert "https://chatgpt.com/?temporary-chat=true" not in login_args

        await manager.stop()
        assert not browser_lock.locked()

    asyncio.run(scenario())


def test_chatgpt_start_browser_uses_launch_background_browser_on_macos(tmp_path: Path, monkeypatch) -> None:
    from job_radar.desktop_handoff import BackgroundProcess

    fake_bg_proc = BackgroundProcess(99999)
    captured_bundle = []
    captured_args = []

    def fake_launch(bundle, args):
        captured_bundle.append(bundle)
        captured_args.append(args)
        return fake_bg_proc

    monkeypatch.setattr("job_radar.chatgpt_handoff.chrome_executable",
                        lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr("job_radar.chatgpt_handoff.launch_background_browser", fake_launch)
    monkeypatch.setattr("job_radar.chatgpt_handoff.activate_app", lambda _b: None)

    async def scenario():
        browser_lock = asyncio.Lock()
        manager = ChatGPTInputManager(Settings(tmp_path), browser_lock)

        await manager._start_browser(background=True, temporary=True)
        assert len(captured_bundle) == 1
        assert captured_bundle[0] == "/Applications/Google Chrome.app"
        assert manager.process is fake_bg_proc
        assert browser_lock.locked()

        # Stop releases lock and clears process
        await manager.stop()
        assert not browser_lock.locked()
        assert manager.process is None

    asyncio.run(scenario())


def test_chatgpt_enter_stops_browser_on_completion(tmp_path: Path) -> None:
    async def scenario():
        browser_lock = asyncio.Lock()
        manager = ChatGPTInputManager(Settings(tmp_path), browser_lock)
        stop_called = False

        original_stop = manager.stop

        async def tracking_stop():
            nonlocal stop_called
            stop_called = True
            await original_stop()

        manager.stop = tracking_stop

        async def fake_new_page(background=False, temporary=True):
            raise RuntimeError("Fake failure to test cleanup")

        manager._new_page = fake_new_page
        result = await manager.enter("Draft")
        assert result["status"] == "failed"
        assert stop_called is True
        assert not browser_lock.locked()

    asyncio.run(scenario())


def test_auto_apply_chatgpt_web_drafts_when_logged_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("job_radar.web.chrome_executable", lambda: "/fake/chrome")
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example",
        "email": "alex@example.org",
        "drafting_provider": "chatgpt_web",
        "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024–2026",
                        "bullets": ["Built Python search systems."]}]
    })
    client.put("/api/profile", json=profile)

    job = client.post("/api/jobs/import", json={
        "company": "OpenAI Partner",
        "title": "Senior AI Engineer",
        "description": "Develop Python ML architectures.",
        "apply_url": "https://example.com/apply"
    }).json()

    chatgpt_input = app.state.chatgpt_input
    monkeypatch.setattr(chatgpt_input, "status", lambda: {"logged_in": True, "state": "saved", "error": None})

    async def fake_enter(prompt: str, timeout: float = 60.0):
        return {
            "status": "entered",
            "reply": json.dumps({
                "summary": "Experienced AI engineer specializing in Python pipelines.",
                "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python search systems."]}]
            })
        }

    monkeypatch.setattr(chatgpt_input, "enter", fake_enter)

    queued = app.state.auto_apply_manager.queue_manual(job["id"], "chatgpt_web", prepare_anyway=True)
    assert queued["status"] == "queued"
    asyncio.run(app.state.auto_apply_manager._process(job["id"]))

    attempt = app.state.db.one("SELECT status,draft_id FROM auto_application_attempts WHERE vacancy_id=?", (job["id"],))
    assert attempt["status"] in {"awaiting_review", "needs_review"}
    draft = app.state.db.one("SELECT resume_data FROM application_drafts WHERE id=?", (attempt["draft_id"],))
    resume = json.loads(draft["resume_data"])
    assert "Experienced AI engineer" in resume["summary"]


def test_auto_apply_chatgpt_web_delay_between_automatic_drafts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("job_radar.web.chrome_executable", lambda: "/fake/chrome")
    app = create_app(Settings(tmp_path))
    manager = app.state.auto_apply_manager
    manager.chatgpt_delay = 0.05  # fast delay for test

    app.state.db.set_setting("profile", {"name": "Alex", "email": "a@b.com", "drafting_provider": "chatgpt_web"})
    assert manager.chatgpt_delay == 0.05



