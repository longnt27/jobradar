"""Baoviet's official hiring announcements contain multiple positions per document."""
from __future__ import annotations

import re
from hashlib import sha256
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from .ingest import ObservedJob


def parse_baoviet_announcement(html: str, url: str) -> list[ObservedJob]:
    """Split recruitment table rows into stable, individually deduplicated jobs."""
    from .collectors import _application_destination, _expired_posting, _structured_text, _target_title

    soup = BeautifulSoup(html, "html.parser")
    if _expired_posting(soup.get_text(" ", strip=True)):
        return []
    output = []
    seen = set()
    title_node = soup.select_one("h1")
    parent_title = title_node.get_text(" ", strip=True) if title_node else ""
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", soup.get_text(" ", strip=True), re.I)
    apply_url = f"mailto:{email_match.group(0)}" if email_match else _application_destination(soup, url)
    for table in soup.select("table"):
        for row in table.select("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) < 3:
                continue
            header = cells[0].get_text(" ", strip=True)
            match = re.search(r"Chức danh\s*:\s*(.+?)(?=Số lượng\s*:|Nơi làm việc\s*:|$)", header, re.I)
            if not match:
                continue
            title = re.sub(r"\s+", " ", match.group(1)).strip(" -:;")
            if not _target_title(title):
                continue
            code_match = re.search(r"Mã số\s*:\s*([\w.-]+)", header, re.I)
            identity = code_match.group(1) if code_match else sha256(title.encode()).hexdigest()[:16]
            if identity in seen:
                continue
            description = "\n\n".join(filter(None, [
                _structured_text(cells[1]), _structured_text(cells[2])
            ]))
            if len(description) < 100:
                continue
            loc = re.search(r"Nơi làm việc\s*:\s*(.+)$", header, re.I)
            # Explicit query identifier survives URL normalization unlike a fragment.
            job_url = url + ("&" if "?" in url else "?") + urlencode({"jr_role": identity})
            output.append(ObservedJob(
                url=job_url, external_id=identity, company="Bao Viet",
                title=title[:180], location=(loc.group(1) if loc else "")[:250],
                description=description[:30000], raw_text=description[:30000],
                apply_url=apply_url, 
            ))
            seen.add(identity)
    return output


async def collect_baoviet_announcement(source: dict) -> list[ObservedJob]:
    """Follow real first-party announcement links, ignoring unrelated subsidiaries."""
    from .collectors import _career_client
    from urllib.parse import urlsplit

    async with _career_client() as client:
        response = await client.get(source["url"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        candidates = {}
        for anchor in soup.select("a[href]"):
            url = urljoin(str(response.url), anchor.get("href", ""))
            parts = urlsplit(url)
            title = anchor.get_text(" ", strip=True)
            if parts.hostname not in {"baoviet.com.vn", "www.baoviet.com.vn"}:
                continue
            if not re.search(r"tuy[eể]n.d[uụ]ng", title, re.I):
                continue
            if not re.search(r"trung t[aâ]m|t[aậ]p [đd]o[aà]n", title, re.I):
                continue
            if parts.path.rstrip("/") == urlsplit(source["url"]).path.rstrip("/"):
                continue
            candidates[url.split("#")[0]] = title
        if not candidates:
            raise RuntimeError("Bao Viet careers page exposed no group recruitment announcements")
        jobs = []
        for url in candidates:
            detail = await client.get(url)
            detail.raise_for_status()
            jobs.extend(parse_baoviet_announcement(detail.text, str(detail.url)))
        return jobs
