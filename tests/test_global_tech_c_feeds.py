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
