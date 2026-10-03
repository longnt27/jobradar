from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.async_api import BrowserContext, Page, async_playwright

from .ingest import ObservedJob
from .settings import Settings


class AuthRequired(RuntimeError):
    pass


def _text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _structured_text(node: Tag) -> str:
    """Keep the paragraphs and lists that give a posting its meaning."""
    def walk(part: Tag | NavigableString) -> str:
        if isinstance(part, NavigableString):
            return re.sub(r"\s+", " ", str(part).replace("\xa0", " "))
        if part.name in {"script", "style", "noscript", "svg"}:
            return ""
        if part.name == "br":
            return "\n"
        contents = "".join(walk(child) for child in part.children)
        if part.name == "li":
            return f"\n• {contents.strip()}"
        if part.name in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "blockquote"}:
            return f"\n\n{contents.strip()}\n\n"
        if part.name in {"div", "section", "article", "main"}:
            return f"\n{contents.strip()}\n"
        return contents

    value = walk(node)
    value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r" {2,}", " ", value)
    return value.strip()


def _meta(soup: BeautifulSoup, key: str) -> str:
    node = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return str(node.get("content", "")) if node else ""


def _date_from_age(text: str) -> str | None:
    match = re.search(r"(\d+)\s*(minute|hour|day|week|month|phút|giờ|ngày|tuần|tháng)s?\s*(?:ago|trước)?", text.casefold())
    if not match:
        return None
    number = int(match.group(1))
    unit = match.group(2)
    minutes = number if unit in ("minute", "phút") else number * 60 if unit in ("hour", "giờ") else number * 1440 if unit in ("day", "ngày") else number * 10080 if unit in ("week", "tuần") else number * 43200
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


