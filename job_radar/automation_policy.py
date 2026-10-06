"""Canonical policy for automatic application preparation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .preparation import preparation_preflight
from .search_intent import fit_summary, normalize_search_intent


DEFAULT_AUTOMATION_POLICY = {
    "enabled": False,
    "include_shortlisted": False,
    "max_job_age_days": 3,
    "require_verified_destination": True,
    "require_preferred_location": False,
    "max_auto_drafts_per_day": 5,
    "max_review_notifications_per_day": 5,
}


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return default


def normalize_automation_policy(value: Any, *, threshold: int = 80) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_AUTOMATION_POLICY["enabled"])),
        "threshold": _bounded_int(threshold, 80, 0, 100),
        "include_shortlisted": bool(raw.get("include_shortlisted", False)),
        "max_job_age_days": _bounded_int(raw.get("max_job_age_days"), 3, 1, 30),
        "require_verified_destination": bool(raw.get("require_verified_destination", True)),
        "require_preferred_location": bool(raw.get("require_preferred_location", False)),
        "max_auto_drafts_per_day": _bounded_int(raw.get("max_auto_drafts_per_day"), 5, 1, 50),
        "max_review_notifications_per_day": _bounded_int(
            raw.get("max_review_notifications_per_day"), 5, 1, 50
        ),
    }


def automation_policy(db: Database) -> dict[str, Any]:
    saved = db.get_setting("auto_apply", {})
    intent = normalize_search_intent(db.get_setting("search_intent", {}))
    return normalize_automation_policy(saved, threshold=intent["strong_match_threshold"])


def local_day_start_utc() -> str:
    local_start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc).isoformat(timespec="seconds")


def daily_auto_drafts_used(db: Database) -> int:
    row = db.one(
        "SELECT COUNT(DISTINCT d.vacancy_id) AS count "
        "FROM application_drafts d JOIN auto_application_attempts a ON a.vacancy_id=d.vacancy_id "
        "WHERE a.requested_by='automation' AND datetime(d.created_at)>=datetime(?)",
        (local_day_start_utc(),),
    )
    return int(row["count"] if row else 0)


def daily_review_notifications_used(db: Database) -> int:
    row = db.one(
        "SELECT COUNT(*) AS count FROM notification_attempts n "
        "JOIN auto_application_attempts a ON a.vacancy_id=n.vacancy_id "
        "WHERE n.channel='telegram_application_review' AND n.status='sent' "
        "AND a.requested_by='automation' "
        "AND datetime(n.sent_at)>=datetime(?)",
        (local_day_start_utc(),),
    )
    return int(row["count"] if row else 0)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _location_matches(job: dict[str, Any], preferences: dict[str, Any], detail: dict[str, Any]) -> bool:
    wanted = [str(item).strip().casefold() for item in preferences.get("preferred_locations", []) if str(item).strip()]
    if not wanted:
        return True
    facts = detail.get("facts") if isinstance(detail.get("facts"), dict) else {}
    text = " ".join(filter(None, (str(job.get("location") or ""), str(facts.get("location") or "")))).casefold()
    return any(item in text or text in item for item in wanted if text)


def automation_eligibility(
    db: Database,
    job_id: str,
    *,
    policy: dict[str, Any] | None = None,
    require_analysis: bool = True,
    check_daily_limit: bool = False,
    check_existing_artifacts: bool = True,
) -> dict[str, Any]:
    config = policy or automation_policy(db)
    job = db.one("SELECT * FROM vacancies WHERE id=?", (job_id,))
    if not job:
        raise KeyError("Job not found")
    reasons: list[str] = []

    if not config["enabled"]:
        reasons.append("Automatic draft preparation is off.")

    decision = job.get("decision_state") or "undecided"
    if decision in {"ignored", "later"}:
        reasons.append("The job is ignored or saved for later.")
    elif decision == "shortlisted" and not config["include_shortlisted"]:
        reasons.append("Shortlisted jobs are bookmarks unless you opt them into automation.")

    if job.get("snoozed_until"):
        reasons.append("The job is snoozed.")
    if job.get("manual_applied_at"):
        reasons.append("The job is already marked as applied elsewhere.")
    if str(job.get("recruiting_outcome") or "none") != "none":
        reasons.append("The job already has a recruiting outcome.")

    excluded = db.one(
        "SELECT 1 AS blocked FROM employers WHERE id=? AND coverage_status='excluded_hcm' LIMIT 1",
        (job.get("employer_id"),),
    ) if job.get("employer_id") else None
    if excluded:
        reasons.append("The employer is excluded.")

    seen_at = _parse_time(job.get("first_seen_at"))
    if seen_at:
        age_days = (datetime.now(timezone.utc) - seen_at).total_seconds() / 86400
        if age_days > config["max_job_age_days"]:
            reasons.append(f"The job is older than the {config['max_job_age_days']}-day automation window.")

    try:
        detail = json.loads(job.get("score_detail") or "{}")
    except (TypeError, ValueError):
        detail = {}
    preferences = normalize_search_intent(db.get_setting("search_intent", {}))
    company = str(job.get("company") or "").casefold()
    excluded_employers = [
        str(item).strip().casefold()
        for item in preferences.get("excluded_employers", [])
        if str(item).strip()
    ]
    if excluded_employers and any(item in company for item in excluded_employers):
        reasons.append("The employer is on your excluded list.")

    if config["require_preferred_location"] and not _location_matches(job, preferences, detail):
        reasons.append("The job is outside your preferred locations.")

    if require_analysis:
        if job.get("analysis_status") != "done":
            reasons.append("Local match review is not finished.")
        elif job.get("score") is None or int(job["score"]) < config["threshold"]:
            reasons.append(f"The match score is below {config['threshold']}.")
        else:
            fit = fit_summary(job.get("score"), detail, preferences)
            if fit["fit_class"] == "outside":
                reasons.append("The job violates a hard search preference.")

    if config["require_verified_destination"]:
        preflight = preparation_preflight(db, job_id)
        if not preflight["ready"]:
            reasons.append("No verified application destination is available.")

    if check_existing_artifacts:
        if db.one("SELECT id FROM submissions WHERE vacancy_id=? LIMIT 1", (job_id,)):
            reasons.append("An application submission already exists.")
        if db.one("SELECT id FROM application_drafts WHERE vacancy_id=? LIMIT 1", (job_id,)):
            reasons.append("An application draft already exists.")

    if check_daily_limit and daily_auto_drafts_used(db) >= config["max_auto_drafts_per_day"]:
        reasons.append("The daily automatic draft limit has been reached.")

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "policy": config,
        "job_id": job_id,
    }
