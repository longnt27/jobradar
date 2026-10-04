from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit, urlencode

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag
from playwright.async_api import BrowserContext, Page, async_playwright

from .ingest import ObservedJob
from .settings import Settings
from .social_browser import chrome_context_options
from .facebook_groups import clean_group_title


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
ROLE = re.compile(r"\b(ai|ml|machine learning|engineer|engineering|developer|devops|research|data scientist|data analyst|data analytics|data architect|business analyst|llm|computer vision|software|architect|fullstack|backend|frontend|technical lead|tech lead|cloud|security|cyber|database|network|kỹ sư|lập trình|trí tuệ nhân tạo|công nghệ thông tin|khoa học dữ liệu|phần mềm|phầm mềm|an ninh|bảo mật|quản trị ứng dụng|cơ sở dữ liệu|phân tích nghiệp vụ|chuyển đổi số|kiểm thử)\b", re.I)


async def collect_facebook(context: BrowserContext, source: dict) -> list[ObservedJob]:
    page = await context.new_page()
    try:
        await page.goto(source["url"], wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1600)
        body = await page.locator("body").inner_text(timeout=7000)
        _check_auth(page.url, body)
        titles = await page.evaluate("""() => [
            document.querySelector('meta[property="og:title"]')?.content || '',
            document.querySelector('h1')?.innerText || '',
            document.title || ''
        ]""")
        if name := next((cleaned for title in titles if (cleaned := clean_group_title(title))), None):
            source["resolved_name"] = name
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


def _application_destination(soup: BeautifulSoup, posting_url: str) -> str:
    for anchor in soup.select("a[href]"):
        label = anchor.get_text(" ", strip=True)
        if not re.search(r"\b(apply|application|ứng tuyển|nộp hồ sơ|submit cv)\b", label, re.I):
            continue
        href = urljoin(posting_url, str(anchor.get("href", "")).strip())
        parts = urlsplit(href)
        if parts.scheme in ("http", "https") and parts.hostname:
            return href
        if parts.scheme == "mailto" and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", parts.path):
            return f"mailto:{parts.path}"
    text = soup.get_text(" ", strip=True)
    match = re.search(r"(?:apply|application|send (?:your )?(?:cv|resume)|ứng tuyển|gửi (?:cv|hồ sơ))[^\n]{0,120}?\b([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", text, re.I)
    if match:
        return f"mailto:{match.group(1)}"
    return posting_url


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
                    apply_url=_application_destination(item, str(detail.url)),
                    published_at=published,
                ))
            except Exception:
                continue
        return jobs


def _career_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True, timeout=30,
        limits=httpx.Limits(max_connections=6),
        headers={"User-Agent": "Mozilla/5.0 (compatible; JobRadar/0.1; personal job discovery)"},
    )


def _target_title(title: str) -> bool:
    return bool(title and len(title) <= 180 and "\n" not in title and ROLE.search(title) and not NON_TARGET_TITLE.search(title)
                and not GENERIC_CAREER_TITLE.fullmatch(title.strip()))


def _expired_posting(text: str) -> bool:
    """Skip a posting only when its own page shows an explicit past deadline."""
    label = re.search(r"(?:Application deadline|Hạn nộp hồ sơ|Hạn nộp|Thời gian ứng tuyển|Deadline)\s*:?", text[:3000], re.I)
    if not label:
        return False
    dates = re.findall(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text[label.end():label.end() + 65])
    if not dates:
        return False
    day, month, year = dates[-1]
    try:
        return datetime(int(year), int(month), int(day)).date() < datetime.now(timezone.utc).date()
    except ValueError:
        return False


def _detail_description(soup: BeautifulSoup, preferred: str = "") -> str:
    selectors = [part.strip() for part in preferred.split(",") if part.strip()]
    selectors.extend((".jobdescription", ".job_description_content", ".content-article", ".article", "[class*=job-content]", "article", "main"))
    for selector in selectors:
        for node in soup.select(selector):
            value = _structured_text(node)
            if len(value) >= 100:
                return value
    return ""


