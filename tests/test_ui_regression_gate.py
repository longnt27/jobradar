import hashlib
from io import BytesIO
import json
import os
import socket
import sys
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
from PIL import Image

from job_radar.drafting import prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


ROOT = Path(__file__).parents[1]
MANIFEST = json.loads((Path(__file__).with_name("ui_regression_manifest.json")).read_text())
# Screenshot fingerprints are platform-sensitive: the shipped macOS reference
# and reviewed GitHub Actions Ubuntu reference are intentionally independent.
BASELINE_FILE = "ui_visual_baselines_linux.json" if sys.platform.startswith("linux") else "ui_visual_baselines.json"
VISUAL_BASELINES = json.loads((Path(__file__).with_name(BASELINE_FILE)).read_text())
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
        "apply_url": "https://example.org/apply",
    }).json()
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='done',score=91,state='interesting',decision_state='shortlisted',"
        "work_mode='Hybrid',published_at='2026-10-04T09:00:00+00:00',score_detail=? WHERE id=?",
        (json.dumps({"facts": {"seniority": "Mid", "work_mode": "Hybrid"}}), job["id"]),
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
        "work_mode='Remote',published_at='2026-10-03T09:00:00+00:00',score_detail=? WHERE id=?",
        (json.dumps({"facts": {"seniority": "Mid", "work_mode": "Remote"}}), second["id"]),
    )

    project_id = client.post("/api/evidence", json={
        "kind": "project",
        "title": "Vision Search",
        "claim": "Built a production computer-vision search pipeline.",
        "support": [],
        "approved": True,
    }).json()["id"]

    draft = prepare_draft(app.state.db, app.state.settings, job["id"], "template")
    app.state.auto_apply_manager.register_review(draft)
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
    fragment = pattern.format(draft_id=server["draft_id"], job_id=server["job_id"])
    return f"{server['base_url']}/{fragment}"


def _new_page(browser, viewport: dict) -> Page:
    context = browser.new_context(
        viewport=viewport,
        locale="en-US",
        timezone_id="Asia/Bangkok",
        reduced_motion="reduce",
        color_scheme="light",
    )
    page = context.new_page()
    page.set_default_timeout(6000)
    issues = {"api": [], "page": [], "pending": set()}

    def record_failed_request(request) -> None:
        if "/api/" not in request.url:
            return
        issues["pending"].discard(request.url)
        failure = str(request.failure or "")
        expected_pdf_abort = request.url.endswith("/resume") and "ERR_ABORTED" in failure
        if not expected_pdf_abort:
            issues["api"].append(f"request failed {request.method} {request.url}: {failure}")

    page.on("request", lambda request: issues["pending"].add(request.url)
            if "/api/" in request.url else None)
    page.on("requestfinished", lambda request: issues["pending"].discard(request.url))
    page.on("requestfailed", record_failed_request)
    page.on("response", lambda response: issues["api"].append(
        f"{response.status} {response.request.method} {response.url}"
    ) if "/api/" in response.url and response.status >= 400 else None)
    page.on("pageerror", lambda error: issues["page"].append(str(error)))
    page._ui_issues = issues
    return page


