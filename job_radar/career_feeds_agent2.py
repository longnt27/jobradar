"""First-party career collectors for the Agent 2 technology/VNPT cohort.

Every parser validates the expected board shape. An absent/malformed listing must
raise, rather than masquerade as a successful scan with no vacancies.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .ingest import ObservedJob


MANATAL_PATH = re.compile(r"/jobs/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})/?$", re.I)
TRAKSTAR_PATH = re.compile(r"/jobs/(fk[a-z0-9]+)/?$", re.I)
VNPT_TECH_PATH = re.compile(r"/vi/recruit_detail/(\d+)/?$")
VNPT_GROUP_PATH = re.compile(r"/tim-viec-lam/[^/?#]+-jid(\d+)\.html$")
VNPT_PARENT = re.compile(r"(?:tập đoàn\s+(?:bưu chính viễn thông việt nam|vnpt)|cơ quan tập đoàn)", re.I)


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        node.decompose()
    return soup


def _links(soup: BeautifulSoup, page_url: str, path_pattern: re.Pattern) -> dict[str, str]:
    host = urlsplit(page_url).hostname
    result: dict[str, str] = {}
    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, str(anchor.get("href") or "").strip())
        parts = urlsplit(href)
        if (parts.hostname or "").removeprefix("www.") == (host or "").removeprefix("www.") and path_pattern.fullmatch(parts.path):
            result[href.split("?")[0].split("#")[0]] = anchor.get_text(" ", strip=True)
    return result


def _section(text: str, start: str, *end_markers: str) -> str:
    match = re.search(start, text, re.I)
    if not match:
        return ""
    content = text[match.end():]
    for marker in end_markers:
        ending = re.search(marker, content, re.I)
        if ending:
            content = content[:ending.start()]
    return content.strip()


def _deadline_expired(text: str, label: str) -> bool:
    match = re.search(label + r"\s*:?\s*(\d{1,2})/(\d{1,2})/(20\d{2})", text, re.I)
    if not match:
        return False
    day, month, year = map(int, match.groups())
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).date() < datetime.now(timezone.utc).date()
    except ValueError as exc:
        raise RuntimeError("Invalid advertised career deadline") from exc


async def collect_manatal(source: dict) -> list[ObservedJob]:
    """Exhaust every page of Gear Inc's official Manatal board (not just the first 10)."""
    from .collectors import _application_destination, _career_client, _expired_posting, _structured_text, _target_title

    base = source["url"].split("?")[0].rstrip("/") + "/"
    postings: dict[str, str] = {}
    async with _career_client() as client:
        if hasattr(client, "headers"):
            client.headers["User-Agent"] = "JobRadar/0.1 (public recruitment feed health check)"
        pace = max(0.0, min(8.0, float(source.get("config", {}).get("pace_seconds", 0))))
        request_count = 0

        async def polite_get(url: str):
            nonlocal request_count
            if request_count and pace:
                await asyncio.sleep(pace)
            request_count += 1
            for attempt in range(3):
                response = await client.get(url)
                if response.status_code != 429:
                    return response
                if attempt == 2:
                    response.raise_for_status()
                # A published Retry-After overrides the bounded default
                # cooldown. Never ignore a rate limit and never forge a login.
                raw_delay = response.headers.get("Retry-After", "")
                try:
                    wait = max(2.0, float(raw_delay))
                except ValueError:
                    wait = 15.0 * (attempt + 1)
                if wait > 45.0:
                    response.raise_for_status()
                await asyncio.sleep(wait)
            raise AssertionError("unreachable")

        total = pages = None
        for page in range(1, int(source.get("config", {}).get("max_pages", 50)) + 1):
            url = base if page == 1 else f"{base}?page={page}"
            response = await polite_get(url)
            response.raise_for_status()
            soup = _soup(response.text)
            visible = soup.get_text(" ", strip=True)
            match = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", visible, re.I)
            count = re.search(r"([\d,]+)\s+Open\s+Positions?", visible, re.I)
            if not match or not count or int(match.group(1)) != page:
                raise RuntimeError("Manatal pagination/count markers changed")
            current_pages, current_total = int(match.group(2)), int(count.group(1).replace(",", ""))
            if total is None:
                total, pages = current_total, current_pages
            if (current_pages, current_total) != (pages, total):
                raise RuntimeError("Manatal listing changed during pagination")
            candidates = _links(soup, str(response.url), MANATAL_PATH)
            if not candidates and total:
                raise RuntimeError("Manatal page advertised vacancies but returned no job links")
            old = len(postings)
            postings.update(candidates)
            if len(postings) == old and total:
                raise RuntimeError("Manatal pagination repeated postings")
            if page == pages:
                break
        else:
            raise RuntimeError("Manatal exceeded the configured pagination limit")
        if len(postings) != total:
            raise RuntimeError(f"Manatal advertised {total} jobs, but only {len(postings)} distinct links were collected")

        jobs = []
        for url, label in postings.items():
            if label and not _target_title(label):
                continue
            response = await polite_get(url)
            if response.status_code in {404, 410}:
                continue
            response.raise_for_status()
            soup = _soup(response.text)
            title_node = soup.select_one("h1, h2, h3, h4")
            title = (title_node.get_text(" ", strip=True) if title_node else label).strip()
            if not _target_title(title):
                continue
            content = _structured_text(soup.body or soup)
            found = re.search(r"Job Description\s*:", content, re.I)
            if not found:
                raise RuntimeError(f"Manatal detail has no job description: {url}")
            lead = content[:found.start()]
            description = re.split(r"Powered by\s+Manatal", content[found.end():], maxsplit=1, flags=re.I)[0].strip()
            location = next((part.strip(" •\t") for part in lead.splitlines()
                             if re.search(r"\bVietnam\b", part, re.I) and len(part) < 130), "")
            if not location:
                # This board includes many other countries; do not mislabel them as Vietnam.
                continue
            if len(description) < 100:
                raise RuntimeError(f"Manatal detail omitted required qualifications: {url}")
            if _expired_posting(description):
                continue
            job_id = MANATAL_PATH.fullmatch(urlsplit(url).path).group(1)
            apply_url = url.rstrip("/") + "/apply"
            jobs.append(ObservedJob(
                url=url, external_id=job_id, title=title[:180],
                company=source.get("employer_name") or "Gear Inc",
                description=description[:30000], location=location[:250],
                apply_url=apply_url, raw_text=description[:30000],
            ))
        return jobs