async def _page_text(page: Page, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count():
                value = (await locator.inner_text(timeout=2500)).strip()
                if value:
                    return value
        except Exception:
            continue
    return ""


def _check_auth(url: str, body: str) -> None:
    value = f"{url} {body[:1200]}".casefold()
    if any(term in value for term in ("/login", "/checkpoint", "security verification", "verify your identity", "log in to see", "đăng nhập để")):
        raise AuthRequired("Login or verification is required in the browser profile")


async def collect_linkedin(context: BrowserContext, source: dict) -> list[ObservedJob]:
    page = await context.new_page()
    detail = await context.new_page()
    try:
        await page.goto(source["url"], wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1400)
        _check_auth(page.url, await page.locator("body").inner_text(timeout=5000))
        urls = await page.locator('a[href*="/jobs/view/"]').evaluate_all(
            "links => [...new Set(links.map(a => a.href.split('?')[0]))]"
        )
        max_results = int(source["config"].get("max_results", 40))
        if not urls and "jobs/search" not in page.url:
            raise RuntimeError(f"Unexpected LinkedIn page: {page.url}")
        jobs: list[ObservedJob] = []
        for url in urls[:max_results]:
            try:
                await detail.goto(url, wait_until="domcontentloaded", timeout=30000)
                body = await detail.locator("body").inner_text(timeout=5000)
                _check_auth(detail.url, body)
                title = await _page_text(detail, ("h1", ".top-card-layout__title", ".job-details-jobs-unified-top-card__job-title"))
                company = await _page_text(detail, (".topcard__org-name-link", ".top-card-layout__card a", ".job-details-jobs-unified-top-card__company-name"))
                description = await _page_text(detail, (".jobs-description-content__text", ".jobs-description__content", ".show-more-less-html__markup", "article"))
                location = await _page_text(detail, (".topcard__flavor--bullet", ".top-card-layout__second-subline", ".job-details-jobs-unified-top-card__primary-description-container"))
                if not description:
                    soup = BeautifulSoup(await detail.content(), "html.parser")
                    description = _meta(soup, "og:description") or body[:12000]
                if not title:
                    soup = BeautifulSoup(await detail.content(), "html.parser")
                    title = _meta(soup, "og:title").split(" | ")[0]
                if not title or len(description) < 30:
                    continue
                job_id = re.search(r"/jobs/view/(\d+)", url)
                apply_links = await detail.locator("a[href]").evaluate_all(r"""links => links
                  .filter(a => /apply|ứng tuyển/i.test(`${a.innerText} ${a.href}`))
                  .map(a => a.href)
                  .filter(h => /^https?:\/\//i.test(h) && !/linkedin\.com/i.test(h))""")
                jobs.append(ObservedJob(
                    url=url, external_id=job_id.group(1) if job_id else None,
                    title=title, company=company or "Unknown employer", description=description[:30000],
                    location=location[:250], apply_url=apply_links[0] if apply_links else None,
                    published_at=_date_from_age(body[:1800]), raw_text=body[:30000],
                ))
            except AuthRequired:
                raise
            except Exception:
                continue
        return jobs
    finally:
        await detail.close()
        await page.close()


RECRUITING = re.compile(r"\b(hiring|recruit|vacancy|apply|tuyển dụng|tuyển|cần tìm|cần tuyển|job opening|we are looking)\b", re.I)
ROLE = re.compile(r"\b(ai|ml|machine learning|engineer|developer|research|data scientist|llm|computer vision|kỹ sư|lập trình|trí tuệ nhân tạo)\b", re.I)


async def collect_facebook(context: BrowserContext, source: dict) -> list[ObservedJob]:
    page = await context.new_page()
    try:
        await page.goto(source["url"], wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1600)
        body = await page.locator("body").inner_text(timeout=7000)
        _check_auth(page.url, body)
        max_posts = int(source["config"].get("max_posts", 50))
        for _ in range(min(5, max_posts // 10)):
            await page.mouse.wheel(0, 1400)
            await page.wait_for_timeout(500)
        posts = await page.locator('[role="article"]').evaluate_all("""nodes => nodes.map(node => ({
            text: node.innerText || '',
            url: [...node.querySelectorAll('a[href]')].map(a => a.href).find(h => /\\/groups\\/[^/]+\\/(posts|permalink)\\//.test(h)) || '',
            links: [...node.querySelectorAll('a[href]')].map(a => a.href).filter(h => h.startsWith('http'))
        }))""")
        if not posts:
            raise RuntimeError("No group posts were visible; check group access or sign-in")
        jobs: list[ObservedJob] = []
        for post in posts[:max_posts]:
            text = post["text"].strip()
            if len(text) < 40 or not RECRUITING.search(text) or not ROLE.search(text):
                continue
            url = post["url"]
            if not url:
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = next((line for line in lines[:8] if ROLE.search(line) and len(line) < 160), lines[0][:120])
            company_match = re.search(r"^(?:company|employer|công ty|đơn vị)\s*[:：-]\s*(.{3,100})$", text[:1000], re.I | re.M)
            company = company_match.group(1).strip() if company_match else "Facebook post"
            external = next((link for link in post["links"] if "facebook.com" not in link), None)
            jobs.append(ObservedJob(
                url=url, title=title[:180], company=company, description=text[:30000],
                apply_url=external, raw_text=text[:30000],
            ))
        return jobs
    finally:
        await page.close()


JOB_LINK = re.compile(r"(job|career|position|opening|vacan|recruit|tuyen-dung|viec-lam|apply)", re.I)
NON_TARGET_TITLE = re.compile(r"\b(business development|sales|marketing|recruiter|human resources|account manager)\b", re.I)
GENERIC_CAREER_TITLE = re.compile(r"^(career(?:s)?|jobs?|job openings?|current openings?|open positions?|join us|apply now|view jobs?|internships?|let.s create the future together!?|what you.ll do|what you.ll need|nice to have.s?)$", re.I)
GENERIC_CAREER_PATH = {"career", "careers", "jobs", "job", "positions", "openings", "join-us", "recruitment", "apply"}


def _specific_posting_url(url: str, source_url: str) -> bool:
    parts = urlsplit(url)
    source = urlsplit(source_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    path = parts.path.rstrip("/").casefold()
    return bool(path and path.split("/")[-1] not in GENERIC_CAREER_PATH and
                (parts.hostname, path) != (source.hostname, source.path.rstrip("/").casefold()))


def _posting_title(item: BeautifulSoup, label: str, company: str) -> str:
    metadata = _meta(item, "og:title") or (item.title.get_text(" ", strip=True) if item.title else "")
    if label and label.casefold() in metadata.casefold() and not GENERIC_CAREER_TITLE.fullmatch(label.strip()):
        return label.strip()
    for separator in (" | ", " - ", " — ", " – "):
        if metadata.casefold().endswith((separator + company).casefold()):
            metadata = metadata[:-(len(separator) + len(company))]
            break
    title = metadata.strip() or _text(item, ("h1", "h2"))
    return title if not GENERIC_CAREER_TITLE.fullmatch(title) else ""


def _posting_description(item: BeautifulSoup) -> str:
    for selector in ("[class*=careers_detail_contents]", "[class*=job-description]", "[class*=job_description]",
                     "[data-test*=job-description]", "article", "main", "[class*=description]"):
        node = item.select_one(selector)
        if node:
            text = _structured_text(node)
            if text:
                return text
    return ""


async def collect_career(source: dict) -> list[ObservedJob]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers={"User-Agent": "JobRadar/0.1 personal job discovery"}) as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        company = source.get("employer_name") or source["name"]
        links: dict[str, str] = {}
        root_host = urlsplit(str(response.url)).hostname
        for anchor in soup.select("a[href]"):
            href = urljoin(str(response.url), anchor.get("href", ""))
            label = anchor.get_text(" ", strip=True)
            if not href.startswith("http") or not JOB_LINK.search(f"{href} {label}") or not _specific_posting_url(href, str(response.url)):
                continue
            if urlsplit(href).hostname != root_host and not any(host in href for host in ("greenhouse.io", "lever.co", "ashbyhq.com", "workdayjobs.com", "smartrecruiters.com")):
                continue
            if href.rstrip("/") != str(response.url).rstrip("/"):
                links[href.split("#")[0]] = label
        max_results = int(source["config"].get("max_results", 40))
        jobs: list[ObservedJob] = []
        for url, label in list(links.items())[:max_results]:
            try:
                detail = await client.get(url)
                detail.raise_for_status()
                if not _specific_posting_url(str(detail.url), str(response.url)):
                    continue
                item = BeautifulSoup(detail.text, "html.parser")
                for tag in item(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                title = _posting_title(item, label, company)
                description = _posting_description(item)
                if (not title or len(description) < 100 or
                        NON_TARGET_TITLE.search(title) or
                        not ROLE.search(f"{title} {description[:1000]}")):
                    continue
                date_node = item.select_one("time[datetime]")
                published = date_node.get("datetime") if date_node else None
                location = _text(item, ("[class*=location]", "[data-test*=location]"))[:250]
                jobs.append(ObservedJob(
                    url=str(detail.url), title=title[:180], company=company,
                    description=description[:30000], location=location,
                    published_at=published,
                ))
            except Exception:
                continue
        return jobs


async def collect_source(settings: Settings, source: dict) -> list[ObservedJob]:
    if source["kind"] == "career":
        return await collect_career(source)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.browser_profile), headless=True, viewport={"width": 1365, "height": 900},
        )
        try:
            if source["kind"] == "linkedin":
                return await collect_linkedin(context, source)
            return await collect_facebook(context, source)
        finally:
            await context.close()


async def login_browser(settings: Settings) -> None:
    settings.ensure_dirs()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.browser_profile), headless=False, viewport={"width": 1365, "height": 900},
        )
        try:
            await context.new_page()
            print("Sign in to LinkedIn and Facebook in the opened browser. Press Enter here when done.")
            await asyncio.to_thread(input)
        finally:
            await context.close()
