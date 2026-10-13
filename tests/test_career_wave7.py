"""Agent 7 official Agoda recruitment board regression coverage."""
import asyncio
import httpx
import pytest

from job_radar.career_wave7 import collect_agoda
from job_radar.feed_catalog import CAREER_FEEDS


def source():
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == "Agoda")
    return {"url": feed.url, "name": "Agoda", "config": {"adapter": feed.adapter}}


def html_board():
    return """<main><h1>Job vacancies</h1>
    <div class="jobs"><article>
      <a href="/job/123-ai-engineer/">AI Engineer</a>
      <span><img alt="Location"> Ho Chi Minh City, Vietnam</span>
    </article><article>
      <a href="/job/124-data-engineer/">Data Engineer</a>
      <span><img alt="Location"> Bangkok, Thailand</span>
    </article></div></main>"""


def html_detail(location="Ho Chi Minh City, Vietnam", content=None):
    description = content or ("Responsibilities: Build production AI systems. "
                              "Requirements: Python, machine learning and deployment experience. " * 6)
    return f"""<main><h1>AI Engineer</h1><div>
    <span><img alt="Location"> {location}</span></div><article>{description}</article></main>"""


def install_client(monkeypatch, listing, details=None, status=200):
    requested = []
    details = details or {}
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def get(self, url):
            requested.append(url)
            if url.endswith("/vacancies/"):
                text = listing
                code = status
            else:
                text = details.get(url, html_detail())
                code = 200
            return httpx.Response(code, text=text, request=httpx.Request("GET", url))
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    return requested


def test_agoda_scopes_to_verified_posting_location(monkeypatch):
    requested = install_client(monkeypatch, html_board())
    jobs = asyncio.run(collect_agoda(source()))
    assert len(jobs) == 1
    assert jobs[0].company == "Agoda"
    assert jobs[0].external_id == "123"
    assert jobs[0].url == jobs[0].apply_url == "https://careersatagoda.com/job/123-ai-engineer/"
    assert "Vietnam" in jobs[0].location
    assert "Requirements:" in jobs[0].description
    assert all("124" not in req for req in requested)


@pytest.mark.parametrize("listing", [
    "<main>Job vacancies</main>",
    "<main><h1>10 jobs available</h1><a href='/job/123-ai-engineer/'>AI Engineer</a>"
    "<span><img alt='Location'> Vietnam</span></main>",
])
def test_agoda_parsing_errors_are_not_silent_zero(monkeypatch, listing):
    install_client(monkeypatch, listing)
    with pytest.raises(RuntimeError):
        asyncio.run(collect_agoda(source()))


def test_agoda_true_empty_feed(monkeypatch):
    install_client(monkeypatch, "<main><h2>No jobs available</h2></main>")
    assert asyncio.run(collect_agoda(source())) == []


def test_agoda_rejects_location_mismatch(monkeypatch):
    install_client(monkeypatch, html_board(), {
        "https://careersatagoda.com/job/123-ai-engineer/": html_detail("Bangkok, Thailand")
    })
    with pytest.raises(RuntimeError, match="country disagree"):
        asyncio.run(collect_agoda(source()))


def test_agoda_rejects_missing_detail_requirements(monkeypatch):
    install_client(monkeypatch, html_board(), {
        "https://careersatagoda.com/job/123-ai-engineer/": html_detail(content="This is a job advert." * 15)
    })
    with pytest.raises(RuntimeError, match="responsibilities/requirements"):
        asyncio.run(collect_agoda(source()))


def test_agoda_network_failures_are_not_empty_results(monkeypatch):
    install_client(monkeypatch, "Forbidden", status=403)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(collect_agoda(source()))
