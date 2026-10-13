"""Finance/insurance feed contract tests; all network calls are mocked."""
import asyncio

import httpx
import pytest

from job_radar.baoviet import collect_baoviet_announcement, parse_baoviet_announcement
from job_radar.collectors import collect_workday
from job_radar.feed_catalog import CAREER_FEEDS

LONG_DESC = "Maintain production data services, deployment monitoring, security and AI model systems. " * 4


def test_agent_six_feed_configuration():
    expected = {"Mirae Asset Vietnam", "MB Ageas Life", "Bao Viet",
                "FWD Vietnam", "Prudential Vietnam", "Manulife Vietnam", "AIA Vietnam"}
    feed_map = {f.employer: f for f in CAREER_FEEDS}
    assert expected <= set(feed_map)
    assert all(feed_map[name].url.startswith("https://") for name in expected)
    assert feed_map["Bao Viet"].adapter == "baoviet_announcement"
    assert all(feed_map[n].adapter == "workday" for n in [
        "FWD Vietnam", "Prudential Vietnam", "Manulife Vietnam", "AIA Vietnam"])


def test_baoviet_multiple_roles_preserve_distinct_ids_and_requirements():
    html = f"""
    <h1>Trung tâm Công nghệ thông tin tuyển dụng 2026</h1>
    <p>Hòm thư nhận CV: itc-tuyendung@baoviet.com.vn</p>
    <table>
      <tr><th>STT</th><th>Thông tin chung</th><th>Mô tả</th><th>Yêu cầu</th></tr>
      <tr><td>1</td><td>Mã số: AI-01<br>Chức danh: Kỹ sư dữ liệu<br>Nơi làm việc: Hà Nội</td>
          <td><p>{LONG_DESC}</p></td><td><p>{LONG_DESC}</p></td></tr>
      <tr><td>2</td><td>Mã số: SE-02<br>Chức danh: Kỹ sư phần mềm<br>Nơi làm việc: Hà Nội</td>
          <td><p>{LONG_DESC}</p></td><td><p>{LONG_DESC}</p></td></tr>
    </table>"""
    jobs = parse_baoviet_announcement(html, "https://baoviet.com.vn/vi/it-2026")
    assert len(jobs) == 2
    assert {j.external_id for j in jobs} == {"AI-01", "SE-02"}
    assert all(j.company == "Bao Viet" and j.location == "Hà Nội" for j in jobs)
    assert all(j.apply_url == "mailto:itc-tuyendung@baoviet.com.vn" for j in jobs)
    assert len({j.url for j in jobs}) == 2
    assert all("deployment monitoring" in j.description for j in jobs)


def test_baoviet_invalid_board_raises_instead_of_reporting_zero(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, url):
            return httpx.Response(200, text="<html><h1>Welcome</h1></html>",
                                  request=httpx.Request("GET", url))
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    with pytest.raises(RuntimeError, match="no group recruitment announcements"):
        asyncio.run(collect_baoviet_announcement({
            "url": "https://www.baoviet.com.vn/vi/tuyen-dung",
            "name": "Bao Viet careers", "config": {}}))


def test_workday_finance_location_scope_and_pagination(monkeypatch):
    requested = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, url, json):
            requested.append(json["offset"])
            if json["offset"] == 0:
                postings = [
                    {"title": "Data Engineer", "externalPath": "/job/1", "locationsText": "Hanoi, Vietnam"},
                    {"title": "AI Engineer", "externalPath": "/job/2", "locationsText": "London, UK"}]
            else:
                postings = []
            return httpx.Response(200, json={"total": 2, "jobPostings": postings},
                                  request=httpx.Request("POST", url))
        async def get(self, url):
            location = "Vietnam, Hanoi" if url.endswith("/1") else "London"
            return httpx.Response(200, json={"jobPostingInfo": {
                "jobReqId": url.rsplit("/", 1)[-1], "location": location,
                "jobDescription": "<p>" + LONG_DESC + "</p>"}},
                request=httpx.Request("GET", url))
    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    feed = next(f for f in CAREER_FEEDS if f.employer == "FWD Vietnam")
    jobs = asyncio.run(collect_workday({"name": "FWD Vietnam", "employer_name": "FWD Vietnam",
                                       "url": feed.url, "config": feed.options}))
    assert requested == [0]
    assert len(jobs) == 1 and jobs[0].company == "FWD Vietnam"
    assert jobs[0].external_id == "1"
    assert "Vietnam" in jobs[0].location
