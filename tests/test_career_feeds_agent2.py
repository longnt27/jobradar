"""Focused mocked coverage for the Agent 2 first-party career adapters."""
import asyncio

import httpx
import pytest

from job_radar.career_feeds_agent2 import (
    collect_manatal, collect_trakstar, collect_vnpt_parent, collect_vnpt_technology,
)
from job_radar.feed_catalog import CAREER_FEEDS


def response(url, html="", status=200):
    return httpx.Response(status, text=html, request=httpx.Request("GET", url))


class MockClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url, **_):
        self.calls.append(url)
        value = self.routes.get(url)
        if value is None:
            raise AssertionError("Unexpected or missing first-party request: " + url)
        return response(url, value)


def patch_client(monkeypatch, routes):
    client = MockClient(routes)
    monkeypatch.setattr("job_radar.collectors._career_client", lambda: client)
    return client


def feed_source(name):
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == name)
    return {"url": feed.url, "name": name, "employer_name": name, "config": feed.options}


def test_four_new_adapters_are_scanner_routable():
    from job_radar.collectors import CAREER_ADAPTERS
    for name in ("Gear Inc", "Anduin Transactions", "VNPT Group", "VNPT Technology"):
        feed = next(x for x in CAREER_FEEDS if x.employer == name)
        assert feed.adapter in CAREER_ADAPTERS


def test_vnpt_parent_explicit_empty_filter(monkeypatch):
    url = feed_source("VNPT Group")["url"]
    patch_client(monkeypatch, {url: "<html><h2>Tập đoàn VNPT</h2><p>Không tìm thấy việc làm phù hợp</p></html>"})
    assert asyncio.run(collect_vnpt_parent(feed_source("VNPT Group"))) == []


def test_vnpt_parent_unrecognized_page_must_raise(monkeypatch):
    url = feed_source("VNPT Group")["url"]
    patch_client(monkeypatch, {url: "<html><h1>Website maintenance</h1></html>"})
    with pytest.raises(RuntimeError, match="filter disappeared"):
        asyncio.run(collect_vnpt_parent(feed_source("VNPT Group")))


def test_vnpt_technology_full_description_id_deadline(monkeypatch):
    source = feed_source("VNPT Technology")
    detail = "https://www.vnpt-technology.vn/vi/recruit_detail/53"
    patch_client(monkeypatch, {
        source["url"]: '<main><table><tr><td><a href="/vi/recruit_detail/53">Kỹ sư Phần mềm AI</a></td></tr></table></main>',
        detail: (
            '<html><h1>Kỹ sư Phần mềm AI</h1><main>'
            '<p>Nơi làm việc: Hà Nội</p><p>Hạn chót nhận hồ sơ: 31/12/2099</p>'
            '<h2>Mô tả công việc</h2><p>' + "Xây dựng mô hình AI và triển khai sản phẩm học máy. " * 7 +
            '</p><h3>Yêu cầu</h3><p>Python, thuật toán và hệ thống học máy.</p>'
            '<h2>Thông tin tuyển dụng khác</h2></main></html>'
        ),
    })
    result = asyncio.run(collect_vnpt_technology(source))
    assert len(result) == 1
    assert result[0].external_id == "53"
    assert result[0].location == "Hà Nội"
    assert result[0].company == "VNPT Technology"
    assert "Yêu cầu" in result[0].description
    assert result[0].url == detail


def test_gear_manatal_all_pages_dedup_and_full_content(monkeypatch):
    source = feed_source("Gear Inc")
    source["config"] = {"max_pages": 3}
    base = source["url"]
    first = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    second = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    a = "https://gear-inc-2.careers-page.com/jobs/" + first
    b = "https://gear-inc-2.careers-page.com/jobs/" + second
    card = lambda href, title: f'<a href="{href}">{title}</a>'
    patch_client(monkeypatch, {
        base: "<main>2 Open Positions Page 1 of 2" + card(a, "AI Engineer") + "</main>",
        base + "?page=2": "<main>2 Open Positions Page 2 of 2" + card(b, "Customer Support") + "</main>",
        a: (
            '<h1>AI Engineer</h1><main><p>Vietnam</p><h2>Job Description:</h2>'
            + "<p>Build and evaluate computer vision models for production platforms.</p>" * 7
            + "<h3>Requirements</h3><p>Python and evaluation benchmarks.</p>"
            + '<p>Powered by Manatal</p></main>'
        )
    })
    jobs = asyncio.run(collect_manatal(source))
    assert len(jobs) == 1
    assert jobs[0].external_id == first
    assert jobs[0].location == "Vietnam"
    assert jobs[0].apply_url.endswith(first + "/apply")
    assert "Requirements" in jobs[0].description


def test_gear_manatal_detects_missing_page(monkeypatch):
    source = feed_source("Gear Inc")
    source["config"] = {"max_pages": 3}
    base = source["url"]
    patch_client(monkeypatch, {
        base: '<main>2 Open Positions Page 1 of 2<a href="/jobs/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa">AI Engineer</a></main>',
        base + "?page=2": "<main>2 Open Positions Page 1 of 2</main>",
    })
    with pytest.raises(RuntimeError, match="pagination/count"):
        asyncio.run(collect_manatal(source))


