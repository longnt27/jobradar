"""Agent 9 regression checks: bounded employer-scoped international boards."""
import asyncio

import httpx
import pytest

from job_radar.collectors import collect_html_board
from job_radar.feed_catalog import CAREER_FEEDS, html


def _feed(name):
    return next(feed for feed in CAREER_FEEDS if feed.employer == name)


def _source(feed):
    return {"name": feed.employer, "employer_name": feed.employer, "url": feed.url, "config": feed.options}


def test_global_wave9_catalog_scope():
    assert "Ampere" not in {feed.employer for feed in CAREER_FEEDS}
    assert _feed("SAP Vietnam").adapter == "successfactors"


def test_ampere_specific_job_links_full_detail_and_stable_id(monkeypatch):
    # Exercise generic HTML extraction without registering the retired board.
    feed = html("Ampere", "https://careers.amperecomputing.com/search/jobs/in/sg-ho-chi-minh-sai-gon",
                r"^/jobs/\d+-[^/?#]+$", description_selector="main",
                external_id_pattern=r"/jobs/(\d+)-", location_text_pattern=r"(?m)^Location:\s*([^\n]+)")
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def get(self, url):
            if "/search/jobs/" in url:
                html = '<a href="/jobs/1234-software-engineer">Software Engineer</a>'
            else:
                html = ('<main><h1>Software Engineer</h1><p>Responsibilities: '
                        + 'Design and implement production machine learning systems. ' * 5
                        + '</p><p>Location: Ho Chi Minh City, Vietnam</p></main>')
            return httpx.Response(200, text=html, request=httpx.Request("GET",url))
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board(_source(feed)))
    assert len(jobs) == 1
    assert jobs[0].company == "Ampere"
    assert jobs[0].external_id == "1234"
    assert "production machine learning" in jobs[0].description
    assert jobs[0].url.endswith("/jobs/1234-software-engineer")


