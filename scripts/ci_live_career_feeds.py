"""Bounded, read-only runtime probes for company career collectors on GitHub Actions.

This is deliberately separate from mocked pytest cases. A zero-result scan is
inconclusive, not "verified"; network or parsing exceptions are hard failures.
No local database, accounts, credentials, or application submission is used.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from job_radar.collectors import collect_source
from job_radar.feed_catalog import CAREER_FEEDS, CareerFeed
from job_radar.ingest import ObservedJob, normalize_url


MANIFEST = Path(__file__).resolve().parents[1] / "docs" / "career-feed-targets.json"


def select_feeds(
    scope: str, *, shard_index: int = 0, shard_count: int = 1,
    manifest: dict | None = None,
) -> list[CareerFeed]:
    if not (shard_count > 0 and 0 <= shard_index < shard_count):
        raise ValueError("shard_index must be between 0 and shard_count - 1")
    manifest = manifest if manifest is not None else json.loads(MANIFEST.read_text(encoding="utf-8"))
    feeds = {feed.employer: feed for feed in CAREER_FEEDS}
    if len(feeds) != len(CAREER_FEEDS):
        raise RuntimeError("Duplicate employer in CAREER_FEEDS")

    if scope == "integrated":
        selected = set(manifest["integration"]["new_feed_employers"])
        absent = selected - feeds.keys()
        if absent:
            raise RuntimeError(f"Integrated employers missing adapters: {sorted(absent)}")
    elif scope == "provisional":
        selected = {
            row["employer"] for row in manifest["targets"]
            if row["status"] in {"pending", "adapter_unverified", "implemented_unverified"}
            and row.get("adapter")
        }
    elif scope == "all":
        selected = set(feeds)
    else:
        raise ValueError(f"Unsupported feed test scope: {scope}")

    ordered = [feeds[employer] for employer in sorted(selected)]
    return [feed for index, feed in enumerate(ordered) if index % shard_count == shard_index]


def _validate_jobs(feed: CareerFeed, jobs: list[ObservedJob]) -> list[str]:
    if not isinstance(jobs, list):
        return ["collector returned a non-list result"]
    errors: list[str] = []
    urls: set[str] = set()
    external_ids: set[str] = set()
    for index, job in enumerate(jobs):
        if not isinstance(job, ObservedJob):
            errors.append(f"result {index} is not an ObservedJob")
            continue
        uri = urlsplit(job.url or "")
        if uri.scheme not in {"https", "http"} or not uri.netloc:
            errors.append(f"result {index}: invalid job URL")
        if normalize_url(job.url) == normalize_url(feed.url):
            errors.append(f"result {index}: job URL is only the career homepage")
        if not job.title or not job.title.strip() or len(job.title) > 180:
            errors.append(f"result {index}: missing or invalid title")
        if not job.description or len(job.description.strip()) < 100:
            errors.append(f"result {index}: missing full job description")
        if not job.company or not job.company.strip():
            errors.append(f"result {index}: missing employer identity")
        if feed.employer != "Vingroup" and job.company != feed.employer:
            errors.append(f"result {index}: wrong employer attribution ({job.company!r})")
        if not job.apply_url:
            errors.append(f"result {index}: missing application destination")
        elif urlsplit(job.apply_url).scheme not in {"http", "https", "mailto"}:
            errors.append(f"result {index}: invalid application destination")
        normalized = normalize_url(job.url)
        if normalized in urls:
            errors.append(f"result {index}: duplicate job URL")
        urls.add(normalized)
        if job.external_id:
            if job.external_id in external_ids:
                errors.append(f"result {index}: duplicate external_id")
            external_ids.add(job.external_id)
    return errors


async def probe_feed(
    feed: CareerFeed, *, timeout: float = 90,
    collector=collect_source,
) -> dict:
    source = {
        "kind": "career",
        "name": f"{feed.employer} careers",
        "employer_name": feed.employer,
        "url": feed.url,
        "config": {"adapter": feed.adapter, **feed.options},
    }
    started = time.monotonic()
    result = {
        "employer": feed.employer,
        "adapter": feed.adapter,
        "source_url": feed.url,
    }
    try:
        jobs = await asyncio.wait_for(collector(None, source), timeout=timeout)
        contract_errors = _validate_jobs(feed, jobs)
        if contract_errors:
            raise ValueError("; ".join(contract_errors[:8]))
        result.update(
            status="ok" if jobs else "empty",
            count=len(jobs),
            samples=[
                {
                    "title": job.title,
                    "url": job.url,
                    "location": job.location,
                    "external_id": job.external_id,
                    "description_characters": len(job.description),
                }
                for job in jobs[:3]
            ],
        )
        if not jobs:
            result["note"] = "No relevant postings returned; full job-detail parsing remains unproven"
    except Exception as exc:
        result.update(
            status="error",
            error_type=type(exc).__name__,
            error=str(exc)[:650],
            count=0,
        )
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


async def probe_shard(feeds: list[CareerFeed], timeout: float, max_concurrency: int) -> list[dict]:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def limited(feed: CareerFeed) -> dict:
        async with semaphore:
            result = await probe_feed(feed, timeout=timeout)
            status = result["status"].upper()
            print(f"{status} {feed.employer}: {result['count']} role(s), {result['seconds']}s", flush=True)
            if result["status"] == "error":
                print(f"::error title=Career feed failed::{feed.employer}: {result['error_type']}: {result['error']}", flush=True)
            elif result["status"] == "empty":
                print(f"::warning title=Career feed inconclusive::{feed.employer}: no matching jobs / detail verification incomplete", flush=True)
            return result

    return await asyncio.gather(*(limited(feed) for feed in feeds))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("integrated", "provisional", "all"), default="integrated")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fail-on-empty", action="store_true", help="Treat inconclusive zero-job scans as failure")
    args = parser.parse_args()
    if args.timeout <= 0 or args.concurrency <= 0:
        parser.error("timeout and concurrency must be positive")

    feeds = select_feeds(args.scope, shard_index=args.shard_index, shard_count=args.shard_count)
    results = asyncio.run(probe_shard(feeds, timeout=args.timeout, max_concurrency=args.concurrency))
    totals = dict(Counter(row["status"] for row in results))
    report = {
        "schema_version": 1,
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "total": len(feeds),
        "totals": totals,
        "interpretation": {
            "ok": "Live collector returned valid job details",
            "empty": "Site responded with no relevant jobs; full detail extraction remains unverified",
            "error": "Live HTTP, parsing, data contract, or deadline failure",
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Live feed results: {json.dumps(totals, sort_keys=True)}; JSON: {args.output}", flush=True)
    return int(bool(totals.get("error") or (args.fail_on_empty and totals.get("empty"))))


if __name__ == "__main__":
    raise SystemExit(main())
