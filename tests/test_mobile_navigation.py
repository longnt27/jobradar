"""Mobile navigation behavior, covering list/detail history and desktop parity."""
from playwright.sync_api import expect
from test_ui_regression_gate import ui_server, browser


def _mobile(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=1)
    page = context.new_page()
    page.set_default_timeout(8000)
    return context, page


def test_mobile_drawer_navigation_and_escape(ui_server, browser):
    context, page = _mobile(browser)
    try:
        page.goto(ui_server["base_url"] + "/#home")
        toggle = page.get_by_role("button", name="Open navigation menu")
        expect(toggle).to_be_visible()
        expect(page.locator("#main-navigation")).to_be_hidden()
        toggle.click()
        expect(page.locator("#main-navigation")).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        page.locator("#main-navigation [data-tab='jobs']").click()
        expect(page.locator("#jobs.active")).to_be_visible()
        expect(page.locator("#main-navigation")).to_be_hidden()
        page.get_by_role("button", name="Open navigation menu").click()
        page.keyboard.press("Escape")
        expect(page.locator("#main-navigation")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


def test_mobile_jobs_list_detail_back_and_deep_link(ui_server, browser):
    context, page = _mobile(browser)
    try:
        page.goto(ui_server["base_url"] + "/#jobs?inbox=all")
        expect(page.locator("#jobs.active")).to_be_visible()
        expect(page.locator("#job-list [data-job]").first).to_be_visible()
        expect(page.locator("#job-detail")).to_be_hidden()
        page.locator("#job-list [data-job]").first.click()
        expect(page.locator("#job-detail")).to_be_visible()
        expect(page.locator(".job-list-pane")).to_be_hidden()
        assert "job=" in page.url
        page.go_back()
        expect(page.locator("#job-list [data-job]").first).to_be_visible()
        expect(page.locator("#job-detail")).to_be_hidden()
        page.locator("#job-list [data-job]").first.click()
        page.locator("#jobs-back-to-list").click()
        expect(page.locator("#job-list [data-job]").first).to_be_visible()
        assert "job=" not in page.url
        page.goto(ui_server["base_url"] + "/#jobs?inbox=all&job=" + ui_server["job_id"])
        expect(page.locator("#job-detail")).to_be_visible()
    finally:
        context.close()


def test_mobile_applications_list_detail_back_and_deep_link(ui_server, browser):
    context, page = _mobile(browser)
    try:
        page.goto(ui_server["base_url"] + "/#applications")
        expect(page.locator("#application-list [data-application]").first).to_be_visible()
        expect(page.locator("#application-detail")).to_be_hidden()
        page.locator("#application-list [data-application]").first.click()
        expect(page.locator("#application-detail")).to_be_visible()
        expect(page.locator(".application-list-pane")).to_be_hidden()
        assert "/"+ui_server["draft_id"] in page.url
        page.go_back()
        expect(page.locator("#application-list [data-application]").first).to_be_visible()
        expect(page.locator("#application-detail")).to_be_hidden()
        page.goto(ui_server["base_url"] + "/#applications/" + ui_server["draft_id"])
        expect(page.locator("#application-detail")).to_be_visible()
        page.locator("#applications-back-to-list").click()
        expect(page.locator("#application-list [data-application]").first).to_be_visible()
    finally:
        context.close()


def test_desktop_keeps_side_by_side_and_navigation(ui_server, browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(ui_server["base_url"] + "/#jobs?inbox=all")
        expect(page.locator(".nav-primary")).to_be_visible()
        expect(page.locator("#mobile-menu-toggle")).to_be_hidden()
        expect(page.locator(".job-list-pane")).to_be_visible()
        expect(page.locator("#job-detail")).to_be_visible()
    finally:
        context.close()
