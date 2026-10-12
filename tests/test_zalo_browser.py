import asyncio
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_radar.db import Database
from job_radar.social_browser import social_login_at


# Minimal copies of the publicly visible Zalo controls observed during live preflight.
HTML = """<meta charset="utf-8"><div id="app-page"><div id="main-tab"></div><div id="contact-search"><input id="contact-search-input"></div>
<div id="searchResultList"><div id="friend-item-123" class="conv-item">
<div class="conv-item-title__name">Recruiter</div><span class="friend_online_status">Số điện thoại: <span class="txt-highlight">0912345678</span></span></div></div>
<div id="chatViewContainer"><header id="header"><div class="header-title">Recruiter</div></header>
<div id="messageView"><div id="bb_msg_id_old" class="chat-message me" data-component="bubble-message"><div class="text-message__container"><span class="text">Em xin JD chi tiết ạ.</span></div><div class="bubble-message-time-status">Đã gửi</div></div></div>
<div id="chatInput"><div id="richInput" contenteditable="true" data-trailer="Recruiter"></div><div class="send-msg-btn" title="Gửi">Gửi</div></div></div></div>
<script>
window.clicks=0;
document.querySelector('.conv-item').onclick=()=>document.querySelector('.conv-item').classList.add('selected');
document.querySelector('.send-msg-btn').onclick=()=>{window.clicks++;if(window.noConfirm)return;
 const item=document.createElement('div');item.id='bb_msg_id_new';item.className='chat-message me';item.dataset.component='bubble-message';
 const container=document.createElement('div');container.className='text-message__container';const text=document.createElement('span');text.className='text';
 text.innerText=document.querySelector('#richInput').innerText;container.append(text);item.append(container);
 const status=document.createElement('div');status.className='bubble-message-time-status';status.innerText=window.directSent ? 'Đã gửi' : 'Đang gửi';item.append(status);
 document.querySelector('#messageView').append(item);document.querySelector('#richInput').innerText='';
 setTimeout(()=>{status.innerText=window.statusText || 'Đã gửi';}, 30);};
</script>"""


async def exercise(callback, *, change="", url="https://chat.zalo.me/"):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.fulfill(body=HTML, content_type="text/html"))
        await page.goto(url)
        if change:
            await page.evaluate(change)
        try:
            return await callback(page)
        finally:
            await browser.close()


def test_lookup_requires_unique_verified_recipient():
    from job_radar.zalo_browser import lookup_recipient_on_page
    result = asyncio.run(exercise(lambda page: lookup_recipient_on_page(page, "+84912345678")))
    assert result["status"] == "ready"
    assert result["recipient"]["identity_key"] == "friend-item-123"
    assert result["recipient"]["phone"] == "0912345678"
    ambiguous = "document.querySelector('#searchResultList').append(document.querySelector('.conv-item').cloneNode(true))"
    result = asyncio.run(exercise(lambda page: lookup_recipient_on_page(page, "0912345678"), change=ambiguous))
    assert result["status"] == "needs_attention"


@pytest.mark.parametrize("url", ["https://id.zalo.me/account", "https://chat.zalo.me.evil.example/"])
def test_login_requires_the_actual_chat_host(url):
    from job_radar.zalo_browser import zalo_authenticated
    assert not asyncio.run(exercise(zalo_authenticated, url=url))


def test_login_and_privacy_restrictions_stop_before_send():
    from job_radar.zalo_browser import send_inquiry_on_page
    async def verify(page):
        result = await send_inquiry_on_page(page, {"phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter"}, "Em xin JD.")
        assert await page.evaluate("window.clicks") == 0
        return result
    result = asyncio.run(exercise(verify, change="document.querySelector('#richInput').remove()"))
    assert result["status"] == "needs_attention"


def test_changed_identity_stops_before_send():
    from job_radar.zalo_browser import send_inquiry_on_page
    async def verify(page):
        result = await send_inquiry_on_page(page, {"phone": "0912345678", "identity_key": "different-account", "display_name": "Recruiter"}, "Em xin JD.")
        assert await page.evaluate("window.clicks") == 0
        return result
    assert asyncio.run(exercise(verify))["status"] == "needs_attention"


