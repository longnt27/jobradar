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
                    f"&location=Hanoi&seniority=senior&sort=posted&page=1&job={first}"
                )
                page.locator("#jobs.active").wait_for()
                page.wait_for_function(r"document.querySelector('#page-title').textContent === 'Jobs' && /^\d+ new$/.test(document.querySelector('#jobs-new-count').textContent)")
                assert page.locator("#jobs-new-count").evaluate("node => parseFloat(getComputedStyle(node).fontSize) < parseFloat(getComputedStyle(document.querySelector('#page-title')).fontSize)")
                assert page.locator("#jobs-inbox-summary, .jobs-inbox-strip").count() == 0
                assert page.locator("#job-inbox").is_hidden()
                page.get_by_role("button", name="Filter jobs").click()
                assert page.locator("#job-inbox").is_visible()
                page.keyboard.press("Escape")
                assert page.get_by_role("button", name="Add a job from a description").is_visible()
                assert page.locator("#job-import-toggle").bounding_box()["x"] > page.locator("#job-detail").bounding_box()["x"]
                assert page.locator("#job-import-toggle svg").count() == 1
                page.get_by_role("button", name="Add a job from a description").hover()
                assert page.locator(".job-add-help").is_visible()
                page.get_by_role("button", name="Add a job from a description").click()
                assert page.locator("#job-import").is_visible()
                page.keyboard.press("Escape")
                assert page.locator("#job-query").input_value() == "Senior"
                assert page.locator("#job-score").input_value() == "80"
                assert page.locator("#job-work-mode").input_value() == "remote"
                assert page.locator("#job-location").input_value() == "Hanoi"
                assert page.locator("#job-seniority").input_value() == "senior"
                assert page.locator("#job-sort").input_value() == "posted"
                card = page.get_by_role("button", name="Open Senior AI Engineer at Example Robotics")
                card.wait_for()
                assert "Strong match" not in card.inner_text()
                assert page.locator("#job-list .job-card-tags .status-badge").count() == 0
                source_links = page.evaluate("""() => ['linkedin', 'facebook', 'career'].map(kind => {
                  const node = document.createElement('div');
                  node.innerHTML = renderJobCard({id:'source-preview', title:'Source role', company:'Example',
                    location:'Hanoi', score:91, fit_class:'strong', work_mode:'Remote', read_state:'seen',
                    decision_state:'undecided', application_progress:'not_started', recruiting_outcome:'none',
                    source:{kind, url:'https://example.org/jobs/example'}});
                  const link = node.querySelector('.job-source-link');
                  return {text:link?.textContent.trim(), icon:!!link?.querySelector('svg'),
                    badges:[...node.querySelectorAll('.job-card-tags span')].map(item => item.textContent)};
                })""")
                assert source_links == [
                    {"text": "LinkedIn ↗", "icon": True, "badges": ["Remote"]},
                    {"text": "Facebook ↗", "icon": True, "badges": ["Remote"]},
                    {"text": "Example career page ↗", "icon": True, "badges": ["Remote"]},
                ]
                card.click()
                assert card.get_attribute("aria-pressed") == "true"
                page.wait_for_function("document.activeElement === document.querySelector('#job-detail')")
                assert page.locator("#job-detail").get_attribute("role") == "region"
                assert page.get_by_role("link", name="Open application page").is_visible()
                assert "Remote" in card.inner_text()
                assert "Senior" in card.inner_text()
                assert "Role" in page.locator("#job-detail").inner_text()
                assert page.locator("#job-detail .match-summary-card").count() == 0
                assert page.locator("#job-detail .job-score-badge").is_visible()
                assert page.locator("#job-detail .job-title-row .job-original-icon").is_visible()
                assert page.locator("#job-detail .decision-card--basics").is_visible()
                assert page.locator("#job-detail .job-lifecycle-grid").evaluate("node => node.nextElementSibling?.classList.contains('job-decision-summary')")
                assert page.locator("#job-detail .job-review-status").count() == 0
                assert "Match reviewed" not in page.locator("#job-detail").inner_text()
                assert page.locator("#job-detail #job-zalo-contact").count() == 0
                assert page.locator("#job-detail .job-application-method, #job-detail .job-prepare-section").count() == 0
                assert page.locator("#job-detail .job-lifecycle-card [data-prepare]").count() == 1
                if page.locator("#job-detail .job-sightings").count():
                    assert page.locator("#job-detail > :last-child").evaluate("node => node.matches('details.job-sightings')")
                assert "Location" in page.locator("#job-detail").inner_text()
                assert "ago" in card.inner_text() or "Yesterday" in card.inner_text()
                page.get_by_role("heading", name="Senior AI Engineer").wait_for()
                assert page.locator("#job-outcome-control").input_value() == "none"
                assert "No decision yet" in page.locator("#job-detail").inner_text()
                assert client.get(f"/api/jobs/{first}").json()["read_state"] == "seen"
                assert page.locator("#job-list").evaluate("node => getComputedStyle(node).overflowY") == "auto"
                assert page.locator("#job-detail").evaluate("node => getComputedStyle(node).overflowY") == "auto"
                assert page.locator("#job-detail").evaluate("node => {node.scrollTop = 200; return node.scrollTop > 0}")
                assert page.locator("#jobs").evaluate("node => node.getBoundingClientRect().bottom <= innerHeight + 1")
                assert page.locator("#job-list").evaluate("node => getComputedStyle(node).maxHeight") == "none"

                before = page.url
                page.reload()
                page.locator("#jobs.active").wait_for()
                page.get_by_role("heading", name="Senior AI Engineer").wait_for()
                assert page.url == before
                assert page.locator("#job-query").input_value() == "Senior"

                app.state.db.execute("UPDATE vacancies SET analysis_status='pending' WHERE id=?", (first,))
                page.reload()
                page.get_by_role("heading", name="Senior AI Engineer").wait_for()
                detail = page.locator("#job-detail").inner_text()
                assert "Score pending" in detail
                assert "91/100" not in detail
                assert "Direct role fit" not in detail

                page.get_by_role("button", name="Filter jobs").click()
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
