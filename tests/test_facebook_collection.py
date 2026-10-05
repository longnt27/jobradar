import asyncio

import pytest

from job_radar.collectors import RECRUITING, _facebook_detail_matches, _facebook_post_url, _facebook_posted_at, _facebook_timestamp_date


def test_facebook_post_links_from_group_card() -> None:
    group = "https://www.facebook.com/groups/1407434203194440/"
    assert _facebook_post_url(group, [
        "https://www.facebook.com/groups/1407434203194440/posts/123/?comment_id=456",
    ]) == "https://www.facebook.com/groups/1407434203194440/posts/123/"
    assert _facebook_post_url(group, [
        "https://www.facebook.com/photo/?fbid=9&set=gm.456&idorvanity=1407434203194440",
    ]) == "https://www.facebook.com/groups/1407434203194440/posts/456/"
    assert _facebook_post_url(group, [
        "https://www.facebook.com/groups/other/posts/789/",
        "https://www.facebook.com/photo/?set=gm.789&idorvanity=other",
    ]) is None


def test_facebook_timestamp_tooltip_is_saved_as_posting_date() -> None:
    assert _facebook_posted_at("Tuesday 29 September 2026 at 15:50") == "2026-09-29T08:50:00+00:00"
    assert _facebook_posted_at("5 days ago") is None
    assert RECRUITING.search("VMO cần gấp AI Engineer có KN về GenAI")


@pytest.mark.parametrize("value", [
    "Thứ Ba, 29 tháng 9, 2026 lúc 15:50",
    "Thứ Ba, 29 tháng 9 năm 2026 lúc 15:50",
    "29 tháng 9, 2026 lúc 15:50",
    "Thứ Ba,\u00a029 tháng 9,\u202f2026 lúc 15:50",
    "Tuesday, September 29, 2026 at 3:50 PM",
    "Tuesday, September 29, 2026 at 15:50",
    "29 September 2026 at 15:50",
    "2026-09-29T15:50:00+07:00",
    "2026-09-29T08:50:00Z",
    "1790671800",
    "1790671800000",
    "29 SEPTEMBER 2026 AT 15:50",
])
def test_facebook_exact_timestamp_formats(value) -> None:
    assert _facebook_posted_at(value) == "2026-09-29T08:50:00+00:00"


@pytest.mark.parametrize("value", [
    "", "5 ngày trước", "5 days ago", "29/09/2026", "09/10/2026 15:50",
    "Thứ Ba, 31 tháng 9, 2026 lúc 15:50", "29 tháng 13, 2026 lúc 15:50",
    "29 tháng 9, 2026 lúc 25:50", "2026-09-29", "1790671800000000000",
    "Tuesday, September 29, 2026 at 0:50 PM",
    "Tuesday, September 29, 2026 at 13:50 PM",
    "2026-02-30T15:50:00Z",
])
def test_facebook_timestamp_rejects_unknown_or_incomplete_dates(value) -> None:
    assert _facebook_posted_at(value) is None


def test_facebook_timestamp_respects_browser_timezone_and_explicit_offset() -> None:
    assert _facebook_posted_at("29 tháng 9, 2026 lúc 15:50", "America/New_York") == "2026-09-29T19:50:00+00:00"
    assert _facebook_posted_at("2026-09-29T15:50:00", "America/New_York") == "2026-09-29T19:50:00+00:00"
    assert _facebook_posted_at("2026-09-29T15:50:00+07:00", "America/New_York") == "2026-09-29T08:50:00+00:00"
    assert _facebook_posted_at("1790671800", "America/New_York") == "2026-09-29T08:50:00+00:00"
    assert _facebook_posted_at("Tuesday, September 29, 2026 at 12:50 AM") == "2026-09-28T17:50:00+00:00"
    assert _facebook_posted_at("Tuesday, September 29, 2026 at 12:50 PM") == "2026-09-29T05:50:00+00:00"


def test_facebook_timestamp_metadata_and_tooltip_fallback_in_dom() -> None:
    from playwright.async_api import async_playwright

    async def check():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(locale="vi-VN", timezone_id="America/New_York")
                # A date outside the timestamp must not be mistaken for this post's date.
                await page.set_content('''
                    <time datetime="2020-01-01T00:00:00Z"></time>
                    <a id="timestamp" data-utime="1790671800">5 ngày</a>
                    <div role="tooltip">unrecognized locale</div>
                ''')
                timestamp = await page.query_selector('#timestamp')
                timezone_id = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
                assert await _facebook_timestamp_date(timestamp, page, timezone_id) == "2026-09-29T08:50:00+00:00"
                await timestamp.evaluate('''link => {
                    link.removeAttribute('data-utime');
                    link.innerHTML = '<time datetime="2026-09-29T15:50:00+07:00"></time>';
                }''')
                assert await _facebook_timestamp_date(timestamp, page, timezone_id) == "2026-09-29T08:50:00+00:00"
                await timestamp.evaluate('''link => {
                    link.innerHTML = '<abbr data-utime="1790671800000"></abbr>';
                }''')
                assert await _facebook_timestamp_date(timestamp, page, timezone_id) == "2026-09-29T08:50:00+00:00"
                await timestamp.evaluate('''link => {
                    link.innerHTML = '5 ngày';
                    link.setAttribute('data-utime', 'invalid');
                    document.querySelector('[role="tooltip"]').innerText = 'Thứ Ba, 29 tháng 9, 2026 lúc 15:50';
                }''')
                assert await _facebook_timestamp_date(timestamp, page, timezone_id) == "2026-09-29T19:50:00+00:00"
                await page.locator('[role="tooltip"]').evaluate("node => node.innerText = '5 ngày trước'")
                assert await _facebook_timestamp_date(timestamp, page, timezone_id) is None
            finally:
                await browser.close()

    asyncio.run(check())


def test_facebook_post_confirmation_uses_opened_dialog_only() -> None:
    class OpenedPost:
        first = None

        def __init__(self, texts):
            self.texts = texts
            self.first = self

        async def wait_for(self, **_kwargs):
            return None

        async def all_inner_texts(self):
            return self.texts

    class Page:
        def __init__(self, texts):
            self.texts = texts

        def locator(self, selector):
            assert selector == '[role="dialog"] [data-ad-rendering-role="story_message"]'
            return OpenedPost(self.texts)

    saved = "Mình cần tuyển AI Engineer, 3 năm kinh nghiệm"
    assert not asyncio.run(_facebook_detail_matches(Page(["Different HCM software role"]), saved))
    assert asyncio.run(_facebook_detail_matches(Page([saved]), saved))