def _detail_title(soup: BeautifulSoup, label: str) -> str:
    headings = [node.get_text(" ", strip=True) for node in soup.select("h1")]
    title = next((value for value in headings if _target_title(value)), "")
    if title:
        return title
    headings = [node.get_text(" ", strip=True) for node in soup.select("h2")]
    title = next((value for value in headings if _target_title(value)), "")
    if title:
        return title
    if _target_title(label):
        return label
    metadata = _meta(soup, "og:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    for separator in (" | ", " - ", " — ", " – "):
        metadata = metadata.split(separator)[0]
    return metadata.strip() if _target_title(metadata.strip()) else ""


async def collect_html_board(source: dict) -> list[ObservedJob]:
    config = source["config"]
    pattern = re.compile(config["link_path"])
    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        root_host = urlsplit(str(response.url)).hostname
        allowed_hosts = {root_host, *config.get("allowed_hosts", [])}
        links: dict[str, str] = {}
        for page in range(min(int(config.get("max_pages", 1)), 12)):
            if page:
                if config.get("pagination") == "wordpress":
                    listing_url = urljoin(source["url"].rstrip("/") + "/", f"page/{page + 1}/")
                elif config.get("pagination") == "vnpt":
                    listing_url = source["url"].removesuffix(".html") + f"/p{page + 1}.html"
                else:
                    parts = urlsplit(source["url"])
                    query = dict(parse_qsl(parts.query))
                    query["page"] = str(page + 1)
                    listing_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
                response = await client.get(listing_url)
                if response.status_code == 404:
                    break
                response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            before = len(links)
            for anchor in soup.select("a[href]"):
                url = urljoin(str(response.url), anchor.get("href", "").strip())
                parts = urlsplit(url)
                if parts.hostname not in allowed_hosts or not pattern.fullmatch(parts.path):
                    continue
                label = anchor.get_text(" ", strip=True)
                if not label or len(label) > 180 or label.casefold() in {"apply", "apply now", "ứng tuyển", "ứng tuyển ngay", "learn more", "xem chi tiết"}:
                    label = ""
                clean_url = url.split("#")[0]
                if not config.get("keep_query"):
                    clean_url = clean_url.split("?")[0]
                if clean_url not in links or (label and _target_title(label)):
                    links[clean_url] = label
            if len(links) == before:
                break
        jobs: list[ObservedJob] = []
        for url, label in list(links.items())[:int(config.get("max_results", 80))]:
            if label and not _target_title(label):
                continue
            try:
                detail = await client.get(url)
                detail.raise_for_status()
                item = BeautifulSoup(detail.text, "html.parser")
                title = _detail_title(item, label)
                if not _target_title(title):
                    continue
                for tag in item(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                description = _detail_description(item, config.get("description_selector", ""))
                if len(description) < 100 or _expired_posting(item.get_text(" ", strip=True)):
                    continue
                location = _text(item, ("[class*=job-location]", "[class*=location]", "[data-test*=location]"))[:250]
                jobs.append(ObservedJob(
                    url=str(detail.url) if config.get("keep_query") else str(detail.url).split("?")[0], title=title[:180],
                    company=source.get("employer_name") or source["name"],
                    description=description[:30000], location=location,
                    apply_url=_application_destination(item, str(detail.url)),
                    raw_text=description[:30000],
                ))
            except httpx.HTTPError:
                continue
        return jobs


async def collect_browser_board(source: dict) -> list[ObservedJob]:
    """Read a company-owned board that renders its listings in the browser."""
    config = source["config"]
    pattern = re.compile(config["link_path"])
    links: dict[str, str] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            detail = await browser.new_page()
            await page.goto(source["url"], wait_until="domcontentloaded", timeout=45000)
            listing_selector = config.get("listing_selector", "a[href*='/jobs/']")
            await page.locator(config.get("listing_ready_selector", listing_selector)).first.wait_for(state="attached", timeout=20000)
            for index in range(min(int(config.get("max_pages", 1)), 20)):
                found = await page.locator(listing_selector).evaluate_all(
                    "elements => elements.map(a => ({url: a.href, label: (a.innerText || a.getAttribute('title') || '').split('\\n')[0].trim()}))"
                )
                before = len(links)
                for item in found:
                    url = item["url"].split("?")[0].split("#")[0]
                    if urlsplit(url).hostname == urlsplit(source["url"]).hostname and pattern.fullmatch(urlsplit(url).path):
                        links[url] = item["label"]
                next_button = page.locator(config.get("next_selector", ".ant-pagination-next:not(.ant-pagination-disabled)"))
                if index + 1 >= int(config.get("max_pages", 1)) or not await next_button.count() or len(links) == before:
                    break
                first_url = found[0]["url"] if found else ""
                await next_button.first.click()
                try:
                    await page.wait_for_function(
                        "([selector, previous]) => !!document.querySelector(selector)?.href && document.querySelector(selector).href !== previous",
                        arg=[listing_selector, first_url], timeout=10000,
                    )
                except Exception:
                    break
            jobs = []
            for url, label in list(links.items())[:int(config.get("max_results", 120))]:
                if label and not _target_title(label):
                    continue
                try:
                    await detail.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if config.get("detail_ready_min_chars"):
                        await detail.wait_for_function(
                            "minimum => (document.querySelector('main')?.innerText.length || 0) >= minimum",
                            arg=int(config["detail_ready_min_chars"]), timeout=12000,
                        )
                    else:
                        await detail.locator("h1").first.wait_for(state="attached", timeout=12000)
                    soup = BeautifulSoup(await detail.content(), "html.parser")
                    title = _detail_title(soup, label)
                    if not _target_title(title):
                        continue
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    description = _detail_description(soup, config.get("description_selector", "main"))
                    if len(description) < 100 or _expired_posting(description):
                        continue
                    jobs.append(ObservedJob(
                        url=detail.url, title=title[:180],
                        company=source.get("employer_name") or source["name"],
                        description=description[:30000], apply_url=_application_destination(soup, detail.url),
                        raw_text=description[:30000],
                    ))
                except Exception:
                    continue
            return jobs
        finally:
            await browser.close()


async def collect_bidv(source: dict) -> list[ObservedJob]:
    """BIDV's public career API supplies full descriptions and live deadlines."""
    async with _career_client() as client:
        response = await client.get("https://tuyendung.bidv.com.vn/GetAllTinTuyenDung")
        response.raise_for_status()
        rows = response.json().get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("BIDV career API returned an unexpected response")
    jobs = []
    for item in rows:
        title = str(item.get("title") or "").strip()
        identifier = str(item.get("id") or "")
        if not _target_title(title) or not identifier.isdigit():
            continue
        try:
            deadline = datetime.strptime(str(item.get("enddateapply")), "%d/%m/%Y").date()
            if deadline < datetime.now(timezone.utc).date():
                continue
        except ValueError:
            continue
        description = _structured_text(BeautifulSoup(item.get("descriptionjob") or "", "html.parser"))
        if len(description) < 100:
            continue
        jobs.append(ObservedJob(
            url=f"https://tuyendung.bidv.com.vn/tin-tuyen-dung/{identifier}/bidv.html",
            external_id=identifier, title=title[:180],
            company=source.get("employer_name") or "BIDV",
            description=description[:30000], raw_text=description[:30000],
        ))
    return jobs


async def collect_vietinbank(source: dict) -> list[ObservedJob]:
    """The VietinBank board puts titles and deadlines outside its Apply links."""
    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        board = BeautifulSoup(response.text, "html.parser")
        links = {}
        for card in board.select(".c-card"):
            anchor = card.select_one('a[href*="/thong-tin-tuyen-dung/"]')
            title_node = card.select_one(".c-line-jobtitle")
            if not anchor or not title_node:
                continue
            title = title_node.get_text(" ", strip=True)
            if not _target_title(title):
                continue
            deadline_node = card.select_one(".c-line-date-expired .date-expired")
            if deadline_node:
                try:
                    deadline = datetime.strptime(deadline_node.get_text(" ", strip=True), "%d/%m/%Y").date()
                    if deadline < datetime.now(timezone.utc).date():
                        continue
                except ValueError:
                    continue
            links[urljoin(str(response.url), anchor["href"])] = title
        jobs = []
        for url, title in links.items():
            try:
                detail = await client.get(url)
                detail.raise_for_status()
                soup = BeautifulSoup(detail.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                description = _detail_description(soup, ".job_jd")
                if len(description) < 100:
                    continue
                jobs.append(ObservedJob(
                    url=str(detail.url), title=title[:180],
                    company=source.get("employer_name") or "VietinBank",
                    description=description[:30000], apply_url=_application_destination(soup, str(detail.url)),
                    raw_text=description[:30000],
                ))
            except httpx.HTTPError:
                continue
        return jobs


async def collect_successfactors(source: dict) -> list[ObservedJob]:
    config = source["config"]
    boards = config.get("boards") or [source["url"]]
    if config.get("queries"):
        boards = [source["url"].split("?")[0] + "?" + urlencode({"q": query}) for query in config["queries"]]
    links: dict[str, str] = {}
    async with _career_client() as client:
        for board in boards:
            for page in range(min(int(config.get("max_pages", 1)), 8)):
                listing_url = urljoin(board, f"{page * 10}/") if "/go/" in board and page else board
                if page and "/go/" not in board:
                    break
                response = await client.get(listing_url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                found = 0
                for anchor in soup.select('a.jobTitle-link[href*="/job/"]'):
                    title = anchor.get_text(" ", strip=True)
                    if not _target_title(title):
                        continue
                    url = urljoin(str(response.url), anchor.get("href", ""))
                    links[url] = title
                    found += 1
                if not soup.select('a.jobTitle-link[href*="/job/"]'):
                    break
                if page and found == 0:
                    break
        jobs: list[ObservedJob] = []
        for url, label in list(links.items())[:120]:
            try:
                response = await client.get(url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                title = _detail_title(soup, label)
                description = _detail_description(soup, ".jobdescription")
                if not _target_title(title) or len(description) < 100:
                    continue
                jobs.append(ObservedJob(
                    url=str(response.url), title=title[:180],
                    company=source.get("employer_name") or source["name"],
                    description=description[:30000],
                    location=_text(soup, (".job-location", "[class*=location]"))[:250],
                    apply_url=_application_destination(soup, str(response.url)),
                    raw_text=description[:30000],
                ))
            except httpx.HTTPError:
                continue
        return jobs


async def collect_vindynamics(source: dict) -> list[ObservedJob]:
    payload = {
        "categoryGroupId": "019fcb1c7a097f82bc0497c05ad10a75", "keyword": "",
        "pageIndex": 0, "pageSize": 100, "languageId": "00000P",
        "includeContent": True, "attributeFilters": [],
    }
    async with _career_client() as client:
        response = await client.post("https://vindynamics.net/api/Articles/Gets", json=payload)
        response.raise_for_status()
        data = response.json()
    if not data.get("status") or "articles" not in data.get("data", {}):
        raise RuntimeError("VinDynamics career API returned an unexpected response")
    jobs = []
    for item in data["data"]["articles"]:
        title = str(item.get("title", "")).strip()
        path = str(item.get("detailUrl", ""))
        if not _target_title(title) or not path.startswith("/career/"):
            continue
        description = _structured_text(BeautifulSoup(item.get("content") or "", "html.parser"))
        if len(description) < 100:
            continue
        location = item.get("attrs", {}).get("location", {})
        jobs.append(ObservedJob(
            url=urljoin(source["url"], path), external_id=str(item.get("id") or ""),
            title=title[:180], company=source.get("employer_name") or "VinDynamics",
            description=description[:30000], location=location.get("label", "") if isinstance(location, dict) else "",
            published_at=item.get("publishDate"), raw_text=description[:30000],
        ))
    return jobs


async def collect_vinrobotics(source: dict) -> list[ObservedJob]:
    async with _career_client() as client:
        response = await client.get("https://vinrobotics.net/api/job-description", params={"page": 1, "pageSize": 100, "locale": "en"})
        response.raise_for_status()
        data = response.json()
        if "data" not in data or not isinstance(data["data"], list):
            raise RuntimeError("VinRobotics career API returned an unexpected response")
        jobs = []
        for item in data["data"]:
            title = str(item.get("title", "")).strip()
            slug = str(item.get("slug", ""))
            if not _target_title(title) or not re.fullmatch(r"[a-z0-9-]+", slug):
                continue
            url = urljoin(source["url"], "/career/" + slug)
            try:
                detail = await client.get(url)
                detail.raise_for_status()
                soup = BeautifulSoup(detail.text, "html.parser")
                node = soup.select_one(".content-logical")
                description = _structured_text(node) if node else ""
                if len(description) < 100:
                    continue
                jobs.append(ObservedJob(
                    url=str(detail.url), external_id=str(item.get("id") or ""),
                    title=title[:180], company=source.get("employer_name") or "VinRobotics",
                    description=description[:30000], location=str(item.get("location") or "")[:250],
                    apply_url=_application_destination(soup, str(detail.url)),
                    published_at=item.get("publishedAt"), raw_text=description[:30000],
                ))
            except httpx.HTTPError:
                continue
        return jobs


async def collect_smartrecruiters(source: dict) -> list[ObservedJob]:
    slug = source["config"]["company_slug"]
    endpoint = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    async with _career_client() as client:
        response = await client.get(endpoint, params={"limit": 100})
        response.raise_for_status()
        data = response.json()
        if "content" not in data:
            raise RuntimeError("SmartRecruiters returned an unexpected response")
        jobs = []
        for item in data["content"]:
            title = str(item.get("name", ""))
            if not _target_title(title):
                continue
            try:
                released = datetime.fromisoformat(item["releasedDate"].replace("Z", "+00:00"))
                if released < datetime.now(timezone.utc) - timedelta(days=180):
                    continue
                detail = await client.get(f"{endpoint}/{item['id']}")
                detail.raise_for_status()
                post = detail.json()
                sections = post.get("jobAd", {}).get("sections", {})
                description = "\n\n".join(_structured_text(BeautifulSoup(section.get("text") or "", "html.parser"))
                                          for section in sections.values() if isinstance(section, dict))
                if len(description) < 100 or not post.get("active", False):
                    continue
                jobs.append(ObservedJob(
                    url=post["postingUrl"], external_id=str(item["id"]),
                    title=title[:180], company=source.get("employer_name") or source["name"],
                    description=description[:30000], location=post.get("location", {}).get("fullLocation", "")[:250],
                    apply_url=post.get("applyUrl"), published_at=item.get("releasedDate"),
                    raw_text=description[:30000],
                ))
            except (httpx.HTTPError, KeyError, ValueError):
                continue
        return jobs


async def collect_mbbank(source: dict) -> list[ObservedJob]:
    """Search MB's own career API, then retain only live direct requisitions."""
    endpoint = "https://careers.mbbank.com.vn/libra-job-management/public/recruitment-news"
    async with _career_client() as client:
        listings: dict[str, dict] = {}
        for query in source["config"].get("queries", []):
            response = await client.get(endpoint, params={"name": query, "size": 100, "page": 0})
            response.raise_for_status()
            data = response.json()
            if "content" not in data:
                raise RuntimeError("MB Bank career API returned an unexpected response")
            for item in data["content"]:
                if _target_title(str(item.get("name", ""))) and item.get("id"):
                    listings[str(item["id"])] = item
        jobs = []
        for item in listings.values():
            try:
                deadline = datetime.strptime(str(item.get("toDate", "")), "%d-%m-%Y").date()
                if deadline < datetime.now(timezone.utc).date():
                    continue
                response = await client.get(f"{endpoint}/{item['id']}")
                response.raise_for_status()
                detail = response.json()
                raw_description = detail.get("jobDescriptionVn") or detail.get("jobDescriptionEn") or ""
                if "<" in raw_description and ">" in raw_description:
                    raw_description = _structured_text(BeautifulSoup(raw_description, "html.parser"))
                description = str(raw_description).strip()
                requirements = str(detail.get("experienceDescription") or "").strip()
                if requirements:
                    description += "\n\nRequirements\n\n" + requirements
                if len(description) < 100:
                    continue
                url = ("https://careers.mbbank.com.vn/list-of-posts/detail-list-of-posts"
                       f"?id={item['id']}&workGroupId={item.get('workGroupId') or ''}")
                jobs.append(ObservedJob(
                    url=url, external_id=str(item["id"]), title=str(detail.get("name") or item["name"])[:180],
                    company=source.get("employer_name") or "MB Bank",
                    description=description[:30000], location=str(detail.get("city") or item.get("province") or "")[:250],
                    raw_text=description[:30000],
                ))
            except (httpx.HTTPError, ValueError, KeyError):
                continue
        return jobs


CAREER_ADAPTERS = {
    "legacy": collect_career,
    "html_board": collect_html_board,
    "browser_board": collect_browser_board,
    "bidv": collect_bidv,
    "vietinbank": collect_vietinbank,
    "successfactors": collect_successfactors,
    "vindynamics": collect_vindynamics,
    "vinrobotics": collect_vinrobotics,
    "smartrecruiters": collect_smartrecruiters,
    "mbbank": collect_mbbank,
}


async def collect_source(settings: Settings, source: dict) -> list[ObservedJob]:
    if source["kind"] == "career":
        adapter = source.get("config", {}).get("adapter", "legacy")
        if adapter not in CAREER_ADAPTERS:
            raise ValueError(f"Unknown career adapter: {adapter}")
        jobs = await CAREER_ADAPTERS[adapter](source)
        for job in jobs:
            if not job.apply_url:
                job.apply_url = job.url
        return jobs
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.browser_profile), headless=True, viewport={"width": 1365, "height": 900},
            **chrome_context_options(required=True),
        )
        try:
            if source["kind"] == "linkedin":
                return await collect_linkedin(context, source)
            return await collect_facebook(context, source)
        finally:
            await context.close()
