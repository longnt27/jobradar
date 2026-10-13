"""First-party Vietnamese bank career boards whose listings are not generic job cards.

Recruitment announcements are not themselves jobs. Parse explicit positions only;
never synthesize one vague 'recruitment notice' observation for multiple roles.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .ingest import ObservedJob


_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b")


def _parse_date(value: str) -> date | None:
    match = _DATE.search(value)
    if not match:
        return None
    try:
        return date(int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        return None


def _deadline(text: str) -> date | None:
    """Use an explicitly labelled submission deadline, not an article's date."""
    for match in re.finditer(
        r"(?:hạn nộp(?: hồ sơ)?|hạn chót(?: nhận hồ sơ)?|thời gian nhận hồ sơ|thời hạn)\s*[:：]?",
        text, re.I,
    ):
        # Includes 'from ... to ...' intervals: the final date is the deadline.
        dates = [_parse_date(found.group()) for found in _DATE.finditer(text[match.end():match.end() + 160])]
        valid = [value for value in dates if value]
        if valid:
            return valid[-1]
    return None


def _location(text: str) -> str:
    match = re.search(r"(?:địa điểm làm việc|nơi làm việc|địa điểm tuyển dụng)\s*[:：]\s*([^\n]{2,130})", text, re.I)
    return match[1].strip() if match else ""


def _role_name(value: str) -> str:
    value = re.sub(r"\s*[:：]\s*\d+\s*(?:người|nhân sự|chỉ tiêu|vị trí)\b.*$", "", value.strip(), flags=re.I)
    return re.sub(r"^\s*[-•\d.)]+\s*", "", value).strip()


