import asyncio
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from job_radar.chrome_auth_watch import CHROME_EPOCH_OFFSET, chrome_login_complete
from job_radar.collectors import AuthRequired, collect_facebook
from job_radar.db import now
from job_radar.settings import Settings
from job_radar.web import create_app


def write_cookies(profile, cookies, *, network=False):
    path = profile / "Default" / ("Network/Cookies" if network else "Cookies")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cookies(host_key TEXT, name TEXT, expires_utc INTEGER, "
                     "has_expires INTEGER, value TEXT, encrypted_value BLOB)")
        conn.executemany("INSERT INTO cookies VALUES(?,?,?,?,?,?)", cookies)


def session_cookies(*, expires=None, host=".facebook.com"):
    if expires is None:
        expires = int((time.time() + CHROME_EPOCH_OFFSET + 3600) * 1_000_000)
    return [(host, name, expires, 1, "", b"encrypted-session") for name in ("c_user", "xs")]


@pytest.mark.parametrize("cookies", [
    [],
    session_cookies()[:1],
    session_cookies(expires=1),
    session_cookies(host=".facebook.com.example.org"),
    [(".facebook.com", name, 0, 0, "", b"") for name in ("c_user", "xs")],
])
def test_setup_detects_facebook_logout_from_saved_profile(tmp_path, cookies):
    settings = Settings(tmp_path)
    app = create_app(settings)
    db = app.state.db
    db.set_setting("social_login_completed_at_facebook", now())
    db.set_setting("social_login_completed_at_linkedin", now())
    write_cookies(settings.browser_profile, cookies)

    browser = TestClient(app).get("/api/setup").json()["browser"]

    assert browser["state"] == "reauth_required"
    assert browser["sites"] == ["facebook"]
    assert browser["connected_sites"] == ["linkedin"]
    assert db.get_setting("social_reauth_required_facebook")
    assert db.get_setting("social_login_completed_at_facebook")
    restarted = create_app(settings)
    assert restarted.state.login_manager.status()["sites"] == ["facebook"]


@pytest.mark.parametrize("network,expires", [(False, None), (True, None), (False, 0)])
def test_setup_keeps_facebook_connected_with_active_session_cookies(tmp_path, network, expires):
    settings = Settings(tmp_path)
    app = create_app(settings)
    app.state.db.set_setting("social_login_completed_at_facebook", now())
    write_cookies(settings.browser_profile, session_cookies(expires=expires), network=network)

    browser = app.state.login_manager.status()

    assert browser["connected_sites"] == ["facebook"]
    assert browser["sites"] == []


@pytest.mark.parametrize("unreadable", [False, True])
def test_unavailable_cookie_database_does_not_claim_facebook_logout(tmp_path, unreadable):
    settings = Settings(tmp_path)
    app = create_app(settings)
    app.state.db.set_setting("social_login_completed_at_facebook", now())
    if unreadable:
        path = settings.browser_profile / "Default" / "Cookies"
        path.parent.mkdir(parents=True)
        path.write_text("unfinished Chrome database")

    assert app.state.login_manager.status()["sites"] == []


def test_expired_sites_are_not_reported_as_connected(tmp_path):
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("browser_login_completed_at", now())
    db.set_setting("social_reauth_required_facebook", {"detected_at": now()})

    assert app.state.login_manager.status()["connected_sites"] == ["linkedin"]


def test_facebook_settings_visit_without_session_is_not_login_completion(tmp_path):
    history = tmp_path / "Default" / "History"
    history.parent.mkdir()
    started = time.time()
    with sqlite3.connect(history) as conn:
        conn.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT);"
                           "CREATE TABLE visits(url INTEGER, visit_time INTEGER);")
        conn.execute("INSERT INTO urls VALUES(1, 'https://www.facebook.com/settings')")
        conn.execute("INSERT INTO visits VALUES(1, ?)",
                     (int((started + CHROME_EPOCH_OFFSET + 1) * 1_000_000),))
    write_cookies(tmp_path, [])

    assert not chrome_login_complete(tmp_path, "facebook", started)


