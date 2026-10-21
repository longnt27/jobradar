import asyncio
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
import time

import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import uvicorn

from job_radar import __version__
from job_radar.db import Database
from job_radar.settings import Settings
from job_radar.updates import cached_release_status, check_for_release, release_check_preference
from job_radar.web import create_app


def test_update_check_is_manual_until_user_enables_automatic(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    assert client.get('/api/update/preferences').json() == {'mode': 'manual'}
    status = client.get('/api/update/status').json()
    assert status['status'] == 'not_checked'
    assert status['checked_at'] is None

    assert client.put('/api/update/preferences', json={'mode': 'automatic'}).json() == {'mode': 'automatic'}
    assert client.get('/api/update/preferences').json() == {'mode': 'automatic'}
    assert client.put('/api/update/preferences', json={'mode': 'sometimes'}).status_code == 422
    assert release_check_preference(app.state.db) == {'mode': 'automatic'}
    assert client.put('/api/update/preferences', json={'mode': 'manual'}).json() == {'mode': 'manual'}


def test_release_check_comparison_and_cached_result(tmp_path: Path) -> None:
    db = Database(tmp_path / 'job_radar.sqlite3')

    async def check(tag: str):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'tag_name': tag}))
        async with httpx.AsyncClient(transport=transport) as client:
            return await check_for_release(db, client)

    newer = asyncio.run(check('v99.0.0'))
    assert newer['status'] == 'update_available'
    assert newer['update_available'] is True
    assert newer['url'].endswith('/tag/v99.0.0')
    assert cached_release_status(db) == newer

    current = asyncio.run(check(f'v{__version__}'))
    assert current['status'] == 'up_to_date'
    assert current['update_available'] is False

    older = asyncio.run(check('v0.1.0'))
    assert older['status'] == 'up_to_date'
    assert older['update_available'] is False


def test_release_check_distinguishes_no_release_from_failure(tmp_path: Path) -> None:
    db = Database(tmp_path / 'job_radar.sqlite3')

    async def check(response):
        transport = httpx.MockTransport(lambda request: response)
        async with httpx.AsyncClient(transport=transport) as client:
            return await check_for_release(db, client)

    missing = asyncio.run(check(httpx.Response(404)))
    assert missing['status'] == 'no_release'
    assert missing['checked_at']
    invalid = asyncio.run(check(httpx.Response(200, json={'tag_name': 'not-a-version'})))
    assert invalid['status'] == 'error'
    assert invalid['update_available'] is False
    assert cached_release_status(db) == invalid


def test_release_tag_must_match_package_version() -> None:
    root = Path(__file__).resolve().parents[1]
    validator = root / 'scripts' / 'check_release_version.py'
    valid = subprocess.run([sys.executable, str(validator), f'v{__version__}'], capture_output=True, text=True)
    invalid = subprocess.run([sys.executable, str(validator), 'v99.0.0'], capture_output=True, text=True)
    assert valid.returncode == 0, valid.stderr
    assert invalid.returncode != 0


def test_update_controls_save_mode_and_check_on_demand(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute('UPDATE sources SET enabled=0')
    calls = []

    async def fake_check(db):
        calls.append('checked')
        result = {
            'current': __version__, 'latest': '99.0.0', 'update_available': True,
            'url': 'https://github.com/longnt27/jobradar/releases/tag/v99.0.0',
            'status': 'update_available', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'message': 'Version 99.0.0 is available.',
        }
        db.set_setting('release_check_status', result)
        return result

    monkeypatch.setattr('job_radar.web.check_for_release', fake_check)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
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
                page = browser.new_page()
                page.goto(f'http://127.0.0.1:{port}/#settings')
                page.wait_for_function("!document.querySelector('#settings').hasAttribute('aria-busy')")
                page.get_by_role('tab', name='Updates', exact=True).click()
                assert page.locator('input[value=manual][name=update-check-mode]').is_checked()
                assert not calls
                page.locator('input[value=automatic][name=update-check-mode]').check()
                page.wait_for_function("document.querySelector('#settings-update-result').textContent.includes('99.0.0')")
                assert app.state.db.get_setting('release_check_preference') == 'automatic'
                assert len(calls) == 1
                page.locator('input[value=manual][name=update-check-mode]').check()
                page.get_by_role('button', name='Check now').click()
                page.wait_for_function("document.querySelector('#settings-check-updates').textContent === 'Check now'")
                assert app.state.db.get_setting('release_check_preference') == 'manual'
                assert len(calls) == 2
                assert page.get_by_role('link', name='View release 99.0.0').get_attribute('href').endswith('/tag/v99.0.0')
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
