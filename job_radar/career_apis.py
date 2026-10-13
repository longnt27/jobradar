"""Public career JSON feeds with explicit, employer-specific field mappings."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from .ingest import ObservedJob


def field(value, path: str):
    for part in path.split(".") if path else []:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def names(value) -> str:
    if isinstance(value, list):
        return ", ".join(names(item) for item in value if item)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("fullLocation") or value.get("city") or "")
    return str(value or "")


def mapped_names(row: dict, path: str, config: dict) -> str:
    value = field(row, path)
    mapping = config.get("value_maps", {}).get(path, {})
    if not mapping:
        return names(value)
    values = value if isinstance(value, list) else [value]
    return ", ".join(mapping.get(names(item), names(item)) for item in values if item)


def _terms(row: dict, taxonomy: str) -> str:
    return ", ".join(str(term.get("name") or "") for group in row.get("_embedded", {}).get("wp:term", [])
                     if isinstance(group, list) for term in group if term.get("taxonomy") == taxonomy)


async def collect_json_board(source: dict) -> list[ObservedJob]:
    from .collectors import _career_client, _detail_description, _expired_posting, _structured_text, _target_title

    config = source["config"]
    api_url = config["api_url"]
    page_size = int(config.get("page_size", 100))
    offset = 0
    seen = set()
    rows = []
    total = None
    max_pages = int(config.get("max_pages", 30))
    async with _career_client() as client:
        for page in range(1, max_pages + 1):
            params = {**config.get("params", {})}
            if config.get("limit_param"):
                params[config["limit_param"]] = page_size
            if config.get("page_param"):
                params[config["page_param"]] = page
            if config.get("offset_param"):
                params[config["offset_param"]] = offset
            if config.get("method", "GET") == "POST":
                response = await client.post(api_url, data=params)
            else:
                response = await client.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()
            batch = field(data, config.get("rows_path", ""))
            if not isinstance(batch, list):
                raise RuntimeError("Career API returned an unexpected job listing")
            advertised = field(data, config["total_field"]) if config.get("total_field") else response.headers.get("X-WP-Total")
            if advertised is not None:
                total = int(advertised)
            if not batch:
                if total is not None and offset < total:
                    raise RuntimeError("Career API ended before all advertised postings were returned")
                break
            previous = len(seen)
            for row in batch:
                if not isinstance(row, dict):
                    raise RuntimeError("Career API returned an invalid posting")
                identity = names(field(row, config.get("id_field", "id"))) or names(field(row, config["url_field"]))
                if not identity:
                    raise RuntimeError("Career API posting is missing its identity")
                if identity not in seen:
                    rows.append(row)
                    seen.add(identity)
            if len(seen) == previous:
                raise RuntimeError("Career API repeated a page instead of completing pagination")
            offset += len(batch)
            if total is not None and offset >= total:
                break
            if not config.get("page_param") and not config.get("offset_param"):
                if total is not None and offset < total:
                    raise RuntimeError("Career API needs pagination to collect its full listing")
                break
            if total is None and len(batch) < page_size:
                break
        else:
            raise RuntimeError("Career API exceeded its configured pagination limit")

        jobs = []
        for row in rows:
            if any(field(row, path) for path in config.get("closed_fields", [])):
                continue
            if config.get("deadline_field"):
                deadline = names(field(row, config["deadline_field"]))
                if deadline:
                    try:
                        if datetime.fromisoformat(deadline.replace("Z", "+00:00")).date() < datetime.now(timezone.utc).date():
                            continue
                    except ValueError:
                        pass
            title = BeautifulSoup(names(field(row, config["title_field"])), "html.parser").get_text(" ", strip=True)
            if not _target_title(title):
                continue
            location = ", ".join(filter(None, (mapped_names(row, path, config) for path in config.get("location_fields", []))))
            if config.get("location_taxonomy"):
                location = _terms(row, config["location_taxonomy"])
            if config.get("location_include") and not re.search(config["location_include"], location, re.I):
                continue
            url = names(field(row, config["url_field"]))
            if not url and config.get("url_template"):
                url = config["url_template"].format(**{k: quote(str(v), safe="") for k, v in row.items() if isinstance(v, (str, int))})
            url = urljoin(source["url"], url)
            if urlsplit(url).scheme not in {"http", "https"} or url.rstrip("/") == source["url"].rstrip("/"):
                raise RuntimeError("Career API posting is missing a specific posting URL")
            sections = []
            for label, path in config.get("description_sections", {}).items():
                value = field(row, path)
                if not value:
                    continue
                content = "<ul>" + "".join("<li>" + names(item) + "</li>" for item in value) + "</ul>" if isinstance(value, list) else names(value)
                if not re.search(r"</?[a-z][^>]*>", content, re.I):
                    content = escape(content).replace("\n", "<br>")
                text = _structured_text(BeautifulSoup(content, "html.parser"))
                if text:
                    sections.append(f"{label}\n\n{text}" if label else text)
            description = "\n\n".join(sections)
            if config.get("detail_selector") or not description:
                try:
                    detail = await client.get(url)
                    if detail.status_code in {404, 410}:
                        continue
                    detail.raise_for_status()
                except httpx.HTTPError as error:
                    raise RuntimeError(f"Career posting could not be read: {url}") from error
                soup = BeautifulSoup(detail.text, "html.parser")
                for node in soup(["script", "style", "header", "footer", "nav"]):
                    node.decompose()
                if _expired_posting(soup.get_text(" ", strip=True)):
                    continue
                if not description:
                    description = _detail_description(soup, config.get("detail_selector", "main, article"))
                if config.get("location_selector"):
                    node = soup.select_one(config["location_selector"])
                    if node:
                        location = node.get_text(" ", strip=True)
            if len(description) < 100 or _expired_posting(description):
                continue
            published = names(field(row, config["published_field"])) if config.get("published_field") else None
            if published:
                try:
                    date = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    published = date.replace(tzinfo=date.tzinfo or timezone.utc).isoformat()
                except ValueError:
                    published = None
            jobs.append(ObservedJob(
                url=url, title=title[:180], company=source.get("employer_name") or source["name"],
                description=description[:30000], location=location[:250],
                work_mode=_terms(row, config["mode_taxonomy"]) if config.get("mode_taxonomy") else mapped_names(row, config["mode_field"], config) if config.get("mode_field") else "",
                external_id=names(field(row, config.get("id_field", "id"))),
                apply_url=names(field(row, config["apply_field"])) if config.get("apply_field") else url,
                published_at=published, raw_text=description[:30000],
            ))
    return jobs
