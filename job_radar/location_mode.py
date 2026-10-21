"""Keep a posting's place and work arrangement in separate fields."""

from __future__ import annotations

import json
import re


_TRAILING_MODE = re.compile(r"\s*\((on[ -]?site|remote|hybrid|wfh|work from home)\)\s*$", re.I)


def _canonical_mode(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "", value.casefold())
    return {"onsite": "On-site", "remote": "Remote", "hybrid": "Hybrid",
            "wfh": "Remote", "workfromhome": "Remote"}.get(normalized, value)


def split_location_work_mode(location: str | None, work_mode: str | None = None) -> tuple[str, str]:
    place = str(location or "").strip()
    mode = str(work_mode or "").strip()
    suffix = _TRAILING_MODE.search(place)
    if suffix:
        place = place[:suffix.start()].rstrip(" ,·-")
        if not mode:
            mode = suffix.group(1)
    return place, _canonical_mode(mode)


def normalize_saved_locations(db) -> int:
    """Backfill combined location strings once, including source observations."""
    if db.get_setting("location_work_mode_v1_normalized", False):
        return 0
    changed = 0
    with db.connection() as conn:
        for row in conn.execute("SELECT id,payload FROM observations").fetchall():
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            place, mode = split_location_work_mode(payload.get("location"), payload.get("work_mode"))
            if place != (payload.get("location") or "") or mode != (payload.get("work_mode") or ""):
                payload["location"], payload["work_mode"] = place, mode
                conn.execute("UPDATE observations SET payload=? WHERE id=?",
                             (json.dumps(payload, ensure_ascii=False), row["id"]))
                changed += 1
        for row in conn.execute("SELECT id,location,work_mode FROM vacancies").fetchall():
            place, mode = split_location_work_mode(row["location"], row["work_mode"])
            if place != (row["location"] or "") or mode != (row["work_mode"] or ""):
                conn.execute("UPDATE vacancies SET location=?,work_mode=? WHERE id=?", (place, mode, row["id"]))
                changed += 1
        conn.execute("INSERT INTO settings(key,value) VALUES('location_work_mode_v1_normalized','true') "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    return changed
