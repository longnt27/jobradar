"""Use observed Zalo Web controls; never call private messaging endpoints."""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urlsplit

from playwright.async_api import Page, async_playwright

from .db import now
from .settings import Settings
from .social_browser import chrome_context_options, clean_stale_chrome_lock
from .zalo_contacts import normalize_zalo_phone

ZALO_URL = "https://chat.zalo.me/"
LOOKUP_TIMEOUT_MS = 12_000
CONFIRMATION_TIMEOUT_MS = 15_000
_SENT = re.compile(r"^\s*(?:đã gửi|đã nhận|đã xem|sent|delivered|read)\s*$", re.I | re.M)


def _text(value: str) -> str:
    return value.replace("\xa0", " ").replace("\r\n", "\n").strip()


def _attention(detail: str) -> dict:
    return {"status": "needs_attention", "detail": detail, "error": detail}


async def zalo_authenticated(page: Page) -> bool:
    parts = urlsplit(page.url)
    if parts.scheme != "https" or parts.hostname != "chat.zalo.me":
        return False
    return await page.locator("#main-tab").count() == 1 and await page.locator("#contact-search-input").is_visible()


async def _matching_results(page: Page, phone: str) -> list[dict]:
    rows = await page.locator('#searchResultList [id^="friend-item-"]').evaluate_all("""nodes => nodes.filter(e=>e.getClientRects().length).map(e=>({
      identity_key:e.id, display_name:e.querySelector('.conv-item-title__name')?.innerText || '',
      phone:e.querySelector('.friend_online_status .txt-highlight')?.innerText || ''
    }))""")
    result = []
    for row in rows:
        try:
            actual = normalize_zalo_phone(row["phone"])
        except ValueError:
            continue
        if actual == phone and re.fullmatch(r"friend-item-\d+", row["identity_key"]):
            result.append({**row, "phone": actual, "display_name": _text(row["display_name"])})
    return result


async def _chat_matches(page: Page, recipient: dict) -> bool:
    key = recipient["identity_key"]
    if not re.fullmatch(r"friend-item-\d+", key):
        return False
    selected = page.locator(f"#{key}.selected")
    header = page.locator("#header .header-title")
    editor = page.locator('#richInput[contenteditable="true"]')
    if await selected.count() != 1 or not await selected.is_visible() or await header.count() != 1 or not await editor.is_visible():
        return False
    name = _text(recipient["display_name"])
    return _text(await header.inner_text()) == name and _text(await editor.get_attribute("data-trailer") or "") == name


async def lookup_recipient_on_page(page: Page, phone: str) -> dict:
    phone = normalize_zalo_phone(phone)
    if not await zalo_authenticated(page):
        return _attention("Sign in to Zalo in Job Radar's saved Chrome profile")
    search = page.locator("#contact-search-input")
    await search.fill(phone)
    deadline = asyncio.get_running_loop().time() + LOOKUP_TIMEOUT_MS / 1000
    rows = []
    while asyncio.get_running_loop().time() < deadline:
        rows = await _matching_results(page, phone)
        if rows:
            break
        await asyncio.sleep(.15)
    if len(rows) != 1 or not rows[0]["display_name"]:
        return _attention("Zalo did not find one unambiguous profile for this phone number")
    recipient = {**rows[0], "evidence": "A unique Zalo phone search result displayed this number."}
    await page.locator(f"#{recipient['identity_key']}").click(timeout=5000)
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        if await _chat_matches(page, recipient):
            return {"status": "ready", "recipient": recipient}
        await asyncio.sleep(.15)
    return _attention("The selected Zalo profile has no verifiable, available chat. Check its messaging restrictions.")


async def send_inquiry_on_page(page: Page, recipient: dict, message: str) -> dict:
    may_have_sent = False
    try:
        if not message or not message.strip():
            return _attention("Review a nonempty Zalo message before sending")
        lookup = await lookup_recipient_on_page(page, recipient["phone"])
        if lookup["status"] != "ready":
            return lookup
        current = lookup["recipient"]
        if any(current[key] != recipient.get(key) for key in ("identity_key", "display_name", "phone")):
            return _attention("The Zalo recipient changed since review. Look up and review the contact again.")
        editor = page.locator("#richInput")
        if _text(await editor.inner_text()):
            return _attention("This Zalo chat already has an unsent draft. Save or clear it in Zalo before retrying.")
        bubbles = page.locator('#messageView [data-component="bubble-message"].me')
        baseline = set(await bubbles.evaluate_all("nodes=>nodes.map(e=>e.id)"))
        await editor.fill(message)
        send = page.locator("#chatInput .send-msg-btn")
        await send.wait_for(state="visible", timeout=5000)
        if (_text(await editor.inner_text()) != _text(message) or not await _chat_matches(page, recipient)
                or await send.get_attribute("aria-disabled") == "true"
                or await send.get_attribute("data-disabled") in ("true", "disabled")):
            return _attention("The Zalo composer or recipient does not match the reviewed message")
        may_have_sent = True
        await send.click(timeout=5000)
        deadline = asyncio.get_running_loop().time() + CONFIRMATION_TIMEOUT_MS / 1000
        while asyncio.get_running_loop().time() < deadline:
            if not await _chat_matches(page, recipient):
                break
            for bubble in await bubbles.all():
                identifier = await bubble.get_attribute("id") or ""
                if not identifier or identifier in baseline:
                    continue
                body = bubble.locator(".text-message__container .text")
                if await body.count() != 1 or _text(await body.inner_text()) != _text(message):
                    continue
                status = bubble.locator(".bubble-message-time-status")
                status_text = await status.inner_text() if await status.count() else ""
                if _SENT.search(status_text):
                    return {"status": "sent_confirmed", "receipt": {
                        "recipient": recipient, "message_id": identifier, "message_hash": hashlib.sha256(message.encode()).hexdigest(),
                        "sent_at": now(), "visible_status": _text(status_text), "source": "zalo_web",
                    }}
            await asyncio.sleep(.15)
        return {"status": "send_uncertain", "error": "Zalo was asked to send, but a new matching message with a sent status was not confirmed. Check the chat before retrying."}
    except Exception as error:
        return {"status": "send_uncertain" if may_have_sent else "needs_attention", "error": str(error)[:600]}


async def _session_operation(settings: Settings, operation) -> dict:
    result = None
    try:
        clean_stale_chrome_lock(settings.browser_profile)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(settings.browser_profile), headless=False, accept_downloads=False, **chrome_context_options(required=True),
            )
            try:
                page = await context.new_page()
                await page.goto(ZALO_URL, wait_until="domcontentloaded", timeout=45000)
                try:
                    await page.locator("#contact-search-input").wait_for(state="visible", timeout=20000)
                except Exception:
                    return _attention("Zalo sign-in is required. Connect Zalo in Settings, then look up the contact again.")
                result = await operation(page)
                return result
            finally:
                await context.close()
    except Exception as error:
        if result is not None:
            return result
        return _attention(f"Job Radar could not open its Zalo session: {str(error)[:400]}")


async def lookup_zalo_recipient(settings: Settings, phone: str) -> dict:
    return await _session_operation(settings, lambda page: lookup_recipient_on_page(page, phone))


async def send_zalo_inquiry(settings: Settings, recipient: dict, message: str) -> dict:
    return await _session_operation(settings, lambda page: send_inquiry_on_page(page, recipient, message))
