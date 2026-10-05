import hashlib
import json
import os
import socket
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import Page, sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


ROOT = Path(__file__).parents[1]
MANIFEST = json.loads((Path(__file__).with_name("ui_regression_manifest.json")).read_text())
ARTIFACT_DIR = ROOT / os.environ.get("JOB_RADAR_UI_ARTIFACTS", "artifacts/ui-regression")


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("ui-gate")
    app = create_app(Settings(data_dir))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)

    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example",
        "email": "alex@example.org",
        "phone": "+84 900 000 000",
        "location": "Hanoi",
        "experience": [{
            "company": "Prior Co",
            "role": "ML Engineer",
            "dates": "2024-2026",
            "bullets": ["Built reliable Python systems.", "Shipped production search tooling."],
        }],
        "skills": ["Python", "Machine Learning"],
    })
    assert client.put("/api/profile", json=profile).status_code == 200

    job = client.post("/api/jobs/import", json={
        "company": "Example Robotics",
        "title": "AI Engineer",
        "description": "Build reliable perception and search systems with Python.",
        "location": "Hanoi",
        "apply_url": "mailto:jobs@example.org",
    }).json()
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='done',score=91,state='interesting',"
        "work_mode='Hybrid',seniority='Mid',published_at='2026-10-04T09:00:00+00:00' WHERE id=?",
        (job["id"],),
    )

    second = client.post("/api/jobs/import", json={
        "company": "Other Systems",
        "title": "Research Engineer",
        "description": "Research and deploy machine learning systems.",
        "location": "Remote",
        "apply_url": "https://example.org/apply",
    }).json()
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='done',score=76,state='new',"
        "work_mode='Remote',seniority='Mid',published_at='2026-10-03T09:00:00+00:00' WHERE id=?",
        (second["id"],),
    )

    project_id = client.post("/api/evidence", json={
        "kind": "project",
        "title": "Vision Search",
        "claim": "Built a production computer-vision search pipeline.",
        "support": [],
        "approved": True,
    }).json()["id"]

    draft_response = client.post(f"/api/jobs/{job['id']}/prepare", json={"provider": "template"})
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    draft = client.patch(f"/api/applications/{draft['id']}", json={
        "destination": {
            "kind": "email",
            "email": "jobs@example.org",
            "action_type": "email",
            "provenance": "manual_override",
            "confidence": "user_confirmed",
        }
    }).json()
    app.state.db.execute(
        "UPDATE auto_application_attempts SET status='awaiting_review',review_hash=? WHERE draft_id=?",
        (draft["package_hash"], draft["id"]),
    )
    assert client.post("/api/setup/smtp", json={
        "host": "smtp.example.org",
        "port": 587,
        "user": "alex",
        "password": "secret",
        "from_address": "alex@example.org",
    }).status_code == 200

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
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "job_id": job["id"],
            "second_job_id": second["id"],
            "draft_id": draft["id"],
            "project_id": project_id,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def _surface_url(server: dict, name: str) -> str:
    pattern = MANIFEST["surfaces"][name]["hash"]
    fragment = pattern.format(draft_id=server["draft_id"])
    return f"{server['base_url']}/{fragment}"


def _new_page(browser, viewport: dict) -> Page:
    context = browser.new_context(
        viewport=viewport,
        locale="en-US",
        timezone_id="Asia/Bangkok",
        reduced_motion="reduce",
    )
    page = context.new_page()
    page.set_default_timeout(6000)
    return page


def _stabilize(page: Page, active_selector: str) -> None:
    page.locator(active_selector).wait_for()
    page.add_style_tag(content="""
      *,*::before,*::after {
        animation: none !important;
        transition: none !important;
        caret-color: transparent !important;
      }
      #clock,#queue-updated-at,#notice,#tab-loading {
        visibility: hidden !important;
      }
    """)
    page.wait_for_timeout(120)


def _capture(page: Page, surface: str, viewport_name: str) -> tuple[Path, str, int]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{surface}-{viewport_name}.png"
    data = page.screenshot(path=str(path), full_page=False, animations="disabled")
    return path, hashlib.sha256(data).hexdigest(), len(data)


