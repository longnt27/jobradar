import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


def test_settings_modal_exposes_controls_in_two_tab_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr('job_radar.web.shutil.which', lambda command: '/usr/bin/' + command)
    monkeypatch.setattr('job_radar.web.list_local_models', lambda: [{'name': 'test:small', 'size': 123_000_000}])
    monkeypatch.setattr('job_radar.web.validate_local_model', lambda model: model)
    monkeypatch.setattr('job_radar.matching.validate_local_model', lambda model: model)
    app = create_app(Settings(tmp_path))
    app.state.db.execute('UPDATE sources SET enabled=0')
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
                page = browser.new_page(viewport={'width': 1450, 'height': 900}, color_scheme='dark')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/#home')
                page.get_by_role('button', name='Profile menu').click()
                page.locator('#profile-menu [data-tab="settings"]').click()
                page.wait_for_function("!document.querySelector('#settings').hasAttribute('aria-busy')")
                assert page.locator('#settings').evaluate('(node) => node.open')
                assert page.locator('#theme-preference').is_visible()
                logo_height = page.locator('.brand-logo').bounding_box()['height']
                tab_height = page.locator('.nav-primary button').first.bounding_box()['height']
                assert logo_height == tab_height
                page.get_by_role('tab', name='AI Models', exact=True).click()
                assert page.get_by_role('tab', name='Resume extraction').is_visible()
                resume = page.locator('#resume-model-form')
                resume.locator('select').select_option('codex')
                resume.locator('[name="model"]').fill('qwen3:4b')
                resume.get_by_role('button', name='Save model').click()
                page.get_by_text('Resume extraction model saved.').wait_for()
                assert app.state.db.get_setting('resume_extraction_provider') == 'codex'
                assert app.state.db.get_setting('resume_extraction_model') == 'qwen3:4b'
                page.get_by_role('tab', name='Job parsing').click()
                parsing = page.locator('#parsing-model-form')
                parsing.locator('select').select_option('test:small')
                parsing.get_by_role('button', name='Use model').click()
                page.get_by_text('Job parsing model saved.').wait_for()
                assert app.state.db.get_setting('parsing_model') == 'test:small'
                parsing.locator('select').select_option('')
                parsing.get_by_role('button', name='Use matching model').click()
                page.wait_for_function("document.querySelector('#parsing-model-form').dataset.saved === ''")
                assert app.state.db.get_setting('parsing_model') == ''
                page.get_by_role('tab', name='Application writing').click()
                assert page.locator('#provider-form select').is_visible()
                page.locator('#provider-form [name="provider"]').select_option('codex')
                page.locator('#provider-form [name="model"]').fill('gpt-6.1-sol')
                page.locator('#provider-form button[type="submit"]').click()
                page.get_by_text('Application writing model saved.').wait_for()
                assert app.state.db.get_setting('application_writing_model') == 'gpt-6.1-sol'
                assert page.locator('#writing-template-choice').is_visible()
                page.get_by_role('tab', name='Connections', exact=True).click()
                assert page.locator('#setup-linkedin-start').is_visible()
                page.get_by_role('tab', name='Facebook').click()
                assert page.locator('#setup-facebook-start').is_visible()
                page.get_by_role('tab', name='General', exact=True).click()
                page.get_by_role('tab', name='Search preferences').click()
                assert page.locator('#search-intent-form').is_visible()
                page.get_by_role('tab', name='Draft automation').click()
                assert page.locator('#auto-apply-form').is_visible()
                page.get_by_role('button', name='Close Settings').click()
                assert not page.locator('#settings').evaluate('(node) => node.open')
                assert page.url.endswith('#home')
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