def test_send_confirms_only_a_new_exact_outgoing_message():
    from job_radar.zalo_browser import send_inquiry_on_page
    async def verify(page):
        result = await send_inquiry_on_page(page, {"phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter"}, "Em xin JD chi tiết ạ.")
        assert await page.evaluate("window.clicks") == 1
        assert await page.locator('#bb_msg_id_new .text').inner_text() == "Em xin JD chi tiết ạ."
        return result
    result = asyncio.run(exercise(verify))
    assert result["status"] == "sent_confirmed"
    assert result["receipt"]["message_id"] == "bb_msg_id_new"


def test_timeout_after_send_is_uncertain(monkeypatch):
    import job_radar.zalo_browser as adapter
    monkeypatch.setattr(adapter, "CONFIRMATION_TIMEOUT_MS", 100)
    async def verify(page):
        result = await adapter.send_inquiry_on_page(page, {"phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter"}, "Em xin JD chi tiết ạ.")
        assert await page.evaluate("window.clicks") == 1
        return result
    result = asyncio.run(exercise(verify, change="window.noConfirm=true"))
    assert result["status"] == "send_uncertain"  # Old identical bubble is insufficient.


def test_delayed_history_cannot_confirm_the_current_send(monkeypatch):
    import job_radar.zalo_browser as adapter
    monkeypatch.setattr(adapter, "CONFIRMATION_TIMEOUT_MS", 100)
    change = """const old=document.querySelector('#messageView .me');old.remove();
      old.id='bb_msg_id_'+(Date.now()-60000);window.noConfirm=true;
      document.querySelector('.send-msg-btn').addEventListener('click',()=>document.querySelector('#messageView').append(old));"""
    result = asyncio.run(exercise(lambda page: adapter.send_inquiry_on_page(page, {
        "phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter",
    }, "Em xin JD chi tiết ạ."), change=change))
    assert result["status"] == "send_uncertain"


def test_sent_bubble_without_a_sending_transition_is_uncertain(monkeypatch):
    import job_radar.zalo_browser as adapter
    monkeypatch.setattr(adapter, "CONFIRMATION_TIMEOUT_MS", 100)
    result = asyncio.run(exercise(lambda page: adapter.send_inquiry_on_page(page, {
        "phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter",
    }, "Em xin JD."), change="window.directSent=true"))
    assert result["status"] == "send_uncertain"


def test_existing_unsent_draft_is_preserved():
    from job_radar.zalo_browser import send_inquiry_on_page
    async def verify(page):
        result = await send_inquiry_on_page(page, {"phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter"}, "Em xin JD.")
        assert await page.locator('#richInput').inner_text() == "My existing draft"
        assert await page.evaluate("window.clicks") == 0
        return result
    assert asyncio.run(exercise(verify, change="document.querySelector('#richInput').innerText='My existing draft'"))["status"] == "needs_attention"


def test_legacy_social_login_does_not_connect_zalo(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.set_setting("browser_login_completed_at", "2026-10-01")
    assert social_login_at(db, "zalo") is None


def test_failed_status_does_not_count_as_sent(monkeypatch):
    import job_radar.zalo_browser as adapter
    monkeypatch.setattr(adapter, "CONFIRMATION_TIMEOUT_MS", 100)
    result = asyncio.run(exercise(lambda page: adapter.send_inquiry_on_page(page, {
        "phone": "0912345678", "identity_key": "friend-item-123", "display_name": "Recruiter",
    }, "Em xin JD."), change="window.statusText='Not sent'"))
    assert result["status"] == "send_uncertain"


def test_browser_cleanup_failure_preserves_confirmed_receipt(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import job_radar.zalo_browser as adapter
    from job_radar.settings import Settings
    page = SimpleNamespace(goto=AsyncMock(), locator=lambda _: SimpleNamespace(wait_for=AsyncMock()))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock(side_effect=RuntimeError("Cleanup failed")))
    class Runtime:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=AsyncMock(return_value=context)))
        async def __aexit__(self, *_):
            pass
    monkeypatch.setattr(adapter, "async_playwright", Runtime)
    monkeypatch.setattr(adapter, "chrome_context_options", lambda **_: {})
    async def confirmed(_page):
        return {"status": "sent_confirmed", "receipt": {"message_id": "confirmed-123"}}
    result = asyncio.run(adapter._session_operation(Settings(tmp_path), confirmed))
    assert result["status"] == "sent_confirmed"
    assert result["receipt"]["message_id"] == "confirmed-123"
