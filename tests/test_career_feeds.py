import asyncio
import json
from pathlib import Path

import httpx

from bs4 import BeautifulSoup

from job_radar.collectors import _application_destination, _expired_posting, _target_title, collect_html_board
from job_radar.db import Database
from job_radar.employer_scope import HCMC_BASED
from job_radar.feed_catalog import CAREER_FEEDS
from job_radar.ranking import score_job
from job_radar.seeds import seed


def test_company_feeds_are_seeded_idempotently(tmp_path: Path) -> None:
    assert len(CAREER_FEEDS) == len({feed.employer for feed in CAREER_FEEDS}) >= 40
    assert {"VinDynamics", "VinRobotics", "VinMotion", "Techcombank", "Vietcombank", "MB Bank", "VPBank", "GPBank", "BIDV", "VNPT AI", "CMC Telecom"} <= {
        feed.employer for feed in CAREER_FEEDS
    }
    assert not {feed.employer for feed in CAREER_FEEDS} & HCMC_BASED.keys()
    assert all("linkedin.com" not in feed.url and "facebook.com" not in feed.url for feed in CAREER_FEEDS)

    db = Database(tmp_path / "jobs.db")
    seed(db)
    seed(db)
    rows = db.all("SELECT s.name,s.interval_minutes,s.config,e.name AS employer FROM sources s JOIN employers e ON e.id=s.employer_id WHERE s.kind='career'")
    assert len(rows) == len(CAREER_FEEDS)
    assert {row["employer"] for row in rows} == {feed.employer for feed in CAREER_FEEDS}
    assert all(row["interval_minutes"] == 1440 and '"adapter"' in row["config"] for row in rows)

    cmc = next(row for row in rows if row["employer"] == "CMC Global")
    assert json.loads(cmc["config"])["title_include"]
    db.execute("UPDATE sources SET interval_minutes=240 WHERE kind='career'")
    seed(db)
    assert {row["interval_minutes"] for row in db.all("SELECT interval_minutes FROM sources WHERE kind='career'")} == {1440}


def test_cmc_uses_card_titles_to_filter_before_detail_limit(monkeypatch) -> None:
    cmc = next(feed for feed in CAREER_FEEDS if feed.employer == "CMC Global")
    config = {"adapter": cmc.adapter, **cmc.options, "max_pages": 2, "max_results": 1}
    listing = "".join(
        f'<li class="__careers-post-wrapper"><h3>Frontend Developer {index}</h3>'
        f'<a href="/career/frontend-{index}/">Learn more</a></li>'
        for index in range(120)
    )
    second_page = '<li class="__careers-post-wrapper"><h3>AI Engineer</h3><a href="/career/ai-engineer/">Learn more</a></li>'
    requested: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            requested.append(url)
            if url.endswith("/career/"):
                html = f"<main>{listing}</main>"
            elif url.endswith("/career/page/2/"):
                html = f"<main>{second_page}</main>"
            else:
                html = "<main><h1>AI Engineer</h1><p>" + "Build and deploy AI systems with Python. " * 8 + "</p></main>"
            return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board({"url": cmc.url, "name": "CMC Global careers", "config": config}))
    assert [job.title for job in jobs] == ["AI Engineer"]
    assert requested == [cmc.url, "https://cmcglobal.com.vn/career/page/2/",
                         "https://cmcglobal.com.vn/career/ai-engineer/"]


def test_expired_requisition_and_non_job_heading_are_rejected() -> None:
    assert _expired_posting("Senior AI Engineer Application deadline 14/11 — 14/11/2025")
    assert not _expired_posting("AI Engineer Application deadline 10/09 — 10/10/2099")
    assert not _expired_posting("Hạn nộp hồ sơ 28/09/2026 - 28/10/2099")
    assert _target_title("Senior Data Engineer")
    assert not _target_title("Careers")
    assert not _target_title("We build AI systems and seek an engineer. " * 10)


def test_company_publication_time_without_timezone_can_be_ranked() -> None:
    score, detail = score_job({
        "title": "AI Engineer", "description": "Build robot perception models.",
        "location": "Hanoi", "published_at": "2026-03-31T20:53:33",
    }, {})
    assert score > 0
    assert detail["components"]["freshness"] >= 0


def test_career_application_destination_prefers_apply_link_email_then_posting() -> None:
    posting = "https://example.org/jobs/42"
    linked = BeautifulSoup('<a href="/apply/42">Apply now</a><p>Email jobs@example.org</p>', "html.parser")
    assert _application_destination(linked, posting) == "https://example.org/apply/42"
    emailed = BeautifulSoup('<p>To apply, send your CV to careers@example.org</p>', "html.parser")
    assert _application_destination(emailed, posting) == "mailto:careers@example.org"
    assert _application_destination(BeautifulSoup("<p>Job details</p>", "html.parser"), posting) == posting