def _stabilize(page: Page, active_selector: str) -> None:
    page.locator(active_selector).wait_for()
    try:
        page.wait_for_function("!document.querySelector('.tab.active')?.hasAttribute('aria-busy')", timeout=3_000)
    except PlaywrightTimeoutError as error:
        notice = page.locator("#notice")
        notice_text = notice.inner_text() if notice.is_visible() else ""
        pending = sorted(page._ui_issues["pending"])
        raise AssertionError(
            "Active tab stayed aria-busy. "
            f"Pending API requests: {pending or ['none']}; "
            f"page errors: {page._ui_issues['page'] or ['none']}; "
            f"API failures: {page._ui_issues['api'] or ['none']}; "
            f"notice: {notice_text or 'none'}"
        ) from error
    assert not page._ui_issues["page"], "Browser page errors:\n- " + "\n- ".join(page._ui_issues["page"])
    assert not page._ui_issues["api"], "API request failures:\n- " + "\n- ".join(page._ui_issues["api"])
    if page.locator("#applications.active").count() > 0:
        try:
            page.locator("#application-detail h2").wait_for(timeout=4000)
        except Exception:
            pass
    notice = page.locator("#notice")
    if notice.is_visible() and notice.get_attribute("role") == "alert":
        raise AssertionError(f"UI loader error: {notice.inner_text()}")
    page.add_style_tag(content="""
      *,*::before,*::after {
        animation: none !important;
        transition: none !important;
        caret-color: transparent !important;
      }
      #queue-updated-at,#notice,#tab-loading {
        visibility: hidden !important;
      }
    """)
    page.evaluate("""() => {
      document.querySelectorAll('.job-card-footer > span:first-child').forEach((node) => {
        node.textContent = 'Found recently';
        node.removeAttribute('title');
      });
      document.querySelectorAll('.application-card .item-meta:last-child').forEach((node) => {
        node.textContent = node.textContent.includes('Sent') ? 'Updated recently · Sent' : 'Updated recently';
      });
      document.querySelectorAll('.activity-item small, .job-sighting small').forEach((node) => {
        node.textContent = 'Activity recently';
      });
      document.querySelectorAll('.metric-subtext').forEach((node) => {
        if (node.textContent.includes('First seen')) node.textContent = 'First seen recently';
      });
      document.querySelectorAll('#application-detail p.hint, .activity-item-content strong').forEach((node) => {
        if (node.textContent.includes('Application draft created')) {
          node.textContent = 'Application draft created recently';
        }
      });
      const sinceLabel = document.querySelector('#jobs-since-label');
      const sinceCount = document.querySelector('#jobs-since-count');
      if (sinceLabel) sinceLabel.textContent = 'Since last visit';
      if (sinceCount) sinceCount.textContent = '0';
    }""")
    page.wait_for_timeout(120)


def _visual_fingerprint(data: bytes) -> str:
    with Image.open(BytesIO(data)) as image:
        normalized = image.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
        return hashlib.sha256(normalized.tobytes()).hexdigest()