async def collect_trakstar(source: dict) -> list[ObservedJob]:
    """Anduin's first-party-linked Trakstar board with full job requirements."""
    from .collectors import _career_client, _expired_posting, _structured_text, _target_title

    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        soup = _soup(response.text)
        listings = _links(soup, str(response.url), TRAKSTAR_PATH)
        if not listings:
            visible = soup.get_text(" ", strip=True)
            if re.search(r"(?:no (?:open )?jobs|no open positions|currently not hiring)", visible, re.I):
                return []
            raise RuntimeError("Anduin Trakstar board has no recognizable posting links")
        jobs = []
        for url, label in listings.items():
            if not _target_title(label):
                continue
            detail = await client.get(url)
            if detail.status_code in {404, 410}:
                continue
            detail.raise_for_status()
            # Trakstar sometimes puts the job heading inside <header>. Unlike
            # listing pages, stripping the detail header deletes the role title.
            soup = BeautifulSoup(detail.text, "html.parser")
            for element in soup(["script", "style", "noscript", "footer", "nav"]):
                element.decompose()
            metadata = soup.title.get_text(" ", strip=True) if soup.title else ""
            metadata = re.split(r"\s+at\s+Anduin Transactions\b", metadata, maxsplit=1, flags=re.I)[0]
            headings = [node.get_text(" ", strip=True) for node in soup.select("h1, h2")]
            titles = [text for text in [*headings, metadata] if _target_title(text)]
            listing_words = set(re.findall(r"\w+", label.casefold()))
            title = next((
                candidate for candidate in titles
                if len(set(re.findall(r"\w+", candidate.casefold())) & listing_words)
                   / max(1, len(set(re.findall(r"\w+", candidate.casefold())))) >= 0.75
            ), "")
            if not title:
                raise RuntimeError(f"Anduin detail title contradicts its advertised role: {url}")
            content = _structured_text(soup.body or soup)
            match = re.search(re.escape(title), content, re.I)
            # The metadata title need not also be duplicated verbatim in the
            # body. Validate the real heading, country and qualifications.
            lead = content[:600] if not match else content[match.end():match.end() + 350]
            location = re.search(r"(?:Ho Chi Minh City|Hanoi|Ha Noi|Hà Nội|Đà Nẵng|Da Nang)[^\n]{0,75}Vietnam", lead, re.I)
            if not location:
                continue  # Do not present US/remote jobs as Vietnam postings.
            description = content if not match else content[match.end():]
            description = re.split(r"Apply for this opening|Application Form", description, maxsplit=1, flags=re.I)[0].strip()
            if len(description) < 100 or "Job Qualifications" not in description:
                raise RuntimeError(f"Anduin detail omitted job requirements: {url}")
            if _expired_posting(description):
                continue
            job_id = TRAKSTAR_PATH.fullmatch(urlsplit(url).path).group(1)
            jobs.append(ObservedJob(
                url=url, external_id=job_id, title=title[:180],
                company=source.get("employer_name") or "Anduin Transactions",
                description=description[:30000], location=location.group(0)[:250],
                apply_url=url + ("&" if "?" in url else "?") + "apply=true",
                raw_text=description[:30000],
            ))
        return jobs


