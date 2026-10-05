import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_sources_and_employers_management_ui(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
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
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.set_default_timeout(4000)
                page.goto(f"http://127.0.0.1:{port}/#sources")
                page.locator("#source-list .source-card").first.wait_for()
                first_source = page.locator("#source-list .source-card").first
                source_name = first_source.locator(".item-title").inner_text().split("\n")[0].strip()
                assert first_source.get_by_role("link", name=f"Open source {source_name}").is_visible()

                assert page.locator("#source-status").is_visible()
                assert page.locator("#source-enabled").is_visible()
                assert page.locator("#source-success").is_visible()
                assert page.locator("#source-sort").is_visible()
                assert page.get_by_role("button", name="Scan never-checked sources").is_visible()
                assert page.get_by_role("button", name="Scan sources due now").is_visible()

                page.locator("#source-query").fill("__definitely_no_source__")
                page.get_by_text("No sources match these filters.").wait_for()
                page.locator("#source-query").fill("")
                page.locator("#source-list .source-card").first.wait_for()

                first_toggle = page.locator("#source-list input[data-toggle]").first
                first_toggle.click()
                page.locator("#source-list .source-auto-status", has_text="Saved").first.wait_for()

                page.locator(".sidebar nav [data-tab='employers']").click()
                page.locator("#employers.active").wait_for()
                page.locator("#employer-query").fill("GSM")
                page.locator("#employer-query").press("Enter")
                employer = page.locator("#employer-list .employer", has_text="GSM / Xanh SM")
                employer.wait_for()
                career_button = employer.locator("[data-employer-source]")
                assert "GSM / Xanh SM" in career_button.get_attribute("aria-label")

                page.evaluate("window.prompt = () => { throw new Error('native prompt should not be used'); }")
                career_button.click()
                assert employer.locator(".employer-career-form").is_visible()
                employer.locator("[data-employer-cancel]").click()
                assert employer.locator(".employer-career-form").is_hidden()

                page.locator("#employer-query").fill("")
                page.locator("#employer-query").press("Enter")
                page.locator("#employer-list .employer").first.wait_for()
                assert not page.locator("#employers-next").is_disabled()
                page.locator("#employers-next").click()
                page.wait_for_function(
                    "document.querySelector('#employer-page-summary').textContent.startsWith('Page 2 of ')"
                )
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
