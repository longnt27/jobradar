from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

from playwright.async_api import async_playwright

from .settings import Settings
from .social_browser import chrome_context_options


FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}


def group_from_url(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    segments = [unquote(part) for part in parts.path.split("/") if part]
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in FACEBOOK_HOSTS:
        raise ValueError("Paste a Facebook group link")
    if len(segments) < 2 or segments[0].lower() != "groups":
        raise ValueError("Paste a Facebook group link such as facebook.com/groups/group-name")
    identifier = segments[1]
    if not re.fullmatch(r"[\w.\-]{1,100}", identifier, re.UNICODE):
        raise ValueError("This Facebook group link has an invalid group ID")
    canonical_url = f"https://www.facebook.com/groups/{quote(identifier, safe='._-')}/"
    if identifier.isdecimal():
        return canonical_url, f"Facebook group {identifier}"
    readable = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", identifier)
    readable = re.sub(r"[._-]+", " ", readable).strip()
    if readable.islower():
        readable = readable.title()
    return canonical_url, readable[:100]


def clean_group_title(value: str) -> str | None:
    title = re.sub(r"\s*[|·–-]\s*Facebook.*$", "", value, flags=re.I).strip()
    if title.casefold() in {"facebook", "groups", "log in", "log in to facebook", "sign up", "facebook groups"}:
        return None
    return title[:100] if 2 <= len(title) <= 160 else None


async def lookup_facebook_group_name(settings: Settings, url: str) -> str | None:
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.browser_profile), headless=True, **chrome_context_options(required=True),
        )
        try:
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            values = await page.evaluate("""() => [
                document.querySelector('meta[property="og:title"]')?.content || '',
                document.querySelector('h1')?.innerText || '',
                document.title || ''
            ]""")
            return next((name for value in values if (name := clean_group_title(value))), None)
        finally:
            await context.close()
