from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .db import Database, new_id, now
from .settings import Settings


def canonical_github_url(value: str) -> tuple[str, str]:
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or parts.hostname != "github.com" or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Use an HTTPS GitHub repository URL")
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", parts.path)
    if not match:
        raise ValueError("Use a GitHub repository URL such as https://github.com/owner/repo")
    owner, repo = match.groups()
    if owner in {".", ".."} or repo in {".", ".."}:
        raise ValueError("Invalid repository path")
    return f"https://github.com/{owner}/{repo}", f"{owner}__{repo}"


def _git(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, timeout=120,
        env={"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:600] or "Git command failed")
    return result.stdout.strip()


def _read_small(root: Path, relative: str) -> str:
    target = root / relative
    if not target.is_file() or target.is_symlink() or target.stat().st_size > 100_000:
        return ""
    return target.read_text(encoding="utf-8", errors="replace")[:15_000]


def inspect_repository(db: Database, settings: Settings, value: str) -> dict:
    url, slug = canonical_github_url(value)
    settings.ensure_dirs()
    local = settings.repository_dir / slug
    if local.exists():
        remote = _git(["remote", "get-url", "origin"], cwd=local)
        if remote.rstrip("/").removesuffix(".git") != url:
            raise ValueError("Local repository has a different origin")
        _git(["fetch", "--depth", "50", "origin"], cwd=local)
        branch = _git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=local)
        _git(["checkout", "--detach", branch], cwd=local)
    else:
        _git(["clone", "--depth", "50", "--", url, str(local)])
    commit = _git(["rev-parse", "HEAD"], cwd=local)
    files = _git(["ls-files"], cwd=local).splitlines()[:500]
    readme = next((name for name in files if name.lower() in {"readme.md", "readme.rst", "readme.txt"}), "")
    manifests = [name for name in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", "requirements.txt") if name in files]
    history = _git(["log", "-n", "12", "--pretty=format:%h %s"], cwd=local).splitlines()
    support = [url, f"{url}/tree/{commit}"]
    excerpt = _read_small(local, readme) if readme else ""
    title = slug.split("__", 1)[1]
    summary = {
        "readme": excerpt,
        "manifests": {name: _read_small(local, name)[:6000] for name in manifests},
        "files": files,
        "recent_commits": history,
    }
    claim = f"Project: {title}. " + (re.sub(r"\s+", " ", excerpt.splitlines()[0]).strip("# ")[:240] if excerpt else "Repository available for review; describe your contribution before approving.")
    timestamp = now()
    with db.connection() as conn:
        existing = conn.execute("SELECT id FROM repository_snapshots WHERE url=?", (url,)).fetchone()
        repository_id = existing[0] if existing else new_id()
        if existing:
            conn.execute("UPDATE repository_snapshots SET commit_sha=?,summary=?,inspected_at=? WHERE id=?",
                         (commit, json.dumps(summary, ensure_ascii=False), timestamp, repository_id))
        else:
            conn.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
                         (repository_id, url, str(local), commit, json.dumps(summary, ensure_ascii=False), timestamp))
        card = conn.execute("SELECT id FROM evidence WHERE repository_id=?", (repository_id,)).fetchone()
        evidence_id = card[0] if card else new_id()
        if not card:
            conn.execute("INSERT INTO evidence(id,kind,title,claim,details,support,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (evidence_id, "project", title, claim, json.dumps({"contribution": "unverified"}), json.dumps(support), repository_id, timestamp, timestamp))
    return {"repository_id": repository_id, "evidence_id": evidence_id, "commit_sha": commit, "summary": summary}
