import json

import pytest

from test_ui_regression_gate import (
    _assert_no_page_overflow, _capture, _new_page, _text_contrast_issues, browser, ui_server,
)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("viewport", [{"width": 1600, "height": 1000}])
def test_interrupted_preparation_is_readable_and_has_one_status(ui_server, browser, theme, viewport):
    page = _new_page(browser, viewport)
    page.context.add_init_script(f"localStorage.setItem('job-radar-theme', '{theme}')")
    preparation = {
        "vacancy_id": "interrupted-preparation", "job_title": "AI Native Software Engineer",
        "company": "WEEDS VINA", "score": 90, "status": "needs_review", "category": "failed",
        "label": "Preparation stopped", "reason": "Job Radar stopped during preparation; review before sending",
        "provider": "chatgpt_web", "retryable": True, "updated_at": "2026-10-14T07:00:00Z",
    }
    page.route("**/api/application-preparations", lambda route: route.fulfill(json=[preparation]))
    deferred = []
    page.route(f"**/api/applications/{ui_server['draft_id']}", lambda route: deferred.append(route))
    try:
        page.goto(ui_server["base_url"] + "/#applications")
        card = page.locator('[data-preparation="interrupted-preparation"]')
        card.click()
        for route in deferred:
            with page.expect_response(lambda response: response.url.endswith(f"/api/applications/{ui_server['draft_id']}")):
                route.continue_()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert page.locator("#application-list").evaluate("node => node.scrollWidth <= node.clientWidth + 1")
        detail = page.locator("#application-detail")
        detail.get_by_role("heading", name="AI Native Software Engineer").wait_for()
        assert card.locator(".status-badge").count() == 1
        assert detail.locator(".application-alert").count() == 0
        assert "No draft was created" not in detail.inner_text()
        assert "Job Radar stopped before it finished creating this draft." in detail.inner_text()
        assert detail.get_by_role("button", name="Retry preparation").is_enabled()
        assert not _text_contrast_issues(page)
        _assert_no_page_overflow(page, "application-preparation", "desktop")
        _capture(page, f"application-preparation-{theme}", "desktop")
    finally:
        page.close()
