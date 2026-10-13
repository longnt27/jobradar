import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from bs4 import BeautifulSoup

from job_radar.domestic_bank_feeds import _deadline, _notice_roles, collect_bank_notices, collect_vikki_bank
from job_radar.feed_catalog import CAREER_FEEDS


def _source(name):
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == name)
    return {"url": feed.url, "name": name, "config": {"adapter": feed.adapter, **feed.options}}


def _response(url, html, status=200):
    return httpx.Response(status, text=html, request=httpx.Request("GET", url))


def test_bank_notice_deadline_uses_end_of_submission_window():
    assert _deadline("Thời gian nhận hồ sơ: từ ngày 01/01/2099 đến 31/12/2099").isoformat() == "2099-12-31"
    assert _deadline("Đăng ngày 01/01/2020") is None


def test_bank_notice_roles_reject_generic_and_keep_explicit_single_role():
    html = BeautifulSoup("<article><h1>Thông báo tuyển dụng</h1><h3>Nội dung tuyển dụng</h3><ul>"
                         "<li>Chuyên viên công nghệ thông tin</li><li>Giao dịch viên</li>"
                         "</ul></article>", "html.parser")
    assert _notice_roles(html, "coopbank") == ["Chuyên viên công nghệ thông tin"]


def test_vikki_paginates_and_fetches_only_target_details(monkeypatch):
    feed = _source("Vikki Bank")
    called = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            called.append(url)
            if "?avia-element-paging=2" in url:
                return _response(url, '<main><h3><a href="/data-engineer/">Data Engineer</a></h3></main>')
            if url == feed["url"]:
                return _response(url, '<main><p>Page 1 of 2</p>'
                                       '<h3><a href="/ky-su-ai/">Kỹ sư AI</a></h3>'
                                       '<h3><a href="/giao-dich-vien/">Giao dịch viên</a></h3></main>')
            return _response(url, '<main><h1>Job description</h1><article><p>'
                                   + "Develop models and production AI data systems. " * 8
                                   + '</p><p>Địa điểm làm việc: Hà Nội</p></article></main>')

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_vikki_bank(feed))
    assert [job.title for job in jobs] == ["Kỹ sư AI", "Data Engineer"]
    assert [job.external_id for job in jobs] == ["ky-su-ai", "data-engineer"]
    assert all(job.company == "Vikki Bank" and job.apply_url == feed["url"] for job in jobs)
    assert all(job.location == "Hà Nội" for job in jobs)
    assert len([url for url in called if "giao-dich-vien" in url]) == 0


def test_vikki_listing_failure_is_not_silently_empty(monkeypatch):
    feed = _source("Vikki Bank")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            return _response(url, "<main><h1>Recruitment</h1></main>")

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError, match="no new job cards"):
        asyncio.run(collect_vikki_bank(feed))


def test_coopbank_notices_skip_expired_and_parse_explicit_role(monkeypatch):
    feed = _source("Co-opBank")
    feed["config"]["max_pages"] = 1
    detail = "https://co-opbank.vn/tuyen-dung/job-123/"
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            calls.append(url)
            if url == feed["url"]:
                return _response(url, f'<main><a href="{detail}">Thông báo tuyển dụng</a></main>')
            return _response(url, '<article><h1>Thông báo tuyển dụng</h1>'
                                   '<h3>Nội dung tuyển dụng</h3>'
                                   '<ul><li>Chuyên viên công nghệ thông tin</li><li>Giao dịch viên</li></ul>'
                                   '<p>Hạn nộp hồ sơ: 31/12/2099</p>'
                                   '<p>Địa điểm làm việc: Hà Nội</p>'
                                   '<p>' + ("Build systems with Python and databases. " * 8) + '</p></article>')

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_bank_notices(feed))
    assert len(jobs) == 1
    assert jobs[0].title == "Chuyên viên công nghệ thông tin"
    assert jobs[0].url == detail
    assert jobs[0].location == "Hà Nội"


def test_coopbank_multi_target_notice_does_not_make_duplicate_urls(monkeypatch):
    feed = _source("Co-opBank")
    feed["config"]["max_pages"] = 1
    detail = "https://co-opbank.vn/tuyen-dung/job-234/"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            return _response(url, f'<main><a href="{detail}">Thông báo tuyển dụng</a></main>' if url == feed["url"] else
                             '<article><h3>Nội dung tuyển dụng</h3><ul><li>Data Engineer</li><li>AI Engineer</li></ul>'
                             '<p>' + ("Requirements and qualifications. " * 9) + '</p></article>')

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError, match="Multiple roles"):
        asyncio.run(collect_bank_notices(feed))


def test_coopbank_empty_board_raises_instead_of_reporting_zero(monkeypatch):
    feed = _source("Co-opBank")

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url):
            return _response(url, "<main>Site navigation without recruitment notices</main>")

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError, match="no verifiable notice links"):
        asyncio.run(collect_bank_notices(feed))
