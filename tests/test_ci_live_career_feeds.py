"""Offline contract tests for scripts/ci_live_career_feeds.py.

The live HTTP counterpart runs on GitHub-hosted runners, not during pytest.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from job_radar.feed_catalog import CareerFeed
from job_radar.ingest import ObservedJob
from scripts.ci_live_career_feeds import _validate_jobs, probe_feed, select_feeds


DESCRIPTION = "Build production machine learning services, implement stable ingestion, evaluate model quality, and document deployment and monitoring practices. " * 2
FEED = CareerFeed("Example", "https://example.com/careers", "html_board", {"link_path": r"^/jobs/.+$"})


def valid_job() -> ObservedJob:
    return ObservedJob(
        title="AI Engineer",
        url="https://example.com/jobs/ai-1",
        company="Example",
        description=DESCRIPTION,
        location="Hanoi",
        external_id="ai-1",
        apply_url="https://example.com/apply/ai-1",
    )


def test_integrated_scope_contains_exact_25_new_feeds():
    selected = select_feeds("integrated")
    names = [feed.employer for feed in selected]
    assert len(names) == 25
    assert len(names) == len(set(names))
    assert {"VinBus", "Qualcomm", "Agoda", "Public Bank Vietnam", "VNPT Group"} <= set(names)


def test_shards_partition_the_integrated_scope():
    full = [feed.employer for feed in select_feeds("integrated")]
    chunks = [
        [feed.employer for feed in select_feeds("integrated", shard_index=i, shard_count=5)]
        for i in range(5)
    ]
    assert sorted(name for group in chunks for name in group) == sorted(full)
    assert all(len(group) == 5 for group in chunks)


def test_contract_rejects_duplicate_and_homepage_and_wrong_employer():
    job = valid_job()
    assert _validate_jobs(FEED, [job]) == []
    assert any("duplicate" in issue for issue in _validate_jobs(FEED, [job, job]))
    job.url = FEED.url
    assert any("homepage" in issue for issue in _validate_jobs(FEED, [job]))
    job.url = "https://example.com/jobs/ai-1"
    job.company = "OtherCo"
    assert any("employer" in issue for issue in _validate_jobs(FEED, [job]))


def test_probe_empty_is_inconclusive_not_a_validated_job():
    async def fake_collector(_settings, source):
        assert source["config"]["adapter"] == "html_board"
        assert source["config"]["link_path"] == r"^/jobs/.+$"
        return []

    result = asyncio.run(probe_feed(FEED, timeout=1, collector=fake_collector))
    assert result["status"] == "empty"
    assert result["count"] == 0
    assert "unproven" in result["note"]


def test_probe_error_is_explicit_never_converted_to_empty():
    async def fake_collector(_settings, source):
        raise RuntimeError("Unexpected login page returned by employer")

    result = asyncio.run(probe_feed(FEED, timeout=1, collector=fake_collector))
    assert result["status"] == "error"
    assert result["error_type"] == "RuntimeError"
    assert "login page" in result["error"]


def test_probe_valid_job_reports_detail_evidence():
    async def fake_collector(_settings, source):
        return [valid_job()]

    result = asyncio.run(probe_feed(FEED, timeout=1, collector=fake_collector))
    assert result["status"] == "ok"
    assert result["samples"][0]["description_characters"] >= 100
    assert urlsplit(result["samples"][0]["url"]).hostname == "example.com"


def test_probe_fails_closed_on_malformed_detail():
    async def fake_collector(_settings, source):
        job = valid_job()
        job.description = "Too short"
        return [job]

    result = asyncio.run(probe_feed(FEED, timeout=1, collector=fake_collector))
    assert result["status"] == "error"
    assert "missing full job description" in result["error"]


def test_probe_timeout_is_error():
    async def fake_collector(_settings, source):
        await asyncio.sleep(1)
        return [valid_job()]

    result = asyncio.run(probe_feed(FEED, timeout=0.001, collector=fake_collector))
    assert result["status"] == "error"
    assert result["error_type"] == "TimeoutError"