def _notice_roles(soup: BeautifulSoup, kind: str) -> list[str]:
    """Extract role names only from explicit listing fields/sections."""
    from .collectors import _target_title

    candidates: list[str] = []
    if kind == "agribank":
        for row in soup.select("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True).casefold()
            if re.fullmatch(r"vị trí(?: tuyển dụng)?|chức danh(?: tuyển dụng)?", label):
                candidates.extend(re.split(r"\s*;\s*|\n+", cells[1].get_text("\n", strip=True)))
                break
    elif kind == "coopbank":
        for node in soup.find_all(["h2", "h3", "h4", "p", "strong"]):
            label = node.get_text(" ", strip=True)
            if len(label) < 90 and re.search(r"(?:^|\d+\.\s*)nội dung tuyển dụng\s*$", label, re.I):
                listing = node.find_next("ul")
                if listing:
                    candidates.extend(item.get_text(" ", strip=True) for item in listing.find_all("li", recursive=False))
                break

    # Individually titled VBSP/Co-opBank posts may be ordinary vacancies.
    if not candidates and kind != "agribank":
        heading = soup.select_one("h1")
        if heading:
            title = heading.get_text(" ", strip=True)
            if not re.search(r"^(?:thông báo|tuyển dụng|cơ hội việc làm)\b", title, re.I):
                candidates.append(title)

    roles = list(dict.fromkeys(_role_name(candidate) for candidate in candidates))
    return [role for role in roles if _target_title(role)]


async def collect_bank_notices(source: dict) -> list[ObservedJob]:
    """Collect explicit roles from first-party announcement listings.

    More than one technical role at the same URL is deliberately rejected:
    JobRadar currently deduplicates source observations by URL alone. A fabricated
    per-role query string would falsely advertise independently navigable jobs.
    """
    from .collectors import _career_client, _structured_text, _application_destination

    config = source["config"]
    pattern = re.compile(config["link_path"])
    root = source["url"]
    host = urlsplit(root).hostname
    max_pages = int(config.get("max_pages", 1))
    links: dict[str, str] = {}
    async with _career_client() as client:
        for page in range(1, max_pages + 1):
            listing_url = root if page == 1 else root.rstrip("/") + f"/page/{page}/"
            response = await client.get(listing_url)
            if response.status_code == 404 and page > 1:
                break
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            before = len(links)
            for anchor in soup.select("a[href]"):
                url = urljoin(str(response.url), str(anchor.get("href", "")))
                parts = urlsplit(url)
                if (parts.hostname or "").removeprefix("www.") != (host or "").removeprefix("www.") or not pattern.fullmatch(parts.path):
                    continue
                links.setdefault(url.split("#")[0], anchor.get_text(" ", strip=True))
            if len(links) == before:
                if page == 1:
                    marker = config.get("empty_marker", "")
                    if marker and marker.casefold() in soup.get_text(" ", strip=True).casefold():
                        return []
                    raise RuntimeError(f"{source['name']}: recruitment board returned no verifiable notice links")
                break

        jobs: list[ObservedJob] = []
        for url, _ in list(links.items())[:int(config.get("max_results", 90))]:
            response = await client.get(url)
            if response.status_code in {404, 410}:
                continue
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup(["script", "style", "nav", "header", "footer"]):
                node.decompose()
            body = (soup.select_one(".entry-content") or soup.select_one("article")
                    or soup.select_one("main") or soup.body)
            if body is None:
                raise RuntimeError(f"Recruitment notice has no readable content: {url}")
            description = _structured_text(body)
            deadline = _deadline(description)
            if deadline and deadline < datetime.now(timezone.utc).date():
                continue
            roles = _notice_roles(body, config["notice_kind"])
            if not roles:
                continue
            if len(roles) > 1:
                raise RuntimeError(
                    f"Multiple roles share a recruitment-notice URL; individual role URLs required: {url}"
                )
            if len(description) < 100:
                raise RuntimeError(f"Recruitment notice has incomplete requirements: {url}")
            title = roles[0]
            jobs.append(ObservedJob(
                url=url, external_id=urlsplit(url).path.rsplit("/", 1)[-1] + (":" + title),
                title=title, company=source.get("employer_name") or source["name"],
                description=description[:30000], raw_text=description[:30000],
                location=_location(description)[:250],
                apply_url=_application_destination(body, url),
            ))
    return jobs


async def collect_vikki_bank(source: dict) -> list[ObservedJob]:
    """Vikki's Avia/Enfold pagination uses avia-element-paging, not ?page=."""
    from .collectors import _career_client, _detail_description, _expired_posting, _target_title

    root = source["url"]
    host = urlsplit(root).hostname
    max_pages = int(source["config"].get("max_pages", 24))
    positions: dict[str, str] = {}
    pages = 1
    async with _career_client() as client:
        for page in range(1, max_pages + 1):
            listing_url = root if page == 1 else root.rstrip("/") + f"/?avia-element-paging={page}"
            response = await client.get(listing_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if page == 1:
                count = re.search(r"\bPage\s+1\s+of\s+(\d+)\b", soup.get_text(" ", strip=True), re.I)
                if count:
                    pages = int(count[1])
                else:
                    linked_pages = [
                        int(value) for value in re.findall(r"[?&]avia-element-paging=(\d+)", response.text)
                    ]
                    pages = max([1, *linked_pages])
                if pages > max_pages:
                    raise RuntimeError("Vikki listing has more pages than the configured safety bound")
            before = len(positions)
            for heading in soup.select("h3"):
                anchor = heading.select_one("a[href]")
                if not anchor:
                    continue
                url = urljoin(str(response.url), str(anchor["href"]))
                parts = urlsplit(url)
                if (parts.hostname != host or not re.fullmatch(r"/[a-z0-9-]+/?", parts.path)
                        or parts.path.rstrip("/") == urlsplit(root).path.rstrip("/")):
                    continue
                title = heading.get_text(" ", strip=True)
                if title:
                    positions.setdefault(url.split("#")[0].split("?")[0], title)
            if len(positions) == before:
                raise RuntimeError(f"Vikki career listing page {page} had no new job cards")
            if page == pages:
                break
        else:
            raise RuntimeError("Vikki career listing pagination was truncated")

        jobs: list[ObservedJob] = []
        for url, title in positions.items():
            if not _target_title(title):
                continue
            response = await client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup(["script", "style", "nav", "footer", "header"]):
                node.decompose()
            description = _detail_description(soup, ".entry-content, .entry-content-wrapper, article, main")
            if len(description) < 100:
                raise RuntimeError(f"Vikki career detail is missing requirements: {url}")
            if _expired_posting(description):
                continue
            # Vikki accepts applications via the form on the original job board.
            jobs.append(ObservedJob(
                url=url, external_id=urlsplit(url).path.strip("/"),
                title=title[:180], company=source.get("employer_name") or source["name"],
                description=description[:30000], raw_text=description[:30000],
                location=_location(description)[:250], apply_url=root,
            ))
    return jobs
