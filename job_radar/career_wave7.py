"""First-party job-board adapters owned by sprint agent 7.

Agoda's public, employer-operated board contains worldwide vacancies. Its job
header supplies the posting's actual country, which must be checked rather than
mistaking words like "Vietnam" in a Bangkok-based role title for its location.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .ingest import ObservedJob


_AGODA_POSTING = re.compile(r"^/job/(?P<id>[0-9]+)-[^/?#]+/?$", re.I)
_COUNTRIES = re.compile(
    r"\b(?:Vietnam|Viet Nam|Việt Nam|Thailand|Singapore|Malaysia|Indonesia|"
    r"India|China|Japan|Taiwan|Philippines|Korea|Australia|Canada|"
    r"United States|United Kingdom|Egypt|Germany|Hong Kong|Cambodia|"
    r"Sri Lanka|United Arab Emirates|Nepal|Maldives|Turkey|Brazil)\b",
    re.I,
)
_VIETNAM = re.compile(r"\b(?:Vietnam|Viet Nam|Việt Nam)\b", re.I)
_EMPTY_BOARD = re.compile(r"\b(?:0\s+jobs?\s+available|no\s+(?:open\s+)?jobs?\s+(?:available|found))\b", re.I)
_ADVERTISED_COUNT = re.compile(r"\b(\d+)\s+jobs?\s+available\b", re.I)


def _agoda_posting_url(url: str) -> re.Match[str] | None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {"careersatagoda.com", "www.careersatagoda.com"}:
        return None
    return _AGODA_POSTING.fullmatch(parts.path)


def _agoda_location(soup: Tag) -> str:
    """Read *job-specific* location metadata; never inspect the global filter.

    Both listing cards and job pages display an image with 'Location' in alt.
    Its own short wrapper is the only acceptable source for country scoping.
    """
    for image in soup.select("img[alt]"):
        if not re.search(r"location", str(image.get("alt", "")), re.I):
            continue
        parent = image.parent
        for _ in range(3):
            if not isinstance(parent, Tag):
                break
            value = parent.get_text(" ", strip=True)
            if 2 <= len(value) <= 130 and _COUNTRIES.search(value):
                return value
            parent = parent.parent
    for node in soup.select("[class*=job-location], [class*=job_location], [data-test*=job-location]"):
        value = node.get_text(" ", strip=True)
        if 2 <= len(value) <= 130 and _COUNTRIES.search(value):
            return value
    return ""


def _agoda_listing_location(anchor: Tag) -> str:
    """Find the card enclosing exactly one job, not the entire jobs grid."""
    candidate: Tag | None = None
    for parent in anchor.parents:
        if not isinstance(parent, Tag) or parent.name in {"body", "html"}:
            break
        posting_links = sum(
            1 for link in parent.select("a[href]")
            if _agoda_posting_url(urljoin("https://careersatagoda.com", str(link.get("href"))))
        )
        if posting_links > 1:
            break
        if posting_links == 1:
            candidate = parent
        if len(parent.get_text(" ", strip=True)) > 600:
            break
    return _agoda_location(candidate) if candidate else ""


async def collect_agoda(source: dict) -> list[ObservedJob]:
    """Collect every relevant Agoda vacancy physically based in Vietnam.

    The official listing is rendered server-side and publishes individual job
    links, location metadata, and full first-party detail/application pages.
    Fail visibly if the listing is incomplete or the detail location vanishes.
    """
    from .collectors import _career_client, _detail_description, _expired_posting, _target_title

    employer = source.get("employer_name") or source["name"]
    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links: dict[str, tuple[str, str, str]] = {}

        for anchor in soup.select("a[href]"):
            url = urljoin(str(response.url), str(anchor.get("href", ""))).split("#")[0].split("?")[0]
            match = _agoda_posting_url(url)
            if not match:
                continue
            title = anchor.get_text(" ", strip=True)
            if not title:
                # A link without a readable role title is a parsing failure,
                # not evidence that the board has no relevant positions.
                raise RuntimeError(f"Agoda listing has an untitled posting: {url}")
            location = _agoda_listing_location(anchor)
            links[url] = (match.group("id"), title, location)

        board_text = soup.get_text(" ", strip=True)
        advertised = _ADVERTISED_COUNT.search(board_text)
        if not links:
            if _EMPTY_BOARD.search(board_text):
                return []
            raise RuntimeError("Agoda listings could not be parsed (site changed or loading failed)")
        if advertised and len(links) < int(advertised.group(1)):
            raise RuntimeError(
                f"Agoda listings incomplete: {len(links)} links of {advertised.group(1)} advertised; "
                "the load-more/pagination mechanism must be checked"
            )

        jobs: list[ObservedJob] = []
        for url, (job_id, title, listed_location) in links.items():
            if not _target_title(title):
                continue
            if listed_location and not _VIETNAM.search(listed_location):
                continue
            detail = await client.get(url)
            if detail.status_code in (404, 410):
                continue  # Listing changed while fetching.
            detail.raise_for_status()
            if not _agoda_posting_url(str(detail.url)):
                raise RuntimeError(f"Agoda job redirected away from its posting: {url}")
            item = BeautifulSoup(detail.text, "html.parser")
            location = _agoda_location(item)
            if not location:
                raise RuntimeError(f"Agoda job location missing, cannot scope employer feed: {url}")
            if listed_location and _VIETNAM.search(listed_location) != _VIETNAM.search(location):
                raise RuntimeError(f"Agoda listing/detail country disagree: {url}")
            if not _VIETNAM.search(location):
                continue
            description = _detail_description(item, "main, article, [class*=job-description]")
            if len(description) < 120 or not re.search(
                r"requirements?|qualifications?|responsibilit|what you will|about the role", description, re.I
            ):
                raise RuntimeError(f"Agoda job detail lacks verifiable responsibilities/requirements: {url}")
            if _expired_posting(description):
                continue
            jobs.append(ObservedJob(
                url=str(detail.url).split("?")[0], external_id=job_id,
                title=title[:180], company=employer, description=description[:30000],
                location=location[:250], apply_url=str(detail.url).split("?")[0],
                raw_text=description[:30000],
            ))
        return jobs
