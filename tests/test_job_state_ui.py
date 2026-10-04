import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_later_job_states_can_be_set_and_filtered_in_browser(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    for state in ("interview", "rejected", "offer"):
        client.post("/api/jobs/import", json={"company": "Example", "title": f"Engineer {state}",
            "description": "Build Python systems.", "apply_url": "https://example.org/apply"})
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
                for state in ("interview", "rejected", "offer"):
                    title = f"Engineer {state}"
                    page = browser.new_page()
                    page.set_default_timeout(10000)
                    page.goto(f"http://127.0.0.1:{port}/#jobs")
                    page.get_by_role("button", name=f"Open {title} at Example").click()
                    page.get_by_role("button", name=state.capitalize(), exact=True).click()
                    page.locator("#job-state").select_option(state)
                    page.get_by_role("button", name="Search", exact=True).click()
                    page.wait_for_function("document.querySelectorAll('[data-job]').length === 1")
                    assert page.get_by_role("button", name=f"Open {title} at Example").is_visible()
                    page.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
