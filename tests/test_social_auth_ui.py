import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_social_sign_in_is_a_setup_step_and_expiry_is_visible_from_jobs(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
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
                page.set_default_timeout(3000)
                page.goto(f"http://127.0.0.1:{port}/#home")
                page.get_by_role("button", name="Connect LinkedIn and Facebook").click()
                assert page.locator("#social-sign-in-panel").evaluate("node => node.open")
                assert page.locator("#profile").get_attribute("class").find("active") >= 0
                assert page.get_by_role("button", name="Sign in to LinkedIn").is_visible()
                assert page.get_by_role("button", name="Sign in to Facebook").is_visible()
                assert page.locator("#setup-browser-finish").count() == 0

                app.state.db.set_setting("social_login_completed_at_linkedin", "2026-10-01T12:00:00+00:00")
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.locator("#linkedin-sign-in-status").get_by_text("Connected").wait_for()
                app.state.db.set_setting("social_reauth_required_linkedin", "1")
                page.get_by_role("button", name="Jobs", exact=True).first.click()
                banner = page.locator("#social-auth-banner")
                banner.wait_for(state="visible")
                assert "LinkedIn" in banner.inner_text()
                banner.get_by_role("button", name="Sign in again").click()
                assert page.locator("#social-sign-in-panel").evaluate("node => node.open")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
