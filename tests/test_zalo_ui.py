import socket
import time
from threading import Thread

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from test_zalo_api import app_with_post, mock_transport
from test_zalo_contacts import facebook_job


@pytest.fixture
def ui(tmp_path, monkeypatch):
    app, job, observation = app_with_post(tmp_path)
    mock_transport(monkeypatch)
    app.state.db.execute("UPDATE sources SET enabled=0")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.03)
    assert server.started
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(); page.set_default_timeout(3000)
        try:
            page.goto(f"http://127.0.0.1:{port}/#jobs?job={job}")
            yield page, app, job, observation
        finally:
            browser.close()
            server.should_exit = True; thread.join(timeout=5)


def prepare_ui(page):
    page.locator('[data-zalo-candidate]').first.click()
    page.get_by_label('Message to send', exact=True).wait_for()


def test_success_shows_contacted_awaiting_reply_and_keeps_prepare_application(ui):
    page, app, job, _ = ui
    prepare_ui(page)
    assert 'STUPID EXTRACTED TITLE' in page.get_by_label('Message to send', exact=True).input_value()
    page.get_by_label('Position in your message', exact=True).fill('AI Engineer')
    page.get_by_role('button', name='Regenerate from template', exact=True).click()
    page.wait_for_function("document.querySelector('[data-zalo-message]').value.includes('vị trí AI Engineer')")
    page.get_by_role('button', name='Look up Zalo recipient', exact=True).click()
    page.get_by_text('Recruiter · 0912345678', exact=True).wait_for()
    send = page.get_by_role('button', name='Approve and send Zalo inquiry', exact=True)
    assert send.is_enabled()
    page.get_by_label('Message to send', exact=True).fill('Em hỏi riêng về JD ạ.')
    assert send.is_disabled()
    page.get_by_role('button', name='Save inquiry', exact=True).click()
    send.wait_for(state='visible')
    page.wait_for_function("!document.querySelector('[data-zalo-action=approve]').disabled")
    send.click()
    page.get_by_text('Contacted, awaiting reply', exact=True).wait_for()
    assert page.get_by_role('button', name='Prepare application', exact=True).is_visible()
    attempt = app.state.db.one('SELECT package_data FROM zalo_inquiry_attempts')
    assert 'Em hỏi riêng về JD ạ.' in attempt['package_data']


def test_job_refresh_preserves_unsaved_inquiry_edits(ui):
    page, _, job, _ = ui
    prepare_ui(page)
    page.get_by_label('Message to send', exact=True).fill('My corrected inquiry')
    page.evaluate('jobId => showJob(jobId, true)', job)
    page.get_by_label('Message to send', exact=True).wait_for()
    assert page.get_by_label('Message to send', exact=True).input_value() == 'My corrected inquiry'
    assert page.get_by_role('button', name='Approve and send Zalo inquiry', exact=True).is_disabled()


@pytest.mark.parametrize('selection', ['candidate', 'manual'])
def test_contact_selection_preserves_unsaved_message_edits(ui, selection):
    page, app, _, observation = ui
    prepare_ui(page)
    page.get_by_label('Message to send', exact=True).fill('My corrected inquiry')
    if selection == 'candidate':
        page.locator('[data-zalo-candidate]').first.click()
    else:
        page.locator('.zalo-manual summary').click()
        page.get_by_label('Facebook post', exact=True).select_option(observation)
        page.get_by_label('Phone number from the post', exact=True).fill('0976543210')
        page.get_by_role('button', name='Prepare inquiry', exact=True).click()
    page.locator('#job-zalo-contact [role=alert]').filter(has_text='Save your message edits before choosing another inquiry.').wait_for()
    assert page.get_by_label('Message to send', exact=True).input_value() == 'My corrected inquiry'
    assert page.get_by_label('Zalo phone number', exact=True).input_value() == '0912345678'
    assert app.state.db.one('SELECT count(*) AS count FROM zalo_inquiry_drafts')['count'] == 1


