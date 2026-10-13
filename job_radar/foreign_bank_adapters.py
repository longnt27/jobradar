"""Parse Public Bank Vietnam's official multi-position recruitment announcement.

The bank publishes several vacancies in one HTML document rather than individual
job detail pages. Give each observed role a stable query identity while keeping
the original first-party board URL as the navigable application context.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .ingest import ObservedJob


_PUBLIC_BANK_HOSTS = {"publicbank.com.vn", "www.publicbank.com.vn"}
_OFFICE_HEADING = re.compile(r"^1\s*[.]\s*HỘI SỞ CHÍNH\b", re.I)
_BRANCH_HEADING = re.compile(r"^2\s*[.]\s*CHI NHÁNH\s*:", re.I)
_ROLE_HEADING = re.compile(
    r"^(?:Chuyên viên(?:\s*/\s*Nhân viên)?|Nhân viên|Kiểm toán viên|"
    r"Lập trình viên|Quản lý bộ phận|Phó Trưởng Phòng|Trưởng Phòng|"
    r"Giao dịch viên|Kỹ sư)\b",
    re.I,
)
_CONTACT_EMAIL = re.compile(r"\bEmail\s*:\s*([\w.+-]+@publicbank\.com\.vn)\b", re.I)


def _fold_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title.casefold())
    ascii_letters = "".join(character for character in folded if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", ascii_letters.replace("đ", "d"))).strip()


def _role_identity(title: str) -> str:
    """Group the two published wordings of the same IT roles into one vacancy."""
    key = _fold_title(title)
    if "lap trinh vien" in key and "cong nghe thong tin" in key:
        return "head-office-programmer"
    if "an ninh mang" in key and "cong nghe thong tin" in key:
        return "head-office-network-security"
    return key


def _role_url(board_url: str, identity: str) -> str:
    """Keep URLs distinct because ingest deduplicates observations by normalized URL.

    jobradar_role is our stable position identifier, NOT a bank-issued requisition
    number or a bank-supported deep link. The URL displays the original board.
    """
    parts = urlsplit(board_url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "jobradar_role"]
    params.append(("jobradar_role", identity))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


def parse_public_bank(html: str, url: str, employer: str = "Public Bank Vietnam") -> list[ObservedJob]:
    """Split the official role list into independent postings, without cross-role JDs."""
    from .collectors import _expired_posting, _target_title

    if urlsplit(url).hostname not in _PUBLIC_BANK_HOSTS:
        raise RuntimeError("Public Bank listing was redirected outside the official domain")
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        node.decompose()

    # HTML on this bank site mixes paragraphs and list items. Newline separators
    # preserve the heading/requirement boundaries even without semantic classes.
    raw_text = soup.get_text("\n", strip=True)
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    email = _CONTACT_EMAIL.search(raw_text)
    if not email:
        raise RuntimeError("Public Bank recruitment page has no verified HR application email")

    postings: list[tuple[str, list[str], str]] = []
    title: str | None = None
    body: list[str] = []
    office = ""
    in_openings = False
    headings_found = 0

    def flush() -> None:
        if title:
            postings.append((title, body.copy(), office))

    for line in lines:
        if _OFFICE_HEADING.match(line):
            flush()
            title, body = None, []
            office = "Hội sở chính, Hà Nội"
            in_openings = True
            continue
        if _BRANCH_HEADING.match(line):
            flush()
            title, body = None, []
            office = "Hà Nội / TP. Hồ Chí Minh"
            in_openings = True
            continue
        if re.search(r"^THÔNG TIN LIÊN HỆ\s*:", line, re.I):
            flush()
            title, body = None, []
            in_openings = False
            continue
        if not in_openings:
            continue
        if 15 <= len(line) <= 180 and _ROLE_HEADING.match(line) and len(line.split()) >= 4:
            flush()
            title, body = line, []
            headings_found += 1
        elif title:
            body.append(line)

    flush()
    if not headings_found:
        raise RuntimeError("Public Bank careers page contains no recognizable vacancy headings")

    jobs_by_identity: dict[str, ObservedJob] = {}
    for title, lines, location in postings:
        if not _target_title(title):
            continue
        description = "\n".join("• " + line for line in lines).strip()
        if len(description) < 100:
            raise RuntimeError(f"Public Bank role is missing its own requirements: {title}")
        if _expired_posting(description):
            continue
        role_key = f"{location}|{_role_identity(title)}"
        requisition = hashlib.sha256(f"{employer}|{role_key}".encode("utf-8")).hexdigest()[:16]
        job = ObservedJob(
            url=_role_url(url, requisition),
            external_id=f"publicbank-{requisition}",
            title=title[:180],
            company=employer,
            description=description[:30000],
            location=location,
            apply_url=f"mailto:{email.group(1)}",
            raw_text=description[:30000],
        )
        existing = jobs_by_identity.get(role_key)
        if existing is None or len(job.description) > len(existing.description):
            jobs_by_identity[role_key] = job
    return list(jobs_by_identity.values())


async def collect_public_bank(source: dict) -> list[ObservedJob]:
    """Collect the first-party board; never report HTTP/parser failure as no jobs."""
    from .collectors import _career_client

    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        return parse_public_bank(
            response.text, str(response.url),
            source.get("employer_name") or source["name"],
        )
