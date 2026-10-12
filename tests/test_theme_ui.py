import httpx
import pytest

from test_ui_regression_gate import (
    _a11y_issues, _assert_no_page_overflow, _capture, _new_page, _stabilize,
    _text_contrast_issues, browser, ui_server,
)


def assert_theme(page, theme: str) -> None:
    page.wait_for_function("theme => document.documentElement.dataset.theme === theme", arg=theme)
    assert page.locator("html").evaluate("node => getComputedStyle(node).colorScheme") == theme


def test_theme_follows_system_until_overridden_and_persists(ui_server, browser) -> None:
    context = browser.new_context(color_scheme="dark")
    page = context.new_page()
    try:
        page.goto(ui_server["base_url"])
        selector = page.get_by_label("Theme", exact=True)
        assert selector.input_value() == "system"
        assert_theme(page, "dark")

        page.emulate_media(color_scheme="light")
        assert_theme(page, "light")
        selector.select_option("dark")
        assert_theme(page, "dark")
        page.emulate_media(color_scheme="dark")
        page.emulate_media(color_scheme="light")
        assert_theme(page, "dark")
        assert page.evaluate("localStorage.getItem('job-radar-theme')") == "dark"

        page.reload()
        assert_theme(page, "dark")
        assert selector.input_value() == "dark"
        selector.select_option("light")
        page.emulate_media(color_scheme="dark")
        assert_theme(page, "light")
        selector.select_option("system")
        assert_theme(page, "dark")
        page.reload()
        assert selector.input_value() == "system"
        page.emulate_media(color_scheme="light")
        assert_theme(page, "light")
    finally:
        context.close()


def test_theme_changes_sync_across_tabs(ui_server, browser) -> None:
    context = browser.new_context(color_scheme="light")
    first, second = context.new_page(), context.new_page()
    try:
        first.goto(ui_server["base_url"])
        second.goto(ui_server["base_url"])
        first.get_by_label("Theme", exact=True).select_option("dark")
        assert_theme(second, "dark")
        assert second.get_by_label("Theme", exact=True).input_value() == "dark"
        first.evaluate("localStorage.clear()")
        assert_theme(second, "light")
        assert second.get_by_label("Theme", exact=True).input_value() == "system"
    finally:
        context.close()


@pytest.mark.parametrize("storage_state", ["invalid", "blocked"])
def test_theme_handles_unavailable_or_invalid_storage(ui_server, browser, storage_state) -> None:
    context = browser.new_context(color_scheme="dark")
    if storage_state == "blocked":
        context.add_init_script("""
          Object.defineProperty(window, 'localStorage', {get() {throw new Error('Storage blocked')}});
        """)
    else:
        context.add_init_script("localStorage.setItem('job-radar-theme', 'invalid')")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(ui_server["base_url"])
        assert_theme(page, "dark")
        page.get_by_label("Theme", exact=True).select_option("light")
        assert_theme(page, "light")
        assert not errors
    finally:
        context.close()


def test_saved_theme_is_applied_before_first_paint(ui_server, browser) -> None:
    context = browser.new_context(color_scheme="light")
    context.add_init_script("""
      localStorage.setItem('job-radar-theme', 'dark');
      new PerformanceObserver((list) => {
        if (list.getEntries().some(entry => entry.name === 'first-contentful-paint')) {
          window.themeAtFirstPaint = document.documentElement.dataset.theme;
        }
      }).observe({type:'paint', buffered:true});
    """)
    page = context.new_page()
    try:
        page.goto(ui_server["base_url"])
        page.wait_for_function("window.themeAtFirstPaint !== undefined")
        assert page.evaluate("window.themeAtFirstPaint") == "dark"
        assert_theme(page, "dark")
    finally:
        context.close()


def test_theme_script_is_served_and_versioned(ui_server) -> None:
    with httpx.Client(base_url=ui_server["base_url"]) as client:
        html = client.get("/").text
        assert 'src="/theme.js?v=' in html
        assert html.index('/theme.js') < html.index('/app.css')
        script = client.get("/theme.js")
        assert script.status_code == 200
        assert 'javascript' in script.headers['content-type']
        assert script.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 1000}, {"width": 390, "height": 844}])
def test_dark_mode_covers_expanded_settings_guide_and_application_package(ui_server, browser, viewport) -> None:
    page = _new_page(browser, viewport)
    page.context.add_init_script("localStorage.setItem('job-radar-theme', 'dark')")
    size = "mobile" if viewport["width"] < 600 else "desktop"

    def check_surface(name):
        _assert_no_page_overflow(page, name, size)
        assert not _text_contrast_issues(page)
        assert not _a11y_issues(page)
        _capture(page, name, size)

    try:
        page.goto(ui_server["base_url"] + "/#settings")
        _stabilize(page, "#settings.active")
        page.locator("#search-intent-panel > summary").click()
        page.locator("#search-intent-advanced > summary").click()
        page.locator("#search-intent-form").scroll_into_view_if_needed()
        check_surface("settings-expanded-dark")
        page.locator("#social-sign-in-panel > summary").click()
        page.locator("#social-sign-in-panel").scroll_into_view_if_needed()
        check_surface("settings-connections-dark")

        page.goto(ui_server["base_url"] + "/#guide")
        _stabilize(page, "#guide.active")
        check_surface("guide-dark")

        page.goto(ui_server["base_url"] + f"/#applications/{ui_server['draft_id']}")
        _stabilize(page, "#applications.active")
        page.locator("#btn-next-to-package").click()
        page.locator(".application-preview-pages img").first.wait_for()
        assert page.locator(".application-resume-preview img").first.evaluate(
            "node => getComputedStyle(node).backgroundColor"
        ) == "rgb(255, 255, 255)"
        page.locator(".application-cv-details > summary").click()
        page.locator("#draft-name").fill("Alex Updated")
        page.locator("#draft-name").scroll_into_view_if_needed()
        check_surface("application-editor-dark")
        page.locator("#btn-sticky-discard-draft").click()
        page.locator("#application-discard-confirm").wait_for()
        check_surface("application-dialog-dark")
        page.locator('#application-discard-confirm button[value="cancel"]').click()
    finally:
        page.context.close()