@pytest.mark.parametrize('site,label', [('linkedin', 'LinkedIn'), ('facebook', 'Facebook'), ('zalo', 'Zalo')])
def test_settings_keeps_loading_while_social_sign_in_is_open(ui, site, label):
    page, _, _, _ = ui
    def open_sign_in(route):
        response = route.fetch()
        data = response.json()
        data['browser'].update(state='open', active_site=site)
        route.fulfill(response=response, json=data)
    page.route('**/api/setup', open_sign_in)
    page.goto(page.url.split('#')[0] + '#settings')
    page.wait_for_function("label => document.querySelector('#setup-browser-detail').textContent.includes('Waiting for '+label+' sign-in')", arg=label)
    page.locator('[data-zalo-template]').wait_for(state='attached')
    page.wait_for_function("document.querySelector('[data-zalo-template]').value.includes('Em chào anh/chị')")


def test_phone_edit_disables_approval_until_new_lookup(ui):
    page, _, _, _ = ui
    prepare_ui(page)
    page.get_by_role('button', name='Look up Zalo recipient', exact=True).click()
    page.get_by_text('Recruiter · 0912345678', exact=True).wait_for()
    page.get_by_label('Zalo phone number', exact=True).fill('0976543210')
    page.get_by_role('button', name='Save inquiry', exact=True).click()
    page.get_by_text('Recipient not verified yet', exact=True).wait_for()
    assert page.get_by_role('button', name='Approve and send Zalo inquiry', exact=True).is_disabled()


def test_multiple_posts_require_explicit_source_selection(ui):
    page, app, job, _ = ui
    other, observation = facebook_job(app.state.db, group='Other group', title='Other role', text='Zalo 0976543210')
    app.state.db.execute('UPDATE sources SET enabled=0')
    app.state.db.execute('UPDATE vacancy_observations SET vacancy_id=? WHERE observation_id=?', (job, observation))
    page.reload()
    page.locator('[data-zalo-candidate]').nth(1).wait_for()
    assert 'Other group' in page.locator('#job-zalo-contact').inner_text()
    assert page.get_by_label('Facebook post', exact=True).input_value() == ''
    page.locator('[data-zalo-candidate]').filter(has_text='0976543210').click()
    assert page.get_by_label('Position in your message', exact=True).input_value() == 'Other role'


def test_existing_custom_message_survives_template_change(ui):
    page, app, job, _ = ui
    prepare_ui(page)
    page.get_by_label('Message to send', exact=True).fill('Em hỏi riêng ạ.')
    page.get_by_role('button', name='Save inquiry', exact=True).click()
    page.wait_for_function("document.querySelector('[data-zalo-action=save]').disabled")
    app.state.db.set_setting('zalo_inquiry_template', 'A new template for {position}')
    page.reload()
    page.get_by_label('Message to send', exact=True).wait_for()
    assert page.get_by_label('Message to send', exact=True).input_value() == 'Em hỏi riêng ạ.'


def test_blocked_and_uncertain_results_have_recovery_actions(ui, monkeypatch):
    page, app, job, _ = ui
    async def uncertain(*_):
        return {'status':'send_uncertain', 'error':'No sent confirmation'}
    monkeypatch.setattr('job_radar.zalo_api.send_zalo_inquiry', uncertain)
    prepare_ui(page)
    assert page.get_by_role('button', name='Connect Zalo', exact=True).is_visible()
    page.get_by_role('button', name='Look up Zalo recipient', exact=True).click()
    page.get_by_text('Recruiter · 0912345678', exact=True).wait_for()
    page.get_by_role('button', name='Approve and send Zalo inquiry', exact=True).click()
    page.get_by_role('button', name='I checked: message was not sent', exact=True).wait_for()
    assert page.get_by_role('button', name='Approve and send Zalo inquiry', exact=True).is_disabled()
    page.get_by_role('button', name='I checked: message was not sent', exact=True).click()
    page.get_by_text('Recipient not verified yet', exact=True).wait_for()
