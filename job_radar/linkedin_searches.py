from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_IGNORED_PARAMETERS = {
    "alternatechannel", "currentjobid", "ebp", "isjobsearch", "origin",
    "position", "refid", "referralsearchid", "trackingid", "trk",
}


def search_from_url(url: str) -> tuple[str, str]:
    """Return a stable search URL and a label derived from its filters."""
    parts = urlsplit(url)
    if (parts.scheme not in {"http", "https"} or
            (parts.hostname or "").lower() not in {"linkedin.com", "www.linkedin.com"} or
            parts.path.rstrip("/") not in {"/jobs/search", "/jobs/search-results"}):
        raise ValueError("Paste a LinkedIn Jobs search link, not an individual job link")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=False)
             if key.casefold() not in _IGNORED_PARAMETERS]
    query.sort(key=lambda item: (item[0], item[1]))
    canonical = urlunsplit(("https", "www.linkedin.com", "/jobs/search/", urlencode(query), ""))
    filters = dict(query)
    keywords = re.sub(r"\s+", " ", filters.get("keywords", "")).strip()
    location = re.sub(r"\s+", " ", filters.get("location", "")).strip()
    if keywords and location:
        name = f"{keywords} · {location}"
    elif keywords:
        name = keywords
    elif location:
        name = f"LinkedIn jobs · {location}"
    else:
        name = "LinkedIn jobs search"
    return canonical, name[:120]
