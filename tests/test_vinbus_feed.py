"""Isolated first-party VinBus board pagination and job-detail checks."""
import asyncio

import httpx

from job_radar.collectors import collect_html_board
from job_radar.feed_catalog import CAREER_FEEDS


def test_vinbus_pages_preserve_detail_text_and_reject_non_job_links(monkeypatch):
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == "VinBus")
    urls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            urls.append(url)
            if url == feed.url:
                html = '<a href="/senior-ai-engineer">Senior AI Engineer</a><a href="/tin-tuc">Tin tức</a>'
            elif url.endswith("?page=2"):
                html = '<a href="/data-engineer">Data Engineer</a>'
            elif url.endswith("?page=3"):
                html = '<a href="/tin-tuc">Tin tức</a>'
            elif url.endswith("/senior-ai-engineer") or url.endswith("/data-engineer"):
                title = "Data Engineer" if url.endswith("data-engineer") else "Senior AI Engineer"
                html = (
                    f'<main><h1>{title}</h1><div class="job-location">Hà Nội</div>'
                    '<section><h2>Mô tả công việc</h2><p>Design and deploy data systems, '
                    'develop robust production pipelines and own technical delivery.</p>'
                    '<h2>Yêu cầu</h2><ul><li>Python</li><li>SQL</li>'
                    '<li>Model evaluation and infrastructure automation</li></ul></section></main>'
                )
            else:
                raise AssertionError(f"unexpected request: {url}")
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board({
        "name": "VinBus careers", "employer_name": feed.employer,
        "url": feed.url, "config": {"adapter": feed.adapter, **feed.options},
    }))
    assert len(jobs) == 2
    assert {job.title for job in jobs} == {"Senior AI Engineer", "Data Engineer"}
    assert all(job.company == "VinBus" and job.location == "Hà Nội" for job in jobs)
    assert all("Model evaluation" in job.description for job in jobs)
    assert all(job.apply_url == job.url for job in jobs)
    assert feed.url + "?page=2" in urls
    assert feed.url + "?page=3" in urls
    assert "https://vinbus.vn/tin-tuc" not in urls
