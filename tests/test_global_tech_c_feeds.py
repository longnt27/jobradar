"""Agent 10: independently owned official company feed contracts."""
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from job_radar.feed_catalog import CAREER_FEEDS
from job_radar.collectors import _specific_posting_url


def test_agent10_sources_match_owned_manifest():
    targets = json.loads((Path(__file__).resolve().parents[1] / "docs/career-feed-targets.json").read_text())["targets"]
    subset = {item["employer"]: item for item in targets if item["employer"] in {
        "Ericsson Vietnam", "Nokia Vietnam", "Huawei Vietnam", "Panasonic Vietnam",
        "Toyota Vietnam", "Honda Vietnam", "Hyundai Motor Vietnam"}}
    feeds = {feed.employer: feed for feed in CAREER_FEEDS}
    assert len(subset) == 7
    for employer, record in subset.items():
        assert record["status"] in {"verified", "blocked"}
        assert record["career_url"] and record["evidence"]
        if record["status"] == "verified":
            assert feeds[employer].adapter == record["adapter"]
            assert feeds[employer].url == record["career_url"]
        else:
            assert employer not in feeds


def test_honda_preserves_unique_posting_query_ids():
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == "Honda Vietnam")
    assert feed.options["keep_query"]
    assert feed.options["max_results"] >= 100
    import re
    pattern = re.compile(feed.options["link_path"])
    for id_ in ("1002", "1116"):
        url = f"https://www.honda.com.vn/tuyen-dung/chi-tiet-co-hoi-nghe-nghiep?id={id_}"
        assert pattern.fullmatch(urlsplit(url).path)
        assert _specific_posting_url(url, feed.url)
        assert parse_qs(urlsplit(url).query)["id"] == [id_]
    assert not pattern.fullmatch("/tuyen-dung/co-hoi-nghe-nghiep")


def test_hyundai_rejects_generic_career_paths():
    import re
    feed = next(feed for feed in CAREER_FEEDS if feed.employer == "Hyundai Motor Vietnam")
    pattern = re.compile(feed.options["link_path"])
    assert pattern.fullmatch("/job/chuyen-vien-chinh-phan-tich-nghiep-vu-kiem-thu.35ac0f5e.html")
    assert not pattern.fullmatch("/job-search/all-jobs/en")
    assert not pattern.fullmatch("/job/marketing")

def test_honda_collects_unique_detail_posts_and_skips_nontech(monkeypatch):
    import asyncio
    import httpx
    from job_radar.collectors import collect_html_board

    listing = """<html><body>
      <a href="/tuyen-dung/chi-tiet-co-hoi-nghe-nghiep?id=1002">Kỹ sư phần mềm</a>
      <a href="/tuyen-dung/chi-tiet-co-hoi-nghe-nghiep?id=1003">Nhân viên kinh doanh</a>
    </body></html>"""
    detail = """<html><main><h1>Kỹ sư phần mềm</h1><p>""" + (
        "Xây dựng và bảo trì hệ thống phần mềm, kiểm thử, tối ưu hiệu năng sản phẩm. " * 4
    ) + """</p></main></html>"""
    seen = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, params=None):
            seen.append(url)
            return httpx.Response(200, text=detail if "id=" in url else listing,
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    feed = next(x for x in CAREER_FEEDS if x.employer == "Honda Vietnam")
    jobs = asyncio.run(collect_html_board({"url": feed.url, "name": feed.employer,
                                           "config": feed.options}))
    assert len(jobs) == 1
    assert jobs[0].url.endswith("?id=1002")
    assert "id=1003" not in " ".join(seen)


def test_hyundai_collects_listing_to_full_detail(monkeypatch):
    import asyncio
    import httpx
    from job_radar.collectors import collect_html_board

    job_url = "/job/chuyen-vien-chinh-phan-tich-nghiep-vu-kiem-thu.35ac0f5e.html"
    listing = f'<html><a href="{job_url}">Chuyên viên chính Phân tích Nghiệp vụ &amp; Kiểm thử</a></html>'
    detail = "<html><main><h1>Chuyên viên chính Phân tích Nghiệp vụ &amp; Kiểm thử</h1>" + (
        "<p>Phân tích nghiệp vụ, xây dựng kịch bản kiểm thử phần mềm, phối hợp với đội kỹ thuật.</p>" * 4
    ) + "</main></html>"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url, params=None):
            return httpx.Response(200, text=detail if "/job/" in url else listing,
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr("job_radar.collectors._career_client", Client)
    feed = next(x for x in CAREER_FEEDS if x.employer == "Hyundai Motor Vietnam")
    jobs = asyncio.run(collect_html_board({"url": feed.url, "name": feed.employer,
                                           "config": feed.options}))
    assert len(jobs) == 1
    assert jobs[0].company == "Hyundai Motor Vietnam"
    assert jobs[0].url.endswith(job_url)
    assert "kiểm thử phần mềm" in jobs[0].description

def test_hyundai_expiry_is_respected():
    from job_radar.collectors import _expired_posting
    assert _expired_posting("Expired Date: 01/01/2020")
    assert not _expired_posting("Expired Date: 01/01/2099")
