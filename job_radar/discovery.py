from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database, new_id, now
from .ranking import score_job


MERGE_REASON_LABELS = {
    "new_vacancy": "First sighting",
    "same_url": "Same posting URL",
    "facebook_content": "Very similar Facebook posting",
    "user_split": "Marked distinct by you",
}


def merge_reason_label(reason: str) -> str:
    return MERGE_REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        return None


def source_coverage(source: dict[str, Any], recent_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Translate collector state into a user-facing discovery confidence contract."""
    state = source.get("scan_state") or source.get("last_status") or "not_scanned"
    config = source.get("config") or {}
    latest = recent_runs[0] if recent_runs else None
    cap = int(config.get("max_results") or config.get("max_posts") or 0)
    observed = int((latest or {}).get("observed_count") or 0)
    completed = [run for run in recent_runs if run.get("status") in {"success", "empty", "failed", "auth_required"}]
    failures = 0
    for run in completed:
        if run.get("status") not in {"failed", "auth_required"}:
            break
        failures += 1
    empties = 0
    for run in completed:
        if run.get("status") != "empty":
            break
        empties += 1

    if not source.get("enabled"):
        return {"level": "paused", "label": "Not watching", "detail": "Automatic checks are off.", "actionable": False}
    if state == "auth_required":
        return {"level": "degraded", "label": "Coverage interrupted", "detail": "Sign-in is required before this source can be checked again.", "actionable": True}
    if failures >= 2 or state == "failed":
        return {"level": "degraded", "label": "Coverage degraded", "detail": f"{max(failures, 1)} recent check{'s' if max(failures, 1) != 1 else ''} failed.", "actionable": True}
    if not latest:
        return {"level": "unknown", "label": "Coverage unknown", "detail": "This source has not completed a check yet.", "actionable": True}
    last_success = _parse_time(source.get("last_success_at"))
    interval = int(source.get("interval_minutes") or 240)
    if last_success and datetime.now(timezone.utc) - last_success > timedelta(minutes=max(interval * 3, 720)):
        return {"level": "degraded", "label": "Coverage stale", "detail": "This source has not completed successfully on its expected schedule.", "actionable": True}
    if cap and observed >= cap:
        return {"level": "limited", "label": "Coverage limited", "detail": f"Checked {observed} postings and reached the collection limit of {cap}.", "actionable": True}
    if empties >= 3 and int(source.get("job_count") or 0) > 0:
        return {"level": "limited", "label": "Coverage uncertain", "detail": "Several recent checks found nothing after this source found jobs before.", "actionable": True}
    if source.get("kind") in {"linkedin", "facebook"}:
        return {
            "level": "moderate",
            "label": "Coverage looks normal",
            "detail": f"Checked {observed} posting{'s' if observed != 1 else ''}; social feeds can still hide older results.",
            "actionable": False,
        }
    return {
        "level": "good",
        "label": "Coverage looks good",
        "detail": f"Checked {observed} posting{'s' if observed != 1 else ''} without hitting a configured limit.",
        "actionable": False,
    }


def summarize_discovery(sources: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = [source for source in sources if source.get("enabled")]
    by_kind = {
        kind: sum(1 for source in enabled if source.get("kind") == kind)
        for kind in ("linkedin", "facebook", "career")
    }
    degraded = [source for source in enabled if source.get("coverage", {}).get("level") == "degraded"]
    limited = [source for source in enabled if source.get("coverage", {}).get("level") in {"limited", "unknown"}]
    if degraded:
        level, label = "degraded", f"{len(degraded)} source{'s' if len(degraded) != 1 else ''} threaten discovery"
    elif limited:
        level, label = "limited", f"{len(limited)} source{'s' if len(limited) != 1 else ''} have uncertain coverage"
    elif enabled:
        level, label = "good", "Discovery coverage looks normal"
    else:
        level, label = "unknown", "No sources are being watched"
    gaps = []
    if not by_kind["linkedin"]:
        gaps.append("No LinkedIn searches")
    if not by_kind["facebook"]:
        gaps.append("No Facebook groups")
    if not by_kind["career"]:
        gaps.append("No company career pages")
    return {"level": level, "label": label, "counts": by_kind, "gaps": gaps,
            "degraded_source_ids": [source["id"] for source in degraded],
            "limited_source_ids": [source["id"] for source in limited]}


def split_observation(db: Database, vacancy_id: str, observation_id: str) -> str:
    """Move one sighting into a new vacancy without deleting or cloning source evidence."""
    profile = db.get_setting("profile", {})
    matching_model = db.get_setting("matching_model", "")
    timestamp = now()
    with db.connection() as conn:
        linked = conn.execute(
            "SELECT vo.merge_reason,o.*,s.kind AS source_kind FROM vacancy_observations vo "
            "JOIN observations o ON o.id=vo.observation_id JOIN sources s ON s.id=o.source_id "
            "WHERE vo.vacancy_id=? AND vo.observation_id=?",
            (vacancy_id, observation_id),
        ).fetchone()
        if not linked:
            raise KeyError("Sighting not found for this job")
        count = conn.execute("SELECT COUNT(*) FROM vacancy_observations WHERE vacancy_id=?", (vacancy_id,)).fetchone()[0]
        if count < 2:
            raise ValueError("This job has only one sighting, so there is nothing to split")
        try:
            payload = json.loads(linked["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        title = str(payload.get("title") or "Untitled job")
        company = str(payload.get("company") or "Unknown employer")
        description = str(payload.get("description") or linked["raw_text"] or "")
        location = str(payload.get("location") or "")
        work_mode = str(payload.get("work_mode") or "")
        apply_url = payload.get("apply_url")
        published_at = payload.get("published_at") or linked["published_at"]
        employer = conn.execute("SELECT id FROM employers WHERE lower(name)=lower(?)", (company,)).fetchone()
        employer_id = employer[0] if employer else None
        score, detail = score_job(
            {"title": title, "company": company, "description": description, "location": location,
             "work_mode": work_mode, "first_seen_at": linked["first_seen_at"]},
            profile,
        )
        new_vacancy = new_id()
        conn.execute(
            "INSERT INTO vacancies(id,employer_id,company,title,location,work_mode,description,apply_url,published_at,"
            "first_seen_at,last_seen_at,score,score_detail,analysis_status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_vacancy, employer_id, company, title, location, work_mode, description, apply_url,
             published_at, linked["first_seen_at"], linked["last_seen_at"], score,
             json.dumps(detail, ensure_ascii=False), "pending" if matching_model else "not_configured",
             timestamp, timestamp),
        )
        conn.execute("INSERT INTO vacancy_fts(vacancy_id,title,company,description) VALUES(?,?,?,?)",
                     (new_vacancy, title, company, description))
        conn.execute("UPDATE vacancy_observations SET vacancy_id=?,merge_reason='user_split' WHERE observation_id=?",
                     (new_vacancy, observation_id))

        remaining = conn.execute(
            "SELECT o.payload,o.raw_text,o.published_at,o.first_seen_at,o.last_seen_at "
            "FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id "
            "WHERE vo.vacancy_id=? ORDER BY o.last_seen_at DESC,o.id DESC LIMIT 1",
            (vacancy_id,),
        ).fetchone()
        if remaining:
            try:
                canonical = json.loads(remaining["payload"])
            except (TypeError, json.JSONDecodeError):
                canonical = {}
            conn.execute(
                "UPDATE vacancies SET company=?,title=?,location=?,work_mode=?,description=?,apply_url=?,"
                "published_at=COALESCE(?,published_at),last_seen_at=?,updated_at=? WHERE id=?",
                (canonical.get("company") or company, canonical.get("title") or title,
                 canonical.get("location") or "", canonical.get("work_mode") or "",
                 canonical.get("description") or remaining["raw_text"] or description,
                 canonical.get("apply_url"), canonical.get("published_at") or remaining["published_at"],
                 remaining["last_seen_at"], timestamp, vacancy_id),
            )
            conn.execute(
                "UPDATE vacancy_fts SET title=(SELECT title FROM vacancies WHERE id=?),"
                "company=(SELECT company FROM vacancies WHERE id=?),"
                "description=(SELECT description FROM vacancies WHERE id=?) WHERE vacancy_id=?",
                (vacancy_id, vacancy_id, vacancy_id, vacancy_id),
            )
        return new_vacancy
