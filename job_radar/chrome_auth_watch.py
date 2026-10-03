from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


# Chrome records the final committed page, so a sign-in redirect does not count.
ACCOUNT_PAGES = {
    "linkedin": re.compile(r"^https://(?:www\.)?linkedin\.com/feed(?:[/?#]|$)", re.I),
    "facebook": re.compile(r"^https://(?:www\.)?facebook\.com/settings(?:[/?#]|$)", re.I),
}
CHROME_EPOCH_OFFSET = 11644473600


def chrome_login_complete(profile: Path, site: str, started_at: float) -> bool:
    """Read only Chrome's newest committed visit in Job Radar's saved profile."""
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
                    "WHERE v.visit_time>=? ORDER BY v.visit_time DESC LIMIT 1",
                    (int((started_at + CHROME_EPOCH_OFFSET) * 1_000_000),),
                ).fetchone()
    except (OSError, sqlite3.DatabaseError):
        # Chrome may be writing while the snapshot is copied; retry on the next poll.
        return False
    return bool(row and ACCOUNT_PAGES[site].match(row[0]))
