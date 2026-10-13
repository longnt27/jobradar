import asyncio

import pytest

from job_radar.collectors import collect_onemount


@pytest.mark.parametrize("phase", ["response", "detail", "status"])
def test_onemount_failed_api_or_detail_is_not_an_empty_feed(monkeypatch, phase):
    class Reply:
        url = "https://careers.onemount.com/ScreenDataSetGetJobRequests"
        status = 503 if phase == "status" else 200

        async def json(self):
            if phase == "response":
                raise RuntimeError("Invalid API JSON")
            if phase == "status":
                return {"error": "Service unavailable"}
            return {"data": {"List": {"List": [{"JobRequest": {
                "Id": 42, "JobName": "AI Engineer", "Location": "Hanoi",
            }}]}}}

    class Listing:
        def on(self, _event, callback):
            self.callback = callback

        async def goto(self, *_args, **_kwargs):
            await self.callback(Reply())

    class Detail:
        async def goto(self, *_args, **_kwargs):
            raise RuntimeError("TLS certificate verification failed")

    class Browser:
        closed = False

        def __init__(self):
            self.pages = iter([Listing(), Detail()])

        async def new_page(self):
            return next(self.pages)

        async def close(self):
            self.closed = True

    browser = Browser()

    class Playwright:
        chromium = None

        async def __aenter__(self):
            self.chromium = self
            return self

        async def __aexit__(self, *_args):
            pass

        async def launch(self, **_kwargs):
            return browser

    monkeypatch.setattr("job_radar.collectors.async_playwright", Playwright)
    with pytest.raises(RuntimeError):
        asyncio.run(collect_onemount({"url": "https://careers.onemount.com/job-list", "config": {}}))
    assert browser.closed
