from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


LINKEDIN_SCAN_INTERVAL_MINUTES = 720


_IGNORED_PARAMETERS = {
    "alternatechannel", "currentjobid", "ebp", "isjobsearch", "origin",
    "position", "refid", "referralsearchid", "trackingid", "trk",
}


def recent_search_url(url: str) -> str:
    """Keep the search filters but limit LinkedIn results to the past 24 hours."""
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or
            (parts.hostname or "").lower() not in {"linkedin.com", "www.linkedin.com"} or
            parts.path.rstrip("/") not in {"/jobs/search", "/jobs/search-results"}):
        raise ValueError("Paste a LinkedIn Jobs search link, not an individual job link")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=False)
             if key.casefold() != "f_tpr"]
    query.append(("f_TPR", "r86400"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def search_from_url(url: str) -> tuple[str, str]:
    """Return a stable search URL and a label derived from its filters."""
    parts = urlsplit(recent_search_url(url))
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=False)
             if key.casefold() not in _IGNORED_PARAMETERS]
    filters = dict(query)
    keywords = re.sub(r"\s+", " ", filters.get("keywords", "")).strip()
    location = re.sub(r"\s+", " ", filters.get("location", "")).strip()
    remote = filters.get("f_WT") == "2" or location.casefold() == "remote"
    if location and location.casefold() != "remote" and location.casefold() not in keywords.casefold():
        filters["keywords"] = f"{keywords} in {location}" if keywords else f"Jobs in {location}"
    if remote and "remote" not in filters.get("keywords", keywords).casefold():
        filters["keywords"] = f"{filters.get('keywords', keywords)} remote".strip()
    filters.pop("location", None)
    filters.pop("f_WT", None)
    query = sorted(filters.items(), key=lambda item: (item[0], item[1]))
    canonical = urlunsplit(("https", "www.linkedin.com", "/jobs/search/", urlencode(query), ""))
    if keywords and location:
        name = f"{keywords} · {location}"
    elif keywords:
        name = keywords
    elif location:
        name = f"LinkedIn jobs · {location}"
    else:
        name = "LinkedIn jobs search"
    return canonical, name[:120]