def test_anduin_trakstar_reads_requirements_and_vietnam_scope(monkeypatch):
    source = feed_source("Anduin Transactions")
    detail = "https://anduin.hire.trakstar.com/jobs/fk01m54"
    patch_client(monkeypatch, {
        source["url"]: '<main><a href="/jobs/fk01m54">Software Engineer</a></main>',
        detail: (
            '<html><title>Software Engineer at Anduin Transactions</title><main>'
            '<h1>Software Engineer</h1><p>Ho Chi Minh City, Vietnam</p>'
            + "<p>Develop production software and ML-backed workflow systems.</p>" * 6
            + "<h3>Job Qualifications</h3><p>Python, system design, secure APIs.</p>"
            + '<h3>Application Form</h3></main></html>'
        ),
    })
    result = asyncio.run(collect_trakstar(source))
    assert len(result) == 1
    assert result[0].external_id == "fk01m54"
    assert result[0].company == "Anduin Transactions"
    assert "Job Qualifications" in result[0].description
    assert result[0].location == "Ho Chi Minh City, Vietnam"


def test_anduin_empty_without_explicit_notice_is_not_success(monkeypatch):
    source = feed_source("Anduin Transactions")
    patch_client(monkeypatch, {source["url"]: "<html><h1>Javascript required</h1></html>"})
    with pytest.raises(RuntimeError, match="no recognizable posting links"):
        asyncio.run(collect_trakstar(source))


def test_anduin_detail_heading_in_header_does_not_disappear(monkeypatch):
    src = feed_source("Anduin Transactions")
    url = "https://anduin.hire.trakstar.com/jobs/fk01m54"
    patch_client(monkeypatch, {
        src["url"]: '<main><a href="/jobs/fk01m54">Software Engineer Intern Ho Chi Minh City Vietnam</a></main>',
        url: ('<html><title>Software Engineer Intern at Anduin Transactions</title>'
              '<header><h1>Software Engineer Intern</h1><p>Ho Chi Minh City, Vietnam</p></header>'
              '<main><article><p>Job Responsibilities: ' + "Build reliable backend systems. " * 8
              + '</p><h3>Job Qualifications</h3><p>Experience with Python and APIs.</p>'
              '</article></main></html>'),
    })
    jobs = asyncio.run(collect_trakstar(src))
    assert len(jobs) == 1 and jobs[0].title == "Software Engineer Intern"
    assert jobs[0].external_id == "fk01m54"


def test_anduin_rejects_unrelated_detail_title(monkeypatch):
    src = feed_source("Anduin Transactions")
    url = "https://anduin.hire.trakstar.com/jobs/fk01m54"
    patch_client(monkeypatch, {
        src["url"]: '<main><a href="/jobs/fk01m54">Software Engineer Intern</a></main>',
        url: ('<html><title>Financial Controller at Anduin Transactions</title><header>'
              '<h1>Financial Controller</h1><p>Ho Chi Minh City, Vietnam</p></header>'
              '<main><h2>Job Qualifications</h2><p>' + "Finance and taxation experience. " * 8
              + '</p></main></html>')
    })
    with pytest.raises(RuntimeError, match="contradicts"):
        asyncio.run(collect_trakstar(src))


def test_vnpt_technology_accepts_official_www_to_bare_domain_links(monkeypatch):
    src = feed_source("VNPT Technology")
    detail = "https://vnpt-technology.vn/vi/recruit_detail/53"
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def get(self, url, **_):
            if url == src["url"]:
                return response(
                    "https://www.vnpt-technology.vn/vi/recruit",
                    '<a href="https://vnpt-technology.vn/vi/recruit_detail/53">Kỹ sư Phần mềm AI Junior</a>'
                )
            assert url == detail
            return response(detail, '<h1>Kỹ sư Phần mềm AI Junior</h1><main>'
                            '<p>Nơi làm việc: Hà Nội</p><p>Hạn chót nhận hồ sơ: 31/12/2099</p>'
                            '<h2>Mô tả công việc</h2><p>'
                            + "Nghiên cứu xây dựng hệ thống AI tích hợp dữ liệu. " * 7
                            + '</p><h2>Thông tin tuyển dụng khác</h2></main>')
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_vnpt_technology(src))
    assert len(jobs) == 1 and jobs[0].external_id == "53"
    assert jobs[0].location == "Hà Nội"


def test_manatal_retries_throttled_detail_without_losing_all_jobs(monkeypatch):
    import job_radar.career_feeds_agent2 as module
    sleeps = []
    async def fake_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    source = feed_source("Gear Inc")
    source["config"] = {"max_pages": 1, "pace_seconds": 0}
    base = source["url"]
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    job_url = base + "jobs/" + job_id
    calls = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def get(self, url):
            calls.append(url)
            if url == base:
                return response(url, '<main>1 Open Position Page 1 of 1<a href="/jobs/' + job_id + '">AI Engineer</a></main>')
            if calls.count(job_url) == 1:
                return httpx.Response(429, headers={"Retry-After":"3"}, request=httpx.Request("GET",url))
            return response(url, '<main><h1>AI Engineer</h1><p>Vietnam</p><h2>Job Description:</h2>'
                            + "Build production AI and machine learning platforms. " * 8
                            + "<h3>Requirements</h3><p>Python and statistical modelling.</p></main>")
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    jobs = asyncio.run(collect_manatal(source))
    assert len(jobs) == 1 and jobs[0].external_id == job_id
    assert calls.count(job_url) == 2 and 3.0 in sleeps


def test_manatal_does_not_suppress_exhausted_rate_limits(monkeypatch):
    import job_radar.career_feeds_agent2 as module
    async def fake_sleep(_seconds):
        return None
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    source = feed_source("Gear Inc")
    source["config"] = {"max_pages": 1, "pace_seconds": 0}
    base = source["url"]
    job_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    job_url = base + "jobs/" + job_id
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def get(self, url):
            if url == base:
                return response(url, '<main>1 Open Position Page 1 of 1<a href="/jobs/' + job_id + '">AI Engineer</a></main>')
            return httpx.Response(429, request=httpx.Request("GET",url))
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(collect_manatal(source))
