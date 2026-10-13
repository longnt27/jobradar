"""Agent 3 FPT and CMC career-feed parser and pagination regressions."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from job_radar.collectors import _expired_posting, collect_html_board
from job_radar.career_apis import collect_json_board
from job_radar.feed_catalog import CAREER_FEEDS, html


DETAIL = ("Develop and maintain production machine learning and data pipelines with reproducible "
          "evaluation and code review, collaborating with engineers on deployed services. " * 2)


def _source(employer):
    if employer == "CMC University":
        # Preserve generic notice parsing coverage for the retired source.
        feed = html(employer, "https://cmcu.edu.vn/tin-tuyen-dung/", r"^/thong-bao-tuyen-dung-[^/?#]+/?$",
                    pagination="wordpress", max_pages=3, description_selector=".entry-content, article",
                    location_regex=r"Địa chỉ\s*:\s*(.{5,180}?)(?=\s+(?:Hình thức|Liên hệ)\s*:|$)",
                    require_links=True, strict_detail=True, external_id_from_path=True)
    else:
        feed = next(feed for feed in CAREER_FEEDS if feed.employer == employer)
    return {"url": feed.url, "name": employer + " careers", "employer_name": employer,
            "config": {"adapter": feed.adapter, **feed.options}}


def _response(url, body="", status=200):
    return httpx.Response(status, text=body, request=httpx.Request("GET", url))


def test_fpt_software_public_api_keeps_first_page_requirements_and_identity(monkeypatch):
    pages = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            pages.append(params["page"])
            rows = [{"id": 123, "slug": "ai-engineer", "title": "AI Engineer"},
                    {"id": 124, "slug": "sales", "title": "Account Manager"}] if params["page"] == 0 else [
                        {"id": 456, "slug": "data-engineer", "title": "Data Engineer"}]
            for row in rows:
                row.update(description="<p>Responsibilities: " + DETAIL + "</p><h2>Requirements</h2><p>Python and SQL</p>",
                           locationName="Ha Noi, Viet Nam", closingDate="2099-12-31", postedDate="2026-10-01", isActive=True)
            return httpx.Response(200, json={"content": rows, "totalElements": 3}, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_json_board(_source("FPT Software")))
    assert pages == [0, 1]
    assert {job.title for job in jobs} == {"AI Engineer", "Data Engineer"}
    assert {job.external_id for job in jobs} == {"123", "456"}
    assert all(job.company == "FPT Software" and job.location == "Ha Noi, Viet Nam" for job in jobs)
    assert all("Requirements" in job.description and "Python and SQL" in job.description for job in jobs)
    assert {job.url for job in jobs} == {"https://career.fpt-software.com/jobs/ai-engineer", "https://career.fpt-software.com/jobs/data-engineer"}


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


@pytest.mark.parametrize("employer", ["CMC University"])
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


def test_unexpected_public_api_failure_is_not_an_empty_feed(monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params=None):
            return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(collect_json_board(_source("FPT Software")))


def test_english_and_numeric_deadline_expiry():
    assert _expired_posting("Apply Before: January 02, 2020")
    assert _expired_posting("Closing date: 01/02/2020")
    assert not _expired_posting("Apply Before: December 31, 2099")
    assert not _expired_posting("This posting was updated January 02, 2020")


def test_scope_only_verified_employers_are_registered():
    manifest = json.loads((Path(__file__).parents[1] / "docs/career-feed-targets.json").read_text(encoding="utf-8"))
    targets = {target["employer"]: target for target in manifest["targets"]}
    available = {feed.employer for feed in CAREER_FEEDS}
    for employer in ("FPT Software",):
        assert employer in available
        assert targets[employer]["status"] == "verified"
        assert targets[employer]["adapter"] == ("json_board" if employer == "FPT Software" else "html_board")
    assert "CMC University" not in available
    assert targets["CMC University"]["status"] == "removed"
    for employer in ("FPT Corporation", "FPT IS", "FPT.AI", "CMC Corporation", "CMC Cloud"):
        assert employer not in available
        assert targets[employer]["status"] == "blocked"
        assert targets[employer]["evidence"]