async def collect_vnpt_technology(source: dict) -> list[ObservedJob]:
    """VNPT Technology's own recruitment table and full-detail pages."""
    from .collectors import _application_destination, _career_client, _structured_text, _target_title

    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        soup = _soup(response.text)
        listings = _links(soup, str(response.url), VNPT_TECH_PATH)
        if not listings:
            if re.search(r"(?:không có vị trí tuyển dụng|chưa có vị trí tuyển dụng|no vacancies)", soup.get_text(" ", strip=True), re.I):
                return []
            raise RuntimeError("VNPT Technology recruitment table has no recognized job links")
        jobs = []
        for url, label in listings.items():
            if not _target_title(label):
                continue
            detail = await client.get(url)
            if detail.status_code in {404, 410}:
                continue
            detail.raise_for_status()
            item = _soup(detail.text)
            content = _structured_text(item.body or item)
            introduction = re.split(r"Mô tả công việc", content, maxsplit=1, flags=re.I)[0]
            if _deadline_expired(introduction, r"Hạn chót nhận hồ sơ"):
                continue
            description = _section(content, r"Mô tả công việc", r"Thông tin tuyển dụng khác", r"Về Chúng Tôi")
            if len(description) < 100:
                raise RuntimeError(f"VNPT Technology detail has no complete description: {url}")
            location_match = re.search(r"Nơi làm việc\s*:?\s*([^\n]+)", introduction, re.I)
            location = location_match.group(1).strip() if location_match else ""
            apply_url = _application_destination(item, url)
            job_id = VNPT_TECH_PATH.fullmatch(urlsplit(url).path).group(1)
            jobs.append(ObservedJob(
                url=url, external_id=job_id, title=label[:180],
                company=source.get("employer_name") or "VNPT Technology",
                description=description[:30000], location=location[:250],
                apply_url=apply_url, raw_text=description[:30000],
            ))
        return jobs


async def collect_vnpt_parent(source: dict) -> list[ObservedJob]:
    """VNPT's *parent-company* filter, never the unscoped subsidiary board."""
    from .collectors import _application_destination, _career_client, _structured_text, _target_title

    base = source["url"]
    links: dict[str, str] = {}
    async with _career_client() as client:
        for page in range(1, int(source.get("config", {}).get("max_pages", 12)) + 1):
            url = base if page == 1 else base.removesuffix(".html") + f"/p{page}.html"
            response = await client.get(url)
            if response.status_code == 404 and page > 1:
                break
            response.raise_for_status()
            soup = _soup(response.text)
            visible = soup.get_text(" ", strip=True)
            if page == 1 and "Không tìm thấy việc làm phù hợp" in visible:
                return []
            if page == 1 and not VNPT_PARENT.search(visible):
                raise RuntimeError("VNPT parent-company filter disappeared")
            batch = _links(soup, str(response.url), VNPT_GROUP_PATH)
            if page == 1 and not batch:
                raise RuntimeError("VNPT parent board is neither explicitly empty nor parseable")
            if not batch:
                break
            before = len(links)
            links.update(batch)
            if len(links) == before:
                break
        else:
            raise RuntimeError("VNPT parent feed reached pagination cap before an end marker")
        jobs = []
        for url, label in links.items():
            if label and not _target_title(label):
                continue
            response = await client.get(url)
            if response.status_code in {404, 410}:
                continue
            response.raise_for_status()
            item = _soup(response.text)
            h1 = item.find("h1")
            if not h1:
                raise RuntimeError(f"VNPT job detail has no job heading: {url}")
            title = h1.get_text(" ", strip=True)
            if not _target_title(title):
                continue
            # The hiring unit is displayed immediately under the job heading.
            following = [s.strip() for s in h1.next_strings if s.strip()]
            hiring_unit = following[0] if following else ""
            if not VNPT_PARENT.search(hiring_unit):
                continue  # A related-job link for a subsidiary is not a group posting.
            content = _structured_text(item.body or item)
            intro = re.split(r"Mô tả công việc", content, maxsplit=1, flags=re.I)[0]
            if _deadline_expired(intro, r"Hạn nộp"):
                continue
            description = _section(content, r"Mô tả công việc", r"Công việc liên quan")
            if len(description) < 100:
                raise RuntimeError(f"VNPT parent detail has no complete description: {url}")
            job_id = VNPT_GROUP_PATH.fullmatch(urlsplit(url).path).group(1)
            jobs.append(ObservedJob(
                url=url, external_id=job_id, title=title[:180],
                company=source.get("employer_name") or "VNPT Group",
                description=description[:30000],
                apply_url=_application_destination(item, url),
                raw_text=description[:30000],
            ))
        return jobs
