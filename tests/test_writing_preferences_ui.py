import socket
import time
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from job_radar.settings import Settings
from job_radar.web import create_app


@pytest.mark.parametrize('width', [1280, 390])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_writing_editor_saves_preserves_edits_restores_and_fits_viewport(tmp_path, monkeypatch, width, theme):
    app = create_app(Settings(tmp_path))
    app.state.db.execute('UPDATE sources SET enabled=0')
    monkeypatch.setattr('job_radar.web.list_local_models', lambda: [])
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
                page = browser.new_page(viewport={'width': width, 'height': 900})
                page.context.add_init_script(f"localStorage.setItem('job-radar-theme', '{theme}')")
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}/#settings')
                page.wait_for_function("!document.querySelector('#settings').hasAttribute('aria-busy')")
                page.locator('#writing-preferences-panel > summary').click()
                choice = page.locator('#writing-template-choice')
                choice.select_option('section_summary')
                text = page.locator('#writing-text')
                original = text.input_value()
                assert 'Applied AI Engineer' in original
                custom = 'Write a concise, warm summary for a product designer.'
                text.fill(custom)
                choice.select_option('email_subject_en')
                choice.select_option('section_summary')
                assert text.input_value() == custom
                assert 'Unsaved edits' in page.locator('#writing-status').inner_text()
                page.get_by_role('button', name='Save customization', exact=True).click()
                page.wait_for_function("document.querySelector('#writing-status').textContent === 'Saved customization'")
                assert app.state.db.get_setting('writing.section_summary') == custom
                page.reload()
                page.wait_for_function("!document.querySelector('#settings').hasAttribute('aria-busy')")
                page.locator('#writing-preferences-panel > summary').click()
                choice.select_option('section_summary')
                assert text.input_value() == custom
                page.get_by_role('button', name='Restore default', exact=True).click()
                page.wait_for_function("document.querySelector('#writing-status').textContent === 'Using the original default'")
                assert text.input_value() == original
                assert app.state.db.get_setting('writing.section_summary') is None
                choice.select_option('project_count')
                number = page.locator('#writing-number')
                assert number.is_visible() and not text.is_visible()
                number.fill('2')
                page.get_by_role('button', name='Save customization', exact=True).click()
                page.wait_for_function("document.querySelector('#writing-status').textContent === 'Saved customization'")
                assert app.state.db.get_setting('writing.project_count') == 2
                choice.select_option('email_body_en')
                text.fill('Hello {company},\n\n{fit}\n\nThank you,\n{name}')
                page.locator('#writing-preferences-panel').scroll_into_view_if_needed()
                assert page.evaluate('Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) <= innerWidth + 1')
                page.locator('#writing-preferences-editor summary').click()
                assert page.locator('#writing-default').is_visible()
                artifact = Path(__file__).parents[1] / 'artifacts' / f'writing-editor-{theme}-{width}.png'
                artifact.parent.mkdir(exist_ok=True)
                page.screenshot(path=str(artifact), full_page=True)
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