def test_facebook_home_after_sign_in_is_login_completion_despite_other_site_visits(tmp_path):
    history = tmp_path / "Default" / "History"
    history.parent.mkdir()
    started = time.time()
    at = int((started + CHROME_EPOCH_OFFSET + 1) * 1_000_000)
    with sqlite3.connect(history) as conn:
        conn.executescript("CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT);"
                           "CREATE TABLE visits(url INTEGER, visit_time INTEGER);")
        conn.execute("INSERT INTO urls VALUES(1, 'https://www.facebook.com/')")
        conn.execute("INSERT INTO urls VALUES(2, 'https://chatgpt.com/?temporary-chat=true')")
        conn.execute("INSERT INTO visits VALUES(1, ?)", (at,))
        conn.execute("INSERT INTO visits VALUES(2, ?)", (at + 1,))
    write_cookies(tmp_path, session_cookies())

    assert chrome_login_complete(tmp_path, "facebook", started)


def test_facebook_session_restored_after_failed_launcher_is_recognized(tmp_path):
    settings = Settings(tmp_path)
    app = create_app(settings)
    db = app.state.db
    db.set_setting("social_login_completed_at_facebook", now())
    write_cookies(settings.browser_profile, [])
    assert app.state.login_manager.status()["sites"] == ["facebook"]
    db.execute("UPDATE sources SET last_attempt_at=? WHERE kind='facebook'", (now(),))
    with sqlite3.connect(settings.browser_profile / "Default" / "Cookies") as conn:
        conn.executemany("INSERT INTO cookies VALUES(?,?,?,?,?,?)", session_cookies())
    manager = app.state.login_manager
    manager.state = "failed"
    manager.site = "facebook"
    manager.error = "Chrome closed before sign-in completed"

    browser = TestClient(app).get("/api/setup").json()["browser"]

    assert browser["state"] == "saved"
    assert browser["error"] is None
    assert browser["connected_sites"] == ["facebook"]
    assert browser["sites"] == []
    assert db.get_setting("social_reauth_required_facebook") is None


@pytest.mark.parametrize("location,locale", [
    ("group", "en"), ("group", "vi"), ("post", "en"), ("post", "vi"),
])
def test_facebook_login_form_stops_collection_even_with_public_post_preview(location, locale):
    group = "https://www.facebook.com/groups/123/"
    post = group + "posts/456/"
    text = "Hiring an AI Engineer to build machine learning systems with Python and data."
    preview = '<div data-ad-rendering-role="story_message">' + text + '</div>'
    feed = preview + f'<a href="{post}">Post</a>'
    label = "Log in" if locale == "en" else "Đăng nhập"
    login = ('<p>' + "Public group preview. " * 100 + '</p>' +
             '<form><input name="email"><input name="pass" type="password">' +
             f'<button>{label}</button></form>')
    logged_out = preview + login

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context()

                async def respond(route):
                    html = logged_out if location == "group" or "/posts/" in route.request.url else feed
                    await route.fulfill(content_type="text/html", body=html)

                await context.route("https://www.facebook.com/**", respond)
                with pytest.raises(AuthRequired):
                    await collect_facebook(context, {"url": group, "config": {"max_posts": 1}})
                assert context.pages == []
            finally:
                await browser.close()

    asyncio.run(run())


def test_hidden_login_form_does_not_block_authenticated_group_collection():
    html = ('<div hidden><input name="email"><input name="pass" type="password"></div>'
            '<div data-ad-rendering-role="story_message">Community announcement</div>')

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                await context.route("https://www.facebook.com/**", lambda route:
                                    route.fulfill(content_type="text/html", body=html))
                assert await collect_facebook(context, {
                    "url": "https://www.facebook.com/groups/123/", "config": {"max_posts": 1},
                }) == []
            finally:
                await browser.close()

    asyncio.run(run())
