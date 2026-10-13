"""Agent 9 regression checks: bounded employer-scoped international boards."""
import asyncio

import httpx
import pytest

from job_radar.collectors import collect_html_board
from job_radar.career_apis import collect_json_board
from job_radar.feed_catalog import CAREER_FEEDS


def _feed(name):
    return next(feed for feed in CAREER_FEEDS if feed.employer == name)


def _source(feed):
    return {"name": feed.employer, "employer_name": feed.employer, "url": feed.url, "config": feed.options}


def test_global_wave9_catalog_scope():
    assert _feed("Ampere").adapter == "html_board"
    assert _feed("Amazon / AWS Vietnam").adapter == "json_board"
    assert _feed("SAP Vietnam").adapter == "successfactors"
    assert "VNM" in _feed("Amazon / AWS Vietnam").options["params"]["normalized_country_code[]"]


def test_ampere_specific_job_links_full_detail_and_stable_id(monkeypatch):
    feed = _feed("Ampere")
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


def test_amazon_country_pagination_detail_and_dedup(monkeypatch):
    feed = _feed("Amazon / AWS Vietnam")
    requested = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def get(self, url, params=None):
            if "search.json" in url:
                offset = params["offset"]
                requested.append(offset)
                rows = [{"id_icims": str(offset + 1), "title": "Software Engineer",
                         "job_path": f"/en/jobs/{offset + 1}/software-engineer",
                         "country_code": "VNM", "normalized_location": "Hanoi, Vietnam",
                         "description": "Build reliable production services. " * 6,
                         "basic_qualifications": "Python and engineering fundamentals. " * 4}]
                return httpx.Response(200, json={"jobs": rows, "hits": 2},
                                      request=httpx.Request("GET", url))
            raise AssertionError(url)
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board(_source(feed)))
    assert requested == [0, 1]
    assert [j.external_id for j in jobs] == ["1", "2"]
    assert all(j.company == "Amazon / AWS Vietnam" for j in jobs)
    assert all("qualifications" in j.description.lower() for j in jobs)


def test_amazon_invalid_response_must_not_be_silent_empty(monkeypatch):
    feed = _feed("Amazon / AWS Vietnam")
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def get(self, url, params=None):
            return httpx.Response(200, json={"message": "sign in required"},
                                  request=httpx.Request("GET", url))
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError, match="unexpected job listing"):
        asyncio.run(collect_json_board(_source(feed)))
