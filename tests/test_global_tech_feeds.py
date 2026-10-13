"""Isolated contract checks for Agent 8's global technology adapters."""
import asyncio

import httpx
import pytest

from job_radar.collectors import CAREER_ADAPTERS
from job_radar.feed_catalog import CAREER_FEEDS
from job_radar.global_tech_feeds import collect_eightfold, collect_workday_vietnam


def feed(name):
    return next(item for item in CAREER_FEEDS if item.employer == name)


def source(name):
    item = feed(name)
    return {"name": f"{name} careers", "employer_name": name, "url": item.url,
            "config": {"adapter": item.adapter, **item.options}}


@pytest.mark.parametrize("name, adapter", [
    ("Qualcomm", "eightfold"), ("NXP Vietnam", "workday_vietnam"),
    ("Cadence Vietnam", "workday_vietnam"),
])
def test_registered_employer_adapter(name, adapter):
    item = feed(name)
    assert item.adapter == adapter
    assert adapter in CAREER_ADAPTERS
    assert item.options


def test_eightfold_pcsx_pagination_and_details(monkeypatch):
    calls = []
    desc = "<p>Design production AI systems with Python and model evaluation.</p>" * 5

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, params):
            calls.append((url, params.copy()))
            if url.endswith("/search"):
                offset = int(params["start"])
                return httpx.Response(200, json={"data": {
                    "count": 2, "positions": [{
                        "id": str(offset + 21), "name": "AI Engineer",
                        "locations": ["Hanoi, Vietnam" if not offset else "Singapore"],
                    }],
                }}, request=httpx.Request("GET", url))
            return httpx.Response(200, json={"data": {"position": {
                "id": "21", "name": "AI Engineer", "locations": ["Hanoi, Vietnam"],
                "jobDescription": desc, "publicUrl": "/careers/job/21", "postedTs": 1790000000000,
            }}}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_eightfold(source("Qualcomm")))
    assert len(jobs) == 1
    assert jobs[0].company == "Qualcomm"
    assert jobs[0].external_id == "21"
    assert jobs[0].url == "https://careers.qualcomm.com/careers/job/21"
    assert "model evaluation" in jobs[0].description
    assert [params["start"] for url, params in calls if url.endswith("/search")] == ["0", "1"]
    assert len([url for url, _ in calls if "position_details" in url]) == 1


def test_eightfold_classic_fallback(monkeypatch):
    calls = []
    desc = "<p>Building semiconductor AI tooling and deploying high-performance models.</p>" * 5

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, params):
            calls.append(url)
            if url.endswith("/pcsx/search"):
                return httpx.Response(403, text="PCSX not enabled", request=httpx.Request("GET", url))
            if url.endswith("/apply/v2/jobs"):
                return httpx.Response(200, json={"positions": [
                    {"id": "17", "name": "Machine Learning Engineer", "locations": ["Vietnam"]},
                ], "count": 1}, request=httpx.Request("GET", url))
            return httpx.Response(200, json={
                "id": "17", "name": "Machine Learning Engineer",
                "locations": ["Vietnam"], "job_description": desc,
            }, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_eightfold(source("Qualcomm")))
    assert len(jobs) == 1 and jobs[0].external_id == "17"
    assert len(calls) == 3


@pytest.mark.parametrize("malformed", [
    {"data": {"unexpected": []}}, {"data": {"positions": [], "count": 2}},
])
def test_eightfold_never_mistakes_broken_feed_for_empty(monkeypatch, malformed):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, url, params):
            return httpx.Response(200, json=malformed, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError):
        asyncio.run(collect_eightfold(source("Qualcomm")))


@pytest.mark.parametrize("employer", ["NXP Vietnam", "Cadence Vietnam"])
def test_workday_vietnam_filters_global_positions_and_deduplicates(monkeypatch, employer):
    requested = []
    description = "<p>Develop silicon verification tooling and production machine learning models.</p>" * 5

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json):
            requested.append(json["searchText"])
            return httpx.Response(200, json={"total": 2, "jobPostings": [
                {"title": "AI Engineer", "externalPath": "/job/Singapore/AI_101"},
                {"title": "Software Engineer", "externalPath": "/job/Singapore/SW_102"},
            ]}, request=httpx.Request("POST", url))

        async def get(self, url):
            local = "101" in url
            return httpx.Response(200, json={"jobPostingInfo": {
                "jobReqId": "101" if local else "102", "location": "Singapore",
                "additionalLocations": ["Hanoi, Vietnam"] if local else ["Japan"],
                "jobDescription": description,
            }}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_workday_vietnam(source(employer)))
    assert len(jobs) == 1
    assert jobs[0].company == employer and jobs[0].external_id == "101"
    assert "Hanoi, Vietnam" in jobs[0].location
    assert requested == ["Vietnam", "Ho Chi Minh", "Hanoi"]
