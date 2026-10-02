from __future__ import annotations

import os
import re

import httpx


def list_public_repositories(username: str) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", username):
        raise ValueError("Enter a GitHub username")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "JobRadar/0.1"}
    if token := os.environ.get("JOB_RADAR_GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    repositories = []
    with httpx.Client(timeout=20, headers=headers) as client:
        for page in range(1, 4):
            response = client.get(f"https://api.github.com/users/{username}/repos", params={"per_page": 100, "page": page, "sort": "updated"})
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise RuntimeError("GitHub returned an unexpected response")
            repositories.extend({"name": item["name"], "url": item["html_url"], "description": item.get("description"),
                                 "language": item.get("language"), "updated_at": item.get("updated_at"), "fork": item.get("fork", False)}
                                for item in batch if not item.get("private"))
            if len(batch) < 100:
                break
    return repositories
