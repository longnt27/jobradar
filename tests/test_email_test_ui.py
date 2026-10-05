import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_email_settings_can_be_tested_from_profile(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    assert client.post("/api/setup/smtp", json={
        "host": "smtp.gmail.com", "port": 465, "user": "alex@gmail.com",
        "password": "app-password", "from_address": "alex@gmail.com",
    }).status_code == 200
    monkeypatch.setattr("job_radar.web.send_test_email", lambda _settings: "alex@gmail.com")
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
                page.goto(f"http://127.0.0.1:{port}/#settings")
                page.locator("#settings.active").wait_for()
                page.wait_for_function("document.querySelector('#provider-availability').textContent.trim().length > 0")
                page.locator("#smtp-panel summary").click()
                page.locator("#smtp-panel[open] #setup-smtp-form").wait_for()
                button = page.locator("#smtp-send-test")
                page.wait_for_function("!document.querySelector('#smtp-send-test').disabled")
                page.locator('#setup-smtp-form [name="from_address"]').fill("other@gmail.com")
                assert button.is_disabled()
                assert "Save your changes" in page.locator("#smtp-test-result").inner_text()
                page.locator('#setup-smtp-form [name="from_address"]').fill("alex@gmail.com")
                page.locator('#setup-smtp-form button[type="submit"]').click()
                page.wait_for_function("!document.querySelector('#smtp-send-test').disabled")
                button.click()
                page.wait_for_function("document.querySelector('#smtp-test-result').textContent.includes('accepted')")
                assert "alex@gmail.com" in page.locator("#smtp-test-result").inner_text()
                assert page.locator("#setup-smtp-status").inner_text() == "Test email accepted"
                page.reload()
                page.wait_for_function("document.querySelector('#setup-smtp-status').textContent === 'Test email accepted'")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
