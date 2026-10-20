import socket
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app
from job_radar.ingest import ObservedJob, ingest


@pytest.fixture
def clarity_app(tmp_path: Path):
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_jobs_keeps_search_in_left_pane_and_settings_owns_review_and_theme(clarity_app, monkeypatch) -> None:
    app, base = clarity_app
    monkeypatch.setattr(app.state.match_manager, "status", lambda: {
        "model": "qwen3.5:4b-q4_K_M", "pending": 120, "completed": 30, "failed": 0,
        "service_error": None,
    })
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(base + "/#jobs")
            page.locator("#jobs.active").wait_for()
            assert page.locator("#jobs .job-analysis-panel").count() == 0
            assert page.locator(".topbar .eyebrow").count() == 0
            assert page.locator(".topbar #theme-preference").count() == 0
            assert page.locator(".job-list-pane .jobs-inbox-strip").count() == 0
            assert page.locator(".job-list-pane .jobs-controls #job-query").is_visible()
            page.locator("#job-query").fill("engineer")
            page.wait_for_function("location.hash.includes('q=engineer')")
            page.locator("#job-query").fill("")
            page.wait_for_function("!location.hash.includes('q=engineer')")
            assert not page.locator("#job-filter-popover").is_visible()
            filter_button = page.get_by_role("button", name="Filter jobs")
            assert filter_button.get_attribute("aria-expanded") == "false"
            filter_button.click()
            assert filter_button.get_attribute("aria-expanded") == "true"
            assert page.locator("#job-inbox").is_visible()
            assert page.locator("#job-fit").is_visible()
            assert page.locator("#job-more-filters").is_visible()
            assert not page.locator("#job-more-filters").evaluate("node => node.open")
            assert page.locator("#job-decision").is_hidden()
            page.locator("#job-more-filters summary").click()
            assert page.locator("#job-decision").is_visible()
            page.keyboard.press("Escape")
            assert not page.locator("#job-filter-popover").is_visible()
            page.get_by_role("button", name="Profile menu").click()
            page.locator("#profile-menu [data-tab='settings']").click()
            page.wait_for_function("document.querySelector('#job-analysis-model').textContent.includes('qwen3.5')")
            assert "120" in page.locator("#matching-overview").inner_text()
            page.get_by_role("tab", name="AI Models", exact=True).click()
            page.get_by_role("tab", name="Job matching").click()
            assert page.locator("#settings .job-analysis-panel").is_visible()
            page.get_by_role("tab", name="General", exact=True).click()
            assert page.locator("#settings #theme-preference").is_visible()
            page.locator("#theme-preference").select_option("dark")
            assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        finally:
            browser.close()


def test_sources_start_with_a_short_list_and_optional_filters(clarity_app) -> None:
    _app, base = clarity_app
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(base + "/#sources")
            page.locator("#source-list .source-card").first.wait_for()
            assert page.locator("#source-list .source-card").count() <= 12
            assert "Page 1" in page.locator("#source-page-summary").inner_text()
            assert page.locator("#source-filter-popover").is_hidden()
            page.locator("#source-filter-toggle").click()
            assert page.locator("#source-enabled").is_visible()
        finally:
            browser.close()


def test_settings_employers_and_tutorial_have_clear_starting_views(clarity_app) -> None:
    app, base = clarity_app
    client = TestClient(app)
    client.post("/api/employers", json={"name": "QA Unwatched Company"})
    client.post("/api/employers", json={
        "name": "QA Watched Company", "career_url": "https://example.org/careers",
    })
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(base + "/#settings")
            page.wait_for_function("document.querySelector('#search-intent-summary strong').textContent.includes('match')")
            page.get_by_role("tab", name="Search preferences").click()
            assert page.locator("#search-intent-form").is_visible()
            page.get_by_role("button", name="Close Settings").click()
            page.get_by_role("button", name="Employers", exact=True).first.click()
            page.locator("#employer-list .employer").first.wait_for()
            assert page.locator("#employer-list .employer").count() <= 18
            assert "QA Unwatched Company" not in page.locator("#employer-list").inner_text()
            page.locator("#employer-query").fill("QA Unwatched Company")
            page.locator("#employer-query").press("Enter")
            page.get_by_text("QA Unwatched Company", exact=True).wait_for()
            page.get_by_role("button", name="Profile menu").click()
            page.locator("#profile-menu [data-tab='guide']").click()
            assert page.get_by_role("heading", name="Your job search, step by step").is_visible()
            assert page.get_by_text("Nothing is sent until you approve it", exact=False).is_visible()
        finally:
            browser.close()


def test_profile_and_settings_load_only_what_is_visible(clarity_app) -> None:
    app, base = clarity_app
    app.state.db.set_setting("profile", {"name": "QA User", "email": "qa@example.org", "drafting_provider": "codex"})
    app.state.db.set_setting("matching_model", "test:small")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            requests = []
            page.on("request", lambda request: requests.append(request.url))
            page.goto(base + "/#profile")
            page.get_by_text("QA User · qa@example.org", exact=False).wait_for()
            assert sum(url.endswith("/api/setup") for url in requests) == 1

            page.get_by_role("button", name="Profile menu").click()
            page.locator("#profile-menu [data-tab='settings']").click()
            page.wait_for_function("document.querySelector('#search-intent-glance').textContent.includes('Strong')")
            page.locator("#tab-loading").wait_for(state="hidden")
            assert any(url.endswith("/api/matching/models") for url in requests)
            assert page.locator("#settings").evaluate("node => node.getBoundingClientRect().width") < 1100
            page.get_by_role("tab", name="AI Models", exact=True).click()
            page.get_by_role("tab", name="Job matching").click()
            assert page.locator("#matching-model-form select").is_visible()
        finally:
            browser.close()


