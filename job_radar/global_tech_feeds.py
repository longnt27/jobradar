"""First-party global technology careers: Eightfold and Vietnam-scoped Workday.

The Workday wrapper isolates Vietnam matching without changing the shared
collector used by NVIDIA and banks. Eightfold checks every listing page and
reads each relevant position's full detail API, rather than scraping a SPA shell.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .ingest import ObservedJob


VIETNAM_LOCATION = re.compile(
    r"\b(?:viet[ -]?nam|vi[eệ]t nam|hanoi|ha noi|hà nội|ha noi|"
    r"ho chi minh|hồ chí minh|hcmc|sai gon|saigon|da nang|đà nẵng|"
    r"hai phong|hải phòng|vietnamese)\b",
    re.I,
)


def _in_vietnam(location: str) -> bool:
    return bool(location and (VIETNAM_LOCATION.search(location) or location.strip().upper() == "VN"))


def _eightfold_location(record: dict) -> str:
    def render(value) -> str:
        if isinstance(value, list):
            return ", ".join(filter(None, (render(part) for part in value)))
        if isinstance(value, dict):
            name = value.get("name") or value.get("fullLocation")
            if name:
                return str(name)
            return ", ".join(str(value[key]) for key in ("city", "state", "country") if value.get(key))
        return str(value or "").strip()

    for key in ("standardizedLocations", "locations", "primaryLocation", "primary_location", "location"):
        value = render(record.get(key))
        if value:
            return value
    return ""


def _eightfold_date(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            timestamp = float(value)
            return datetime.fromtimestamp(timestamp / 1000 if timestamp > 1e11 else timestamp, tz=timezone.utc).isoformat()
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _eightfold_url(row: dict, base: str, identifier: str) -> str:
    uri = next((str(row.get(key)) for key in (
        "publicUrl", "canonicalPositionUrl", "canonical_position_url",
        "positionUrl", "position_url",
    ) if row.get(key)), f"/careers/job/{identifier}")
    url = urljoin(base, uri)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or (
        parts.hostname != urlsplit(base).hostname and parts.hostname != "www.qualcomm.com"
    ):
        raise RuntimeError("Eightfold posting has an invalid external URL")
    return url


async def collect_eightfold(source: dict) -> list[ObservedJob]:
    """Collect a first-party Eightfold PCSX/classic board, with full requisitions.

    PCSX's count is total jobs; classic's count is *not* a real total. A failed
    API/schema/pagination is never treated as an empty list of vacancies.
    """
    from .collectors import _career_client, _expired_posting, _structured_text, _target_title

    cfg = source.get("config", {})
    root_url = source["url"]
    root = f"{urlsplit(root_url).scheme}://{urlsplit(root_url).netloc}"
    domain = cfg["domain"]
    country = cfg.get("location_query", "Vietnam")
    max_pages = int(cfg.get("max_pages", 50))
    if max_pages < 1:
        raise ValueError("Eightfold max_pages must be positive")

    async with _career_client() as client:
        if hasattr(client, "headers"):
            # Identifiable read-only crawler UA. Browser impersonation caused
            # intermittent API throttling on GitHub-hosted runners.
            client.headers["User-Agent"] = "JobRadar/0.1 (public recruitment feed health check)"

        async def fetch(url: str, params: dict):
            for attempt in range(3):
                reply = await client.get(url, params=params)
                if reply.status_code != 429:
                    return reply
                if attempt == 2:
                    reply.raise_for_status()
                raw_delay = reply.headers.get("Retry-After", "")
                try:
                    delay = max(2.0, min(15.0, float(raw_delay)))
                except ValueError:
                    delay = 3.0 * (attempt + 1)
                await asyncio.sleep(delay)
            raise AssertionError("unreachable")

        generation = None
        seen: set[str] = set()
        records: list[dict] = []
        offset = 0
        for page in range(max_pages):
            if page and float(cfg.get("pace_seconds", 0)):
                await asyncio.sleep(min(5.0, float(cfg["pace_seconds"])))
            params = {"domain": domain, "query": "", "location": country, "start": str(offset)}
            if generation != "classic":
                pcsx = await fetch(root + "/api/pcsx/search", params=params)
                if pcsx.status_code == 403 and generation is None:
                    generation = "classic"
                else:
                    pcsx.raise_for_status()
                    payload = pcsx.json()
                    body = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(body, dict) or not isinstance(body.get("positions"), list) or not isinstance(body.get("count"), int):
                        raise RuntimeError("Eightfold PCSX returned an unexpected listing")
                    generation = "pcsx"
                    batch = body["positions"]
                    total = body["count"]

            if generation == "classic":
                classic = await fetch(root + "/api/apply/v2/jobs", params={
                    **params, "num": "10", "sort_by": "timestamp",
                })
                classic.raise_for_status()
                payload = classic.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("positions"), list):
                    raise RuntimeError("Eightfold classic returned an unexpected listing")
                batch = payload["positions"]
                total = None

            if not batch:
                if total is not None and offset < total:
                    raise RuntimeError("Eightfold listing ended before its advertised total")
                break

            before = len(seen)
            for row in batch:
                if not isinstance(row, dict):
                    raise RuntimeError("Eightfold listing contained a malformed position")
                job_id = row.get("id") or row.get("displayJobId") or row.get("display_job_id")
                if not job_id:
                    raise RuntimeError("Eightfold position lacks an identity")
                job_id = str(job_id)
                if job_id not in seen:
                    seen.add(job_id)
                    records.append(row)
            if len(seen) == before:
                raise RuntimeError("Eightfold repeated a listing page")
            offset += len(batch)
            if total is not None and offset >= total:
                break
            if total is None and len(batch) < 10:
                break
        else:
            raise RuntimeError("Eightfold pagination exceeded max_pages")

        jobs: list[ObservedJob] = []
        for row in records:
            title = str(row.get("name") or row.get("posting_name") or row.get("title") or "").strip()
            if not _target_title(title):
                continue
            location = _eightfold_location(row)
            if not _in_vietnam(location):
                continue
            identifier = str(row.get("id") or row.get("displayJobId") or row["display_job_id"])
            if generation == "pcsx":
                resp = await fetch(root + "/api/pcsx/position_details",
                                        params={"position_id": identifier, "domain": domain, "hl": "en"})
            else:
                resp = await fetch(root + f"/api/apply/v2/jobs/{identifier}", params={"domain": domain})
            if resp.status_code in {404, 410}:
                continue
            resp.raise_for_status()
            data = resp.json()
            if generation == "pcsx":
                data = data.get("data") if isinstance(data, dict) else None
                data = data.get("position", data) if isinstance(data, dict) else None
            if not isinstance(data, dict):
                raise RuntimeError("Eightfold returned an unexpected job detail")
            combined = {**row, **data}
            raw = combined.get("jobDescription") or combined.get("job_description")
            if not isinstance(raw, str):
                raise RuntimeError("Eightfold job detail omitted its description")
            description = _structured_text(BeautifulSoup(raw, "html.parser"))
            if len(description) < 100:
                raise RuntimeError("Eightfold job detail omitted its full requirements")
            if _expired_posting(description):
                continue
            detail_location = _eightfold_location(combined) or location
            if not _in_vietnam(detail_location):
                continue
            url = _eightfold_url(combined, root, identifier)
            posted = _eightfold_date(
                combined.get("postedTs") or combined.get("creationTs")
                or combined.get("t_create") or combined.get("t_update")
            )
            jobs.append(ObservedJob(
                url=url, external_id=identifier,
                title=str(combined.get("name") or combined.get("posting_name") or title)[:180],
                company=source.get("employer_name") or source["name"],
                description=description[:30000], location=detail_location[:250],
                apply_url=url, published_at=posted, raw_text=description[:30000],
            ))
    return jobs


async def collect_workday_vietnam(source: dict) -> list[ObservedJob]:
    """Search regional Workday variants and deduplicate *verified Vietnam* jobs.

    Country facet identifiers vary across Workday tenants. Independent search
    terms plus detail locations avoid silently trusting a global result list.
    """
    from .collectors import collect_workday

    cfg = source.get("config", {})
    terms = cfg.get("search_texts") or ["Vietnam", "Hanoi", "Ho Chi Minh"]
    result: dict[str, ObservedJob] = {}
    for term in terms:
        scoped = {**source, "config": {**cfg, "search_text": term}}
        postings = await collect_workday(scoped)
        for job in postings:
            if not _in_vietnam(job.location):
                continue
            key = str(job.external_id or job.url)
            result.setdefault(key, job)
    return list(result.values())
