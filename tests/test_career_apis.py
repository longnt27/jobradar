import asyncio

import httpx
import pytest

from job_radar.career_apis import collect_json_board
from job_radar.collectors import _expired_posting


DESCRIPTION = "Develop reliable Python and machine learning services with model evaluation, monitoring, and reproducible production deployments. " * 2


def test_json_board_follows_server_page_size_and_keeps_full_requirements(monkeypatch):
    requested = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            requested.append(params["from"])
            offset = params["from"]
            row = {"id": offset + 1, "name": "AI Engineer", "seo": {"url": f"/jobs/{offset + 1}"},
                   "description": DESCRIPTION, "category": {"requirements": ["Python", "Model evaluation"]},
                   "country": [{"name": "Viet Nam" if offset == 0 else "USA"}], "created": "2026-10-01T12:00:00Z"}
            return httpx.Response(200, json={"jobs": [row], "total": 2}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    source = {"url": "https://example.org/careers", "name": "Example careers", "employer_name": "Example", "config": {
        "api_url": "https://example.org/api/jobs", "offset_param": "from", "limit_param": "size",
        "rows_path": "jobs", "total_field": "total", "title_field": "name", "url_field": "seo.url",
        "description_sections": {"Description": "description", "Requirements": "category.requirements"},
        "location_fields": ["country"], "location_include": "Viet Nam", "published_field": "created",
    }}
    jobs = asyncio.run(collect_json_board(source))
    assert requested == [0, 1]
    assert len(jobs) == 1
    assert jobs[0].url == "https://example.org/jobs/1" and jobs[0].company == "Example"
    assert "Requirements\n\n• Python\n• Model evaluation" in jobs[0].description
    assert jobs[0].published_at == "2026-10-01T12:00:00+00:00"


@pytest.mark.parametrize("response", [{"unexpected": []}, {"jobs": ["broken"]}, {"jobs": [], "total": 5}])
def test_json_board_does_not_report_malformed_or_incomplete_listing_as_empty(monkeypatch, response):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            return httpx.Response(200, json=response, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError):
        asyncio.run(collect_json_board({"url": "https://example.org/careers", "name": "Example", "config": {
            "api_url": "https://example.org/api", "rows_path": "jobs", "total_field": "total",
            "title_field": "title", "url_field": "url",
        }}))


def test_wordpress_job_terms_deadline_and_detail_content(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            if "/wp-json/" in url:
                row = {"id": 42, "title": {"rendered": "AI Engineer &amp; Researcher"},
                       "link": "https://example.org/job/ai-engineer", "content": {"rendered": f"<p>{DESCRIPTION}</p>"},
                       "_embedded": {"wp:term": [[{"taxonomy": "location", "name": "Hanoi - Vietnam"}],
                                                [{"taxonomy": "workplace-type", "name": "Hybrid"}]]}}
                return httpx.Response(200, json=[row], headers={"X-WP-Total": "1"}, request=httpx.Request("GET", url))
            return httpx.Response(200, text="<main>Closing date: 31.12.2099</main>", request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board({"url": "https://example.org/jobs-finder", "name": "Example", "config": {
        "api_url": "https://example.org/wp-json/wp/v2/job", "title_field": "title.rendered", "url_field": "link",
        "description_sections": {"": "content.rendered"}, "location_taxonomy": "location", "mode_taxonomy": "workplace-type",
        "location_include": "Vietnam", "detail_selector": "main",
    }}))
    assert jobs[0].title == "AI Engineer & Researcher"
    assert jobs[0].location == "Hanoi - Vietnam" and jobs[0].work_mode == "Hybrid"
    assert "Closing date" not in jobs[0].description
    assert _expired_posting("Closing date: 31.12.2020")
    assert not _expired_posting("Closing date: 31.12.2099")
