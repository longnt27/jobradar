import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from job_radar.chatgpt_handoff import ChatGPTInputManager, application_prompt
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
