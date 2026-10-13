"""Agent 3 FPT and CMC career-feed parser and pagination regressions."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from job_radar.collectors import _expired_posting, collect_html_board
from job_radar.feed_catalog import CAREER_FEEDS


DETAIL = ("Develop and maintain production machine learning and data pipelines with reproducible "
          "evaluation and code review, collaborating with engineers on deployed services. " * 2)


def _source(employer):
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == employer)
    return {"url": feed.url, "name": employer + " careers", "employer_name": employer,
            "config": {"adapter": feed.adapter, **feed.options}}


def _response(url, body="", status=200):
    return httpx.Response(status, text=body, request=httpx.Request("GET", url))


def test_fpt_software_uses_page_job_pagination_and_keeps_role_details(monkeypatch):
    seen = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            seen.append(url)
            if url.endswith("/jobs-search/"):
                return _response(url, '<a href="/jobs/data-engineer-123">View Details</a>')
            if "page_job=2" in url:
                return _response(url, '<a href="/jobs/ai-engineer-456">common.viewDetails</a>')
            if "page_job=" in url:
                return _response(url, "<main>No more jobs</main>")
            if "/jobs/" in url:
                slug = url.split("/jobs/")[1]
                title = "Data Engineer" if slug.startswith("data-") else "AI Engineer"
                return _response(url, (f"<article><h1>{title}</h1><main><div class='job-location'>"
                                      f"Ha Noi, Viet Nam</div><p>{DETAIL}</p>"
                                      "<p>Apply Before: December 31, 2099</p></main>"
                                      "<a href='https://career.fpt-software.com/apply/123'>Apply Now</a></article>"))
            raise AssertionError(url)

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    source = _source("FPT Software")
    source["config"]["max_pages"] = 3
    jobs = asyncio.run(collect_html_board(source))
    assert {job.title for job in jobs} == {"AI Engineer", "Data Engineer"}
    assert all(job.company == "FPT Software" and job.external_id for job in jobs)
    assert {job.external_id for job in jobs} == {"data-engineer-123", "ai-engineer-456"}
    assert all(job.location == "Ha Noi, Viet Nam" for job in jobs)
    assert all("machine learning" in job.description for job in jobs)
    assert all(job.apply_url == "https://career.fpt-software.com/apply/123" for job in jobs)
    assert any("page_job=2" in url for url in seen)
    assert not any("?page=" in url for url in seen)


def test_cmc_university_recruitment_notices_keep_contact_and_location(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            if url.endswith("/tin-tuyen-dung/"):
                return _response(url, "<a href='/thong-bao-tuyen-dung-giang-vien-cong-nghe-thong-tin/'>Read more</a>")
            if "/page/2/" in url:
                return _response(url, "<a href='/thong-bao-tuyen-dung-chuyen-vien-ung-dung-ai/'>Read more</a>")
            if "/page/3/" in url:
                return _response(url, "<main>Archive exhausted</main>")
            if "/thong-bao-tuyen-dung-" in url:
                title = ("Giảng viên Công nghệ Thông tin" if "giang-vien" in url
                         else "Chuyên viên Đào tạo và Ứng dụng AI")
                return _response(url, (f"<article><h1>{title}</h1><div class='entry-content'>"
                                      f"<p>Địa chỉ: 84C Nguyễn Thanh Bình, Hà Đông, Hà Nội</p>"
                                      f"<p>Hình thức: Cơ hữu</p><p>{DETAIL}</p>"
                                      "<p><a href='mailto:recruitment@cmcu.edu.vn'>Nộp hồ sơ</a></p>"
                                      "</div></article>"))
            raise AssertionError(url)

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_html_board(_source("CMC University")))
    assert len(jobs) == 2
    assert all(job.company == "CMC University" for job in jobs)
    assert all(job.external_id.startswith("thong-bao-tuyen-dung-") for job in jobs)
    assert all(job.location == "84C Nguyễn Thanh Bình, Hà Đông, Hà Nội" for job in jobs)
    assert all(job.apply_url == "mailto:recruitment@cmcu.edu.vn" for job in jobs)
    assert all("machine learning" in job.description for job in jobs)


@pytest.mark.parametrize("employer", ["FPT Software", "CMC University"])
def test_known_nonempty_career_boards_do_not_silently_become_empty(monkeypatch, employer):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            return _response(url, "<main>Unexpected page without job links</main>")

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    source = _source(employer)
    source["config"]["max_pages"] = 1
    with pytest.raises(RuntimeError, match="no posting links"):
        asyncio.run(collect_html_board(source))


def test_unexpected_job_detail_failure_is_not_an_empty_feed(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            if url.endswith("/jobs-search/"):
                return _response(url, "<a href='/jobs/ai-engineer-456'>View Details</a>")
            if "page_job=" in url:
                return _response(url, "")
            return _response(url, status=503)

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    source = _source("FPT Software")
    source["config"]["max_pages"] = 1
    with pytest.raises(RuntimeError, match="could not be fetched"):
        asyncio.run(collect_html_board(source))


def test_english_and_numeric_deadline_expiry():
    assert _expired_posting("Apply Before: January 02, 2020")
    assert _expired_posting("Closing date: 01/02/2020")
    assert not _expired_posting("Apply Before: December 31, 2099")
    assert not _expired_posting("This posting was updated January 02, 2020")


def test_scope_only_verified_employers_are_registered():
    manifest = json.loads((Path(__file__).parents[1] / "docs/career-feed-targets.json").read_text(encoding="utf-8"))
    targets = {target["employer"]: target for target in manifest["targets"]}
    available = {feed.employer for feed in CAREER_FEEDS}
    for employer in ("FPT Software", "CMC University"):
        assert employer in available
        assert targets[employer]["status"] == "verified"
        assert targets[employer]["adapter"] == "html_board"
    for employer in ("FPT Corporation", "FPT IS", "FPT.AI", "CMC Corporation", "CMC Cloud"):
        assert employer not in available
        assert targets[employer]["status"] == "blocked"
        assert targets[employer]["evidence"]
