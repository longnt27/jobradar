import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_jobs_url_state_cards_and_page_scroll(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    first = client.post("/api/jobs/import", json={
        "company": "Example Robotics", "title": "Senior AI Engineer",
        "description": "Build Python perception systems.", "location": "Hanoi",
        "apply_url": "https://example.org/apply",
    }).json()["id"]
    client.post("/api/jobs/import", json={
        "company": "Other Co", "title": "Backend Engineer",
        "description": "Build APIs.", "location": "Da Nang",
    })
    app.state.db.execute(
        "UPDATE vacancies SET score=91,analysis_status='done',work_mode='Remote',published_at=datetime('now','-2 hours'),score_detail=? WHERE id=?",
        (json.dumps({
            "facts":{"seniority":"Senior","work_mode":"Remote"},
            "criteria":{
                "role":{"score":9,"reason":"Direct role fit"},
                "location":{"score":3,"reason":"Some travel required"},
            },
            "weights":{"role":30,"location":10},
            "explanation":"Strong overall match.",
        }), first),
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
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(
                    f"http://127.0.0.1:{port}/#jobs?q=Senior&score=80&mode=remote"
                    f"&location=Hanoi&seniority=Senior&sort=posted&page=1&job={first}"
                )
                page.locator("#jobs.active").wait_for()
                assert page.locator("#job-query").input_value() == "Senior"
                assert page.locator("#job-score").input_value() == "80"
                assert page.locator("#job-work-mode").input_value() == "remote"
                assert page.locator("#job-location").input_value() == "Hanoi"
                assert page.locator("#job-seniority").input_value() == "Senior"
                assert page.locator("#job-sort").input_value() == "posted"
                card = page.get_by_role("button", name="Open Senior AI Engineer at Example Robotics")
                card.wait_for()
                assert "Remote" in card.inner_text()
                assert "Senior" in card.inner_text()
                assert "Role" in card.inner_text()
                assert "Location" in card.inner_text()
                assert "ago" in card.inner_text() or "Yesterday" in card.inner_text()
                page.get_by_role("heading", name="Senior AI Engineer").wait_for()
                assert page.locator("#job-state-control").input_value() == "new"
                assert page.locator("#job-list").evaluate("node => getComputedStyle(node).overflowY") == "visible"
                assert page.locator("#job-list").evaluate("node => getComputedStyle(node).maxHeight") == "none"

                before = page.url
                page.reload()
                page.locator("#jobs.active").wait_for()
                page.get_by_role("heading", name="Senior AI Engineer").wait_for()
                assert page.url == before
                assert page.locator("#job-query").input_value() == "Senior"

                page.locator("#job-sort").select_option("company")
                page.wait_for_function("location.hash.includes('sort=company')")
                assert "sort=company" in page.url
                page.locator("#jobs-clear-filters").click()
                page.wait_for_function("location.hash === '#jobs'")
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
