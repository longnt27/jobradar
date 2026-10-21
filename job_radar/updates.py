"""Published release checks for the locally installed app."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from . import __version__
from .db import Database


RELEASES_URL = "https://github.com/longnt27/jobradar/releases"
LATEST_RELEASE_API = "https://api.github.com/repos/longnt27/jobradar/releases/latest"
PREFERENCE_KEY = "release_check_preference"
STATUS_KEY = "release_check_status"
VERSION_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def release_check_preference(db: Database) -> dict[str, str]:
    mode = db.get_setting(PREFERENCE_KEY, "manual")
    return {"mode": mode if mode in {"manual", "automatic"} else "manual"}


def save_release_check_preference(db: Database, mode: str) -> dict[str, str]:
    if mode not in {"manual", "automatic"}:
        raise ValueError("Choose automatic or manual update checks.")
    db.set_setting(PREFERENCE_KEY, mode)
    return {"mode": mode}


def cached_release_status(db: Database) -> dict:
    cached = db.get_setting(STATUS_KEY, {})
    if isinstance(cached, dict) and cached.get("current") == __version__:
        return cached
    return {
        "current": __version__, "latest": None, "update_available": False,
        "url": RELEASES_URL, "checked_at": None, "status": "not_checked",
        "message": "Updates have not been checked yet.",
    }


async def check_for_release(db: Database, client: httpx.AsyncClient | None = None) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {
        "current": __version__, "latest": None, "update_available": False,
        "url": RELEASES_URL, "checked_at": checked_at,
    }
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=8) as owned_client:
                response = await owned_client.get(LATEST_RELEASE_API, headers={"Accept": "application/vnd.github+json"})
        else:
            response = await client.get(LATEST_RELEASE_API, headers={"Accept": "application/vnd.github+json"})
        if response.status_code == 404:
            result.update(status="no_release", message="No published release is available yet.")
        else:
            response.raise_for_status()
            tag = str(response.json().get("tag_name") or "")
            latest_match = VERSION_TAG.fullmatch(tag)
            current_match = VERSION_TAG.fullmatch(__version__)
            if not latest_match or not current_match:
                raise ValueError("A release version is missing or invalid")
            result["latest"] = tag.removeprefix("v")
            result["url"] = f"{RELEASES_URL}/tag/{tag}"
            result["update_available"] = tuple(map(int, latest_match.groups())) > tuple(map(int, current_match.groups()))
            result["status"] = "update_available" if result["update_available"] else "up_to_date"
            result["message"] = (f"Version {result['latest']} is available." if result["update_available"]
                                 else "You have the latest published release.")
    except (httpx.HTTPError, ValueError, KeyError):
        result.update(status="error", message="Could not check for updates. Try again later.")
    db.set_setting(STATUS_KEY, result)
    return result
