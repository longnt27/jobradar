import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_app_shell_navigation_settings_and_mobile_accessibility(tmp_path: Path) -> None:
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
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.goto(f"http://127.0.0.1:{port}/#home")

                primary = page.locator(".primary-nav-scroll nav")
                assert primary.get_by_role("link", name="Home", exact=True).get_attribute("href") == "#home"
                assert primary.get_by_role("link", name="Settings", exact=True).get_attribute("href") == "#settings"
                assert page.locator(".sidebar-secondary").get_by_role("link", name="Activity", exact=True).get_attribute("href") == "#queue"
                assert primary.get_by_role("link", name="Home", exact=True).get_attribute("aria-current") == "page"
                assert page.title() == "Home · Job Radar"

                primary.get_by_role("link", name="Settings", exact=True).click()
                page.locator("#settings.active").wait_for()
                assert page.url.endswith("#settings")
                assert page.title() == "Settings · Job Radar"
                assert primary.get_by_role("link", name="Settings", exact=True).get_attribute("aria-current") == "page"
                assert primary.get_by_role("link", name="Home", exact=True).get_attribute("aria-current") is None
                for selector in ("#provider-panel", "#social-sign-in-panel", "#telegram-panel", "#smtp-panel"):
                    assert page.locator(f"#settings {selector}").count() == 1
                    assert page.locator(f"#profile {selector}").count() == 0

                page.goto(f"http://127.0.0.1:{port}/#setup")
                page.locator("#settings.active").wait_for()
                assert page.url.endswith("#settings")

                skip = page.get_by_role("link", name="Skip to content")
                skip.focus()
                assert skip.evaluate("node => getComputedStyle(node).transform") == "none"
                skip.press("Enter")
                assert page.locator("#app-content").evaluate("node => document.activeElement === node")

                assert page.locator(".sidebar").evaluate("node => getComputedStyle(node).position") == "sticky"
                assert primary.evaluate("node => node.scrollWidth > node.clientWidth")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
