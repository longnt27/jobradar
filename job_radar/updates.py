"""Published release checks for the locally installed app."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from . import __version__
from .db import Database


RELEASES_URL = "https://github.com/longnt27/jobradar/releases"
RELEASES_API = "https://api.github.com/repos/longnt27/jobradar/releases?per_page=100"
PREFERENCE_KEY = "release_check_preference"
STATUS_KEY = "release_check_status"
VERSION_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(beta)(?:\.(\d+))?)?$")


def _version_key(value: str) -> tuple[int, int, int, int, int] | None:
    match = VERSION_TAG.fullmatch(value)
    if not match:
        return None
    major, minor, patch, stage, stage_number = match.groups()
    return int(major), int(minor), int(patch), 0 if stage else 1, int(stage_number or 0)


def release_check_preference(db: Database) -> dict[str, str | bool]:
    saved = db.get_setting(PREFERENCE_KEY, "manual")
    mode = saved.get("mode") if isinstance(saved, dict) else saved
    include_beta = isinstance(saved, dict) and saved.get("include_beta") is True
    return {"mode": mode if mode in {"manual", "automatic"} else "manual",
            "include_beta": include_beta}


def save_release_check_preference(db: Database, mode: str, include_beta: bool | None = None) -> dict[str, str | bool]:
    if mode not in {"manual", "automatic"}:
        raise ValueError("Choose automatic or manual update checks.")
    previous = release_check_preference(db)
    preference = {"mode": mode, "include_beta": previous["include_beta"] if include_beta is None else include_beta}
    if preference != previous:
        db.set_setting(PREFERENCE_KEY, preference)
        db.set_setting(STATUS_KEY, {})
    return preference


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
                response = await owned_client.get(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        else:
            response = await client.get(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        if response.status_code == 404:
            result.update(status="no_release", message="No published release is available yet.")
        else:
            response.raise_for_status()
            releases = response.json()
            if not isinstance(releases, list):
                raise ValueError("Release list is invalid")
            include_beta = release_check_preference(db)["include_beta"]
            published = [release for release in releases if isinstance(release, dict) and not release.get("draft")]
            candidates = [(key, tag) for release in published
                          if (tag := str(release.get("tag_name") or "")) and (key := _version_key(tag))
                          and (include_beta or (key[3] == 1 and not release.get("prerelease")))]
            if not candidates and any(_version_key(str(release.get("tag_name") or "")) is None for release in published):
                raise ValueError("A release version is missing or invalid")
            if not candidates:
                result.update(status="no_release", message="No published release is available yet.")
            else:
                latest_key, tag = max(candidates)
                current_key = _version_key(__version__)
                if current_key is None:
                    raise ValueError("Installed version is invalid")
                result["latest"] = tag.removeprefix("v")
                result["url"] = f"{RELEASES_URL}/tag/{tag}"
                result["update_available"] = latest_key > current_key
                result["status"] = "update_available" if result["update_available"] else "up_to_date"
                result["message"] = (f"Version {result['latest']} is available." if result["update_available"]
                                     else "You have the latest published release.")
    except (httpx.HTTPError, ValueError, KeyError):
        result.update(status="error", message="Could not check for updates. Try again later.")
    db.set_setting(STATUS_KEY, result)
    return result