def _capture(page: Page, surface: str, viewport_name: str) -> tuple[Path, str, int, str]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"{surface}-{viewport_name}.png"
    data = page.screenshot(path=str(path), full_page=False, animations="disabled")
    return path, hashlib.sha256(data).hexdigest(), len(data), _visual_fingerprint(data)


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
      const mainNav = document.querySelector('.app-header nav');
      if (!mainNav?.getAttribute('aria-label')) issues.push('main navigation has no aria-label');

      return issues;
    }""")


def _text_contrast_issues(page: Page) -> list[str]:
    return page.evaluate("""() => {
      const issues = [];
      const rgb = (value) => (value.match(/[\\d.]+/g) || []).map(Number);
      const blend = (front, back) => {
        const alpha = front[3] ?? 1;
        return front.slice(0, 3).map((channel, i) => channel * alpha + back[i] * (1 - alpha));
      };
      const background = (node) => {
        if (!node) return [255, 255, 255];
        return blend(rgb(getComputedStyle(node).backgroundColor), background(node.parentElement));
      };
      const luminance = (channels) => channels.map(channel => {
        const value = channel / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const text = walker.currentNode;
        const node = text.parentElement;
        if (!text.textContent.trim() || node.closest('script,style,[disabled],[hidden],.sr-only')) continue;
        const style = getComputedStyle(node);
        if (style.visibility !== 'visible' || style.display === 'none') continue;
        const range = document.createRange();
        range.selectNode(text);
        const box = range.getBoundingClientRect();
        if (!box.width || !box.height || box.bottom < 0 || box.top >= innerHeight) continue;
        const back = background(node);
        const foreground = blend(rgb(style.color), back);
        const a = luminance(foreground), b = luminance(back);
        const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
        const large = parseFloat(style.fontSize) >= 24 ||
          (parseFloat(style.fontSize) >= 18.66 && parseInt(style.fontWeight) >= 700);
        if (ratio < (large ? 3 : 4.5)) {
          issues.push(`${text.textContent.trim().slice(0, 65)}: ${ratio.toFixed(2)}:1`);
        }
      }
      return issues;
    }""")


@pytest.mark.parametrize("surface", list(MANIFEST["surfaces"]))
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_core_surfaces_have_stable_desktop_visual_contract(
    ui_server, browser, surface: str, theme: str
) -> None:
    viewport_name = "desktop"
    viewport = MANIFEST["viewports"][viewport_name]
    page = _new_page(browser, viewport)
    page.context.add_init_script(f"localStorage.setItem('job-radar-theme', '{theme}')")
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
        if theme == "dark":
            issues = _text_contrast_issues(page)
            assert not issues, f"{surface}/{viewport_name} dark text contrast issues: {issues}"
        if surface == "applications":
            manual_action = page.locator("#manual-send-disclosure summary")
            assert manual_action.is_visible()
            assert manual_action.bounding_box()["width"] > 100
            # The manual-send control has a browser interaction test; keep the
            # older cross-platform screenshot focused on the surrounding layout.
            page.locator("#application-manual-send").evaluate("element => element.style.display = 'none'")
        page.evaluate("window.scrollTo(0, 0)")
        snapshot_surface = surface if theme == "light" else f"{surface}-dark"
        path, digest, size, fingerprint = _capture(page, snapshot_surface, viewport_name)
        assert size > 4_000, f"{surface}/{viewport_name} screenshot looks blank ({size} bytes): {path}"
        baseline_key = f"{snapshot_surface}-{viewport_name}"
        assert baseline_key in VISUAL_BASELINES, f"Missing visual baseline for {baseline_key}"
        accepted_fingerprints = {VISUAL_BASELINES[baseline_key]}
        assert fingerprint in accepted_fingerprints, (
            f"{baseline_key} visual regression detected. "
            f"Expected one of {sorted(accepted_fingerprints)}, got {fingerprint}. "
            f"Inspect CI screenshot artifact: {path}"
        )
        (path.with_suffix(".sha256")).write_text(f"{digest}  {path.name}\\n")
    finally:
        page.context.close()


@pytest.mark.parametrize("surface", list(MANIFEST["surfaces"]))
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_core_surfaces_pass_automated_accessibility_smoke(ui_server, browser, surface: str, theme: str) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    page.context.add_init_script(f"localStorage.setItem('job-radar-theme', '{theme}')")
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
        page.locator('.app-header nav [data-tab="jobs"]').focus()
        assert page.evaluate("document.activeElement?.dataset.tab") == "jobs"
        page.keyboard.press("Enter")
        page.locator("#jobs.active").wait_for()
        page.locator('#job-filter-toggle').click()
        page.locator('#job-inbox').select_option('all')
        page.keyboard.press('Escape')
        _stabilize(page, "#jobs.active")
        card = page.locator("[data-job]").first
        card.focus()
        assert page.evaluate("document.activeElement?.hasAttribute('data-job')") is True
        page.keyboard.press("Enter")
        page.wait_for_function("document.activeElement?.id === 'job-detail'")
        assert card.get_attribute("aria-pressed") == "true"

        page.locator('.app-header nav [data-tab="applications"]').focus()
        page.keyboard.press("Enter")
        page.locator("#applications.active").wait_for()
        _stabilize(page, "#applications.active")
        application = page.locator("[data-application]").first
        application.focus()
        page.keyboard.press("Enter")
        page.wait_for_function("document.activeElement?.id === 'application-detail'")
        assert page.locator("#application-detail").is_visible()
        assert application.get_attribute("aria-pressed") == "true"
    finally:
        page.context.close()


def test_main_navigation_uses_top_bar_without_sidebar(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(f"{ui_server['base_url']}/#home")
        _stabilize(page, "#home.active")
        assert page.locator("aside.sidebar").count() == 0
        header = page.locator(".app-header")
        nav = header.get_by_role("navigation", name="Main navigation")
        assert nav.is_visible()
        assert header.evaluate("element => element.nextElementSibling.tagName") == "MAIN"
        assert page.locator("main").bounding_box()["x"] == 0
        jobs = nav.locator('[data-tab="jobs"]')
        jobs.click()
        page.locator("#jobs.active").wait_for()
        assert jobs.get_attribute("aria-current") == "page"
        _assert_no_page_overflow(page, "top-navigation", "desktop")
    finally:
        page.context.close()


def test_top_navigation_uses_stacked_tabs_and_profile_menu(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(f"{ui_server['base_url']}/#home")
        _stabilize(page, "#home.active")
        nav = page.get_by_role("navigation", name="Main navigation")
        assert page.locator(".brand span").count() == 0
        assert page.locator(".brand-logo").get_attribute("alt") == "Job Radar"
        assert nav.locator(".nav-primary [data-tab]").evaluate_all(
            "nodes => nodes.map(node => node.dataset.tab)"
        ) == ["home", "jobs", "applications", "sources", "employers"]
        assert nav.locator(".nav-queue[data-tab='queue']").is_visible()
        assert nav.locator(".nav-primary [data-tab]").evaluate_all(
            "nodes => nodes.every(node => node.querySelector('svg').getBoundingClientRect().bottom <= node.querySelector('span').getBoundingClientRect().top)"
        )
        menu_button = nav.get_by_role("button", name="Profile menu")
        menu = nav.locator("#profile-menu")
        assert menu_button.get_attribute("aria-expanded") == "false"
        assert not menu.is_visible()
        menu_button.click()
        assert menu_button.get_attribute("aria-expanded") == "true"
        assert menu.locator("[data-tab]").evaluate_all(
            "nodes => nodes.map(node => node.dataset.tab)"
        ) == ["profile", "settings", "application-history", "guide"]
        assert menu.get_by_role("button", name="My profile").is_visible()
        page.keyboard.press("Escape")
        assert not menu.is_visible()
        menu_button.click()
        menu.get_by_role("button", name="Settings").click()
        page.locator("#settings.active").wait_for()
        assert not menu.is_visible()
        assert menu_button.get_attribute("aria-expanded") == "false"
        menu_button.click()
        menu.get_by_role("button", name="Application history").click()
        page.locator("#application-history.active").wait_for()
        nav.locator(".nav-queue").click()
        page.locator("#queue.active").wait_for()
        assert page.get_by_role("heading", name="Queue", exact=True).is_visible()
        assert page.locator("#queue-summary, #queue [id$='-count'], #queue .queue-row-order").count() == 0
        nav_box = nav.bounding_box()
        utility_box = nav.locator(".nav-utility").bounding_box()
        assert utility_box["x"] + utility_box["width"] >= nav_box["x"] + nav_box["width"] - 1
    finally:
        page.context.close()


def test_top_navigation_does_not_cover_application_screen_navigation(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(_surface_url(ui_server, "applications"))
        _stabilize(page, "#applications.active")
        page.evaluate("window.scrollTo(0, 650)")
        header = page.locator(".app-header").bounding_box()
        screen_nav = page.locator(".application-screen-nav").first.bounding_box()
        assert screen_nav["y"] >= header["y"] + header["height"]
    finally:
        page.context.close()


def test_application_approve_and_send_is_keyboard_operable_without_redundant_confirmation(ui_server, browser) -> None:
    page = _new_page(browser, MANIFEST["viewports"]["desktop"])
    try:
        page.goto(_surface_url(ui_server, "applications"))
        _stabilize(page, "#applications.active")
        next_pkg = page.locator("#btn-next-to-package")
        if next_pkg.is_visible():
            next_pkg.click()
        send = page.locator("#send-draft")
        send.wait_for()
        assert send.is_enabled()
        send.focus()
        with page.expect_request(lambda request: "/api/applications/" in request.url and request.url.endswith("/approve") and request.method == "POST"):
            page.keyboard.press("Enter")
        assert page.locator("#application-send-confirm").is_hidden()
    finally:
        page.context.close()


def test_visual_gate_static_contract() -> None:
    css = (ROOT / "job_radar" / "static" / "app.css").read_text()
    html = (ROOT / "job_radar" / "static" / "index.html").read_text()
    assert "button:focus-visible" in css
    assert "prefers-reduced-motion:reduce" in css
    assert '<nav aria-label="Main navigation">' in html
    assert set(MANIFEST["viewports"]) == {"desktop"}
    assert set(VISUAL_BASELINES) == {
        f"{surface}{suffix}-{viewport}"
        for surface in MANIFEST["surfaces"]
        for viewport in MANIFEST["viewports"]
        for suffix in ("", "-dark")
    }
    assert set(MANIFEST["surfaces"]) >= {
        "home", "jobs", "applications", "profile", "projects", "sources", "employers", "queue"
    }