def _assert_no_page_overflow(page: Page, surface: str, viewport_name: str) -> None:
    metrics = page.evaluate("""() => ({
      viewport: document.documentElement.clientWidth,
      documentWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
      main: (() => {
        const node = document.querySelector('main');
        const box = node.getBoundingClientRect();
        return {left: box.left, right: box.right, width: box.width};
      })(),
      shell: (() => {
        const node = document.querySelector('.shell');
        const box = node.getBoundingClientRect();
        return {left: box.left, right: box.right, width: box.width};
      })()
    })""")
    overflow = metrics["documentWidth"] - metrics["viewport"]
    assert overflow <= 1, (
        f"{surface}/{viewport_name} creates {overflow}px horizontal page overflow: {metrics}"
    )
    assert metrics["main"]["width"] > 0
    assert metrics["shell"]["width"] > 0


def _a11y_issues(page: Page) -> list[str]:
    return page.evaluate("""() => {
      const issues = [];
      const visible = (node) => {
        const style = getComputedStyle(node);
        const box = node.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' &&
          box.width > 0 && box.height > 0 && !node.closest('[hidden]');
      };
      const referencedText = (node, attr) => {
        const ids = (node.getAttribute(attr) || '').trim().split(/\\s+/).filter(Boolean);
        return ids.map((id) => document.getElementById(id)?.textContent?.trim() || '').join(' ').trim();
      };
      const name = (node) => (
        node.getAttribute('aria-label') ||
        referencedText(node, 'aria-labelledby') ||
        node.textContent?.trim() ||
        node.getAttribute('title') ||
        (node instanceof HTMLInputElement ? node.value : '')
      ).trim();

      const ids = [...document.querySelectorAll('[id]')].map((node) => node.id);
      const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
      duplicates.forEach((id) => issues.push('duplicate id: #' + id));

      for (const node of document.querySelectorAll('button,a[href],[role="button"],[role="tab"],summary')) {
        if (visible(node) && !name(node)) {
          issues.push('interactive control has no accessible name: ' + node.outerHTML.slice(0, 180));
        }
      }

      for (const node of document.querySelectorAll('input,select,textarea')) {
        if (!visible(node) || node.type === 'hidden') continue;
        const labelled = node.labels?.length || node.getAttribute('aria-label') ||
          referencedText(node, 'aria-labelledby') || node.getAttribute('title');
        if (!labelled) {
          issues.push('form control has no label: ' + node.outerHTML.slice(0, 180));
        }
      }

      for (const node of document.querySelectorAll('iframe')) {
        if (visible(node) && !node.getAttribute('title')) {
          issues.push('iframe has no title: ' + node.outerHTML.slice(0, 180));
        }
      }

      for (const node of document.querySelectorAll('[role="tab"]')) {
        if (visible(node) && !node.hasAttribute('aria-selected')) {
          issues.push('tab missing aria-selected: ' + (name(node) || node.outerHTML.slice(0, 120)));
        }
      }

      for (const node of document.querySelectorAll('[aria-labelledby],[aria-describedby],[aria-controls]')) {
        for (const attr of ['aria-labelledby','aria-describedby','aria-controls']) {
          if (!node.hasAttribute(attr)) continue;
          for (const id of node.getAttribute(attr).trim().split(/\\s+/)) {
            if (id && !document.getElementById(id)) {
              issues.push(attr + ' references missing #' + id);
            }
          }
        }
      }

      for (const node of document.querySelectorAll('[aria-hidden="true"]')) {
        if (!visible(node)) continue;
        const focusable = node.matches('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])') ||
          node.querySelector('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])');
        if (focusable) issues.push('aria-hidden subtree contains focusable content: ' + node.outerHTML.slice(0, 160));
      }

      if (document.querySelectorAll('main').length !== 1) {
        issues.push('expected exactly one main landmark, found ' + document.querySelectorAll('main').length);
      }
      const mainNav = document.querySelector('.sidebar nav');
      if (!mainNav?.getAttribute('aria-label')) issues.push('main navigation has no aria-label');

      return issues;
    }""")