def test_navigation_and_job_detail_show_skeletons_during_slow_loads(clarity_app) -> None:
    app, base = clarity_app
    source_id = app.state.db.one("SELECT id FROM sources WHERE kind='linkedin' LIMIT 1")["id"]
    job_id, _ = ingest(app.state.db, source_id, ObservedJob(
        "https://www.linkedin.com/jobs/view/900001/", "AI Engineer", "Example Robotics",
        "Build reliable Python systems.", location="Hanoi",
    ))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script("""
              const originalFetch = window.fetch.bind(window);
              window.fetch = async (...args) => {
                if (String(args[0]).includes('/api/')) {
                  await new Promise(resolve => setTimeout(resolve, 500));
                }
                return originalFetch(...args);
              };
            """)
            page.goto(base + "/#jobs", wait_until="domcontentloaded")
            skeleton = page.locator("#jobs > .tab-skeleton")
            skeleton.wait_for(state="visible")
            assert skeleton.locator(".skeleton-card").count() >= 6
            assert page.locator("#jobs").get_attribute("aria-busy") == "true"
            assert page.locator("#tab-loading").evaluate("node => node.getBoundingClientRect().width") <= 1
            skeleton.wait_for(state="hidden")
            page.locator(f'[data-job="{job_id}"]').click()
            detail_skeleton = page.locator("#job-detail > .region-skeleton")
            detail_skeleton.wait_for(state="visible")
            detail_skeleton.wait_for(state="hidden")
            assert page.locator("#job-detail h2").first.is_visible()

            page.locator("#job-query").fill("Example Robotics")
            list_skeleton = page.locator("#job-list > .region-skeleton")
            list_skeleton.wait_for(state="visible")
            list_skeleton.wait_for(state="hidden")
            assert page.locator(f'[data-job="{job_id}"]').is_visible()

            page.locator('.app-header [data-tab="sources"]').click()
            sources_skeleton = page.locator("#sources > .tab-skeleton")
            sources_skeleton.wait_for(state="visible")
            sources_skeleton.wait_for(state="hidden")
            assert page.locator("#sources").get_attribute("aria-busy") is None
        finally:
            browser.close()


def test_settings_switches_threshold_location_and_top_navigation_layout(clarity_app) -> None:
    app, base = clarity_app
    client = TestClient(app)
    profile = client.get("/api/profile").json()
    profile.update({"name": "QA User", "email": "qa@example.org", "location": "Hanoi"})
    assert client.put("/api/profile", json=profile).status_code == 200
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(base + "/#home")
            page.get_by_role("button", name="Profile menu").click()
            guide = page.locator(".app-header [data-tab='guide']").bounding_box()
            queue = page.locator(".app-header [data-tab='queue']").bounding_box()
            settings_button = page.locator('.app-header nav [data-tab="settings"]').bounding_box()
            assert guide and queue and settings_button
            assert settings_button["x"] == guide["x"]
            assert queue["x"] >= page.locator(".nav-primary").bounding_box()["x"] + page.locator(".nav-primary").bounding_box()["width"]
            assert queue["x"] < page.locator("#profile-menu-toggle").bounding_box()["x"]
            assert settings_button["y"] < guide["y"]
            assert page.locator(".app-header nav [data-tab]").evaluate_all(
                "nodes => nodes.map(node => node.dataset.tab)"
            ) == ["home", "jobs", "applications", "sources", "employers", "queue", "profile", "settings", "application-history", "guide"]
            page.locator("#profile-menu [data-tab='settings']").click()
            page.wait_for_function("!document.querySelector('#settings').hasAttribute('aria-busy')")
            page.get_by_role("tab", name="Search preferences").click()
            assert page.locator("#search-intent-form").is_visible()
            assert page.locator('#search-intent-form [name="auto_preferred_locations"]').is_checked()
            assert page.locator('#search-intent-form [name="preferred_locations"]').is_disabled()
            page.locator('#search-intent-form [name="auto_preferred_locations"]').uncheck()
            page.locator('#search-intent-form [name="preferred_locations"]').fill("Hanoi, Remote")
            page.locator('#search-intent-form [name="hard_location"]').uncheck()
            page.get_by_role("button", name="Save preferences").click()
            page.get_by_text("Search preferences saved", exact=True).wait_for()
            saved = client.get("/api/search-intent").json()
            assert saved["preference_modes"]["preferred_locations"] == "custom"
            assert saved["preferred_locations"] == ["Hanoi", "Remote"]
            assert saved["hard_constraints"]["location"] is False
            assert page.locator("#search-intent-panel").locator("#pref-strong-threshold").count() == 0
            page.get_by_role('tab', name='Draft automation').click()
            page.wait_for_function("document.querySelector('#auto-apply-form').dataset.initialized === 'true'")
            threshold = page.locator('#auto-apply-form [name="threshold"]')
            assert threshold.is_editable()
            threshold.fill("87")
            page.get_by_role("button", name="Save draft automation").click()
            page.get_by_text("Automatic draft preparation paused.", exact=True).wait_for()
            assert client.get("/api/search-intent").json()["strong_match_threshold"] == 87
        finally:
            browser.close()
