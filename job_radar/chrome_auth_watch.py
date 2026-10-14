from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path


# Chrome records the final committed page, so a sign-in redirect does not count.
ACCOUNT_PAGES = {
    "linkedin": re.compile(r"^https://(?:www\.)?linkedin\.com/feed(?:[/?#]|$)", re.I),
    "facebook": re.compile(r"^https://(?:www\.)?facebook\.com/(?:settings(?:[/?#]|$)|[?#]|$)", re.I),
}
CHROME_EPOCH_OFFSET = 11644473600


def facebook_session_present(profile: Path) -> bool | None:
    """Check saved authentication cookies; an unreadable store has unknown status."""
    try:
        cookies = next((path for path in (profile / "Default" / "Network" / "Cookies",
                                         profile / "Default" / "Cookies") if path.is_file()), None)
        if cookies is None:
            return None
        current_chrome = int((time.time() + CHROME_EPOCH_OFFSET) * 1_000_000)
        # Read the live database so committed logout changes in its WAL are visible.
        # Select names only; no cookie values need to leave Chrome's store.
        with closing(sqlite3.connect(cookies.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)) as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM cookies "
                "WHERE host_key IN ('.facebook.com','facebook.com','www.facebook.com') "
                "AND name IN ('c_user','xs') "
                "AND (has_expires=0 OR expires_utc=0 OR expires_utc>?) "
                "AND (length(value)>0 OR length(encrypted_value)>0)",
                (current_chrome,),
            ).fetchall()
        return {row[0] for row in rows} == {"c_user", "xs"}
    except (OSError, sqlite3.DatabaseError):
        return None


def chrome_login_complete(profile: Path, site: str, started_at: float) -> bool:
    """Read the site's newest committed visit, ignoring unrelated browser tabs."""
    history = profile / "Default" / "History"
    if not history.is_file():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="job-radar-history-") as directory:
            snapshot = Path(directory) / "History"
            shutil.copyfile(history, snapshot)
            with closing(sqlite3.connect(snapshot)) as conn:
                row = conn.execute(
                    "SELECT u.url FROM visits v JOIN urls u ON u.id=v.url "
                    "WHERE v.visit_time>=? AND (u.url LIKE ? OR u.url LIKE ?) "
                    "ORDER BY v.visit_time DESC LIMIT 1",
                    (int((started_at + CHROME_EPOCH_OFFSET) * 1_000_000),
                     f"https://www.{site}.com/%", f"https://{site}.com/%"),
                ).fetchone()
    except (OSError, sqlite3.DatabaseError):
        # Chrome may be writing while the snapshot is copied; retry on the next poll.
        return False
    return bool(row and ACCOUNT_PAGES[site].match(row[0]) and (
        site != "facebook" or facebook_session_present(profile) is True
    ))