@pytest.mark.parametrize("viewport_name", ["desktop", "tablet", "mobile"])
@pytest.mark.parametrize("surface", list(MANIFEST["surfaces"]))
def test_core_surfaces_have_stable_responsive_visual_contract(
    ui_server, browser, viewport_name: str, surface: str
) -> None:
    viewport = MANIFEST["viewports"][viewport_name]
    page = _new_page(browser, viewport)
    try:
        page.goto(_surface_url(ui_server, surface))
        config = MANIFEST["surfaces"][surface]
        _stabilize(page, config["active"])
        for selector in config["anchors"]:
            anchor = page.locator(selector).first
            anchor.wait_for()
            assert anchor.is_visible(), f"{surface}/{viewport_name}: {selector} is not visible"
            box = anchor.bounding_box()
            assert box and box["width"] > 20 and box["height"] > 5, (
                f"{surface}/{viewport_name}: {selector} collapsed to {box}"
            )
        _assert_no_page_overflow(page, surface, viewport_name)
        path, digest, size = _capture(page, surface, viewport_name)
        assert size > 4_000, f"{surface}/{viewport_name} screenshot looks blank ({size} bytes): {path}"
        (path.with_suffix(".sha256")).write_text(f"{digest}  {path.name}\\n")
    finally:
        page.context.close()


@pytest.mark.parametrize("surface", list(MANIFEST["surfaces"]))
def test_core_surfaces_pass_automated_accessibility_smoke(ui_server, browser, surface: str) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(_surface_url(ui_server, surface))
        config = MANIFEST["surfaces"][surface]
        _stabilize(page, config["active"])
        issues = _a11y_issues(page)
        assert not issues, f"{surface} accessibility issues:\\n- " + "\\n- ".join(issues)
    finally:
        page.context.close()


def test_desktop_keyboard_path_reaches_navigation_jobs_and_application_review(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(f"{ui_server['base_url']}/#home")
        _stabilize(page, "#home.active")
        page.locator('.sidebar nav [data-tab="jobs"]').focus()
        assert page.evaluate("document.activeElement?.dataset.tab") == "jobs"
        page.keyboard.press("Enter")
        page.locator("#jobs.active").wait_for()

        card = page.locator("[data-job]").first
        card.focus()
        assert page.evaluate("document.activeElement?.hasAttribute('data-job')") is True
        page.keyboard.press("Enter")
        page.locator("#job-detail h2").wait_for()

        page.locator('.sidebar nav [data-tab="applications"]').focus()
        page.keyboard.press("Enter")
        page.locator("#applications.active").wait_for()
        application = page.locator("[data-application]").first
        application.focus()
        page.keyboard.press("Enter")
        page.locator("#application-detail h2").wait_for()
        assert page.locator("#application-detail").is_visible()
    finally:
        page.context.close()


def test_mobile_navigation_is_keyboard_reachable_without_page_overflow(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["mobile"])
    try:
        page.goto(f"{ui_server['base_url']}/#home")
        _stabilize(page, "#home.active")
        nav = page.locator(".sidebar nav")
        assert nav.evaluate("node => getComputedStyle(node).display") == "flex"
        assert nav.evaluate("node => ['auto','scroll'].includes(getComputedStyle(node).overflowX)") is True
        jobs = nav.locator('[data-tab="jobs"]')
        jobs.focus()
        assert page.evaluate("document.activeElement?.dataset.tab") == "jobs"
        page.keyboard.press("Enter")
        page.locator("#jobs.active").wait_for()
        _assert_no_page_overflow(page, "mobile-navigation", "mobile")
    finally:
        page.context.close()


def test_application_send_confirmation_is_keyboard_operable(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(_surface_url(ui_server, "applications"))
        _stabilize(page, "#applications.active")
        send = page.locator("#send-draft")
        send.wait_for()
        assert send.is_enabled()
        send.focus()
        page.keyboard.press("Enter")
        dialog = page.locator("#application-send-confirm")
        dialog.wait_for(state="visible")
        assert "jobs@example.org" in page.locator("#application-send-confirm-target").inner_text()
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")
        assert page.locator("#send-draft").is_visible()
    finally:
        page.context.close()


def test_visual_gate_static_contract() -> None:
    css = (ROOT / "job_radar" / "static" / "app.css").read_text()
    html = (ROOT / "job_radar" / "static" / "index.html").read_text()
    assert "button:focus-visible" in css
    assert "@media(max-width:900px)" in css
    assert "prefers-reduced-motion:reduce" in css
    assert '<nav aria-label="Main navigation">' in html
    assert set(MANIFEST["viewports"]) == {"desktop", "tablet", "mobile"}
    assert set(MANIFEST["surfaces"]) >= {
        "home", "jobs", "applications", "profile", "projects", "sources", "employers", "queue"
    }
