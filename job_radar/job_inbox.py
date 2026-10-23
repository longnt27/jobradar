"""Canonical job inbox and lifecycle semantics.

Read state, user decision, application progress, and recruiting outcome are
independent dimensions. Application progress is derived from durable draft and
submission records instead of being user-editable job state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import Database, new_id, now


DECISIONS = {"undecided", "shortlisted", "ignored", "later"}
OUTCOMES = {"none", "interview", "rejected", "offer"}
CONFIRMED_SUBMISSIONS = {"sent_confirmed", "submitted_confirmed"}
UNCERTAIN_SUBMISSIONS = {"sending", "submitted_unconfirmed"}
APPLICATION_REVIEW_SQL = (
    "CASE "
    "WHEN EXISTS(SELECT 1 FROM submissions su WHERE su.draft_id=d.id AND su.status IN ('submitted_unconfirmed','sending')) THEN 'submission_uncertain' "
    "WHEN EXISTS(SELECT 1 FROM submissions ss WHERE ss.draft_id=d.id AND ss.status IN ('sent_confirmed','submitted_confirmed')) THEN 'sent' "
    "WHEN d.status IN ('sent','submission_uncertain') THEN d.status "
    "WHEN a.status IN ('regenerating','preparing','queued','sending') THEN a.status "
    "WHEN d.status='failed' OR a.status='failed' THEN 'failed' "
    "WHEN d.status='skipped' OR a.status='skipped' THEN 'skipped' "
    "WHEN d.project_refresh_error IS NOT NULL THEN 'needs_review' "
    "ELSE COALESCE(a.status,'draft') END"
)


def auto_shortlist_strong_matches(db: Database, threshold: int, job_id: str | None = None) -> int:
    """Shortlist scored jobs that have never received an explicit user decision."""
    timestamp = now()
    with db.connection() as conn:
        conn.execute(
            "UPDATE vacancies SET decision_state='undecided',state='new',auto_shortlisted=0,updated_at=? "
            "WHERE auto_shortlisted=1 AND decision_state='shortlisted' "
            "AND analysis_status='done' AND (score<? OR "
            "(json_extract(score_detail,'$.method')='deterministic' AND "
            "COALESCE(json_extract(score_detail,'$.confidence'),0)<50)) "
            "AND NOT EXISTS(SELECT 1 FROM feedback f WHERE f.vacancy_id=vacancies.id) "
            "AND (? IS NULL OR id=?)",
            (timestamp, threshold, job_id, job_id),
        )
        result = conn.execute(
            "UPDATE vacancies SET decision_state='shortlisted',state='interesting',auto_shortlisted=1,updated_at=? "
            "WHERE analysis_status='done' AND score>=? AND decision_state='undecided' "
            "AND (json_extract(score_detail,'$.method') IS NOT 'deterministic' OR "
            "COALESCE(json_extract(score_detail,'$.confidence'),0)>=50) "
            "AND snoozed_until IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM feedback f WHERE f.vacancy_id=vacancies.id) "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=vacancies.employer_id "
            "AND e.coverage_status='excluded_hcm') "
            "AND (? IS NULL OR id=?)",
            (timestamp, threshold, job_id, job_id),
        )
        return result.rowcount


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def release_due_snoozes(db: Database) -> int:
    timestamp = now()
    due = db.one(
        "SELECT COUNT(*) AS count FROM vacancies "
        "WHERE decision_state='later' AND snoozed_until IS NOT NULL AND datetime(snoozed_until)<=datetime(?)",
        (timestamp,),
    )["count"]
    if due:
        db.execute(
            "UPDATE vacancies SET decision_state='undecided',snoozed_until=NULL,state='new',updated_at=? "
            "WHERE decision_state='later' AND snoozed_until IS NOT NULL AND datetime(snoozed_until)<=datetime(?)",
            (timestamp, timestamp),
        )
    return int(due)


def mark_seen(db: Database, job_id: str) -> dict[str, Any]:
    row = db.one("SELECT id,seen_at FROM vacancies WHERE id=?", (job_id,))
    if not row:
        raise KeyError("Job not found")
    if not row["seen_at"]:
        timestamp = now()
        db.execute("UPDATE vacancies SET seen_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, job_id))
        row["seen_at"] = timestamp
    return row


def mark_application_seen(db: Database, draft_id: str) -> dict[str, Any]:
    draft = db.one("SELECT id,vacancy_id,seen_at FROM application_drafts WHERE id=?", (draft_id,))
    if not draft:
        raise KeyError("Application not found")
    timestamp = now()
    if not draft.get("seen_at"):
        db.execute("UPDATE application_drafts SET seen_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, draft_id))
        draft["seen_at"] = timestamp
    mark_seen(db, draft["vacancy_id"])
    return draft


def set_decision(
    db: Database,
    job_id: str,
    decision: str,
    *,
    reason: str | None = None,
    snoozed_until: str | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"Unsupported job decision: {decision}")
    row = db.one("SELECT id,decision_state FROM vacancies WHERE id=?", (job_id,))
    if not row:
        raise KeyError("Job not found")

    if decision == "later":
        parsed = _parse_time(snoozed_until)
        if not parsed or parsed <= datetime.now(timezone.utc):
            raise ValueError("Choose a future time for Later")
        snooze_value = parsed.isoformat(timespec="seconds")
    else:
        snooze_value = None

    legacy = "interesting" if decision == "shortlisted" else "ignored" if decision == "ignored" else "new"
    timestamp = now()
    with db.connection() as conn:
        conn.execute(
            "UPDATE vacancies SET decision_state=?,snoozed_until=?,state=?,auto_shortlisted=0,updated_at=? WHERE id=?",
            (decision, snooze_value, legacy, timestamp, job_id),
        )
        conn.execute(
            "INSERT INTO feedback(id,vacancy_id,state,reason,created_at) VALUES(?,?,?,?,?)",
            (new_id(), job_id, decision, (reason or "").strip() or None, timestamp),
        )
    return {"decision_state": decision, "snoozed_until": snooze_value}


def set_recruiting_outcome(db: Database, job_id: str, outcome: str) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"Unsupported recruiting outcome: {outcome}")
    row = db.one("SELECT id FROM vacancies WHERE id=?", (job_id,))
    if not row:
        raise KeyError("Job not found")
    timestamp = now()
    db.execute(
        "UPDATE vacancies SET recruiting_outcome=?,updated_at=? WHERE id=?",
        (outcome, timestamp, job_id),
    )
    return {"recruiting_outcome": outcome}


def set_manual_applied(db: Database, job_id: str, applied: bool) -> dict[str, Any]:
    row = db.one("SELECT id FROM vacancies WHERE id=?", (job_id,))
    if not row:
        raise KeyError("Job not found")
    if applied:
        timestamp = now()
        db.execute(
            "UPDATE vacancies SET manual_applied_at=?,manual_applied_source='user_external',updated_at=? WHERE id=?",
            (timestamp, timestamp, job_id),
        )
        return {"manual_applied_at": timestamp, "manual_applied_source": "user_external"}
    timestamp = now()
    db.execute(
        "UPDATE vacancies SET manual_applied_at=NULL,manual_applied_source=NULL,updated_at=? WHERE id=?",
        (timestamp, job_id),
    )
    return {"manual_applied_at": None, "manual_applied_source": None}


def application_filter_sql(progress: str) -> str:
    if not progress:
        return ""
    confirmed = ",".join(f"'{value}'" for value in sorted(CONFIRMED_SUBMISSIONS))
    uncertain = ",".join(f"'{value}'" for value in sorted(UNCERTAIN_SUBMISSIONS))
    if progress == "not_started":
        return (
            "v.manual_applied_at IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=v.id) "
            "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id) "
            "AND NOT EXISTS(SELECT 1 FROM auto_application_attempts a WHERE a.vacancy_id=v.id "
            "AND a.status IN ('queued','preparing','regenerating','needs_confirmation'))"
        )
    if progress == "draft_ready":
        return (
            "v.manual_applied_at IS NULL "
            "AND EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=v.id) "
            "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id)"
        )
    if progress == "applied":
        return (
            f"(v.manual_applied_at IS NOT NULL OR EXISTS(SELECT 1 FROM submissions s "
            f"WHERE s.vacancy_id=v.id AND s.status IN ({confirmed})))"
        )
    if progress == "attention":
        return (
            f"EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id "
            f"AND s.status IN ({uncertain}))"
        )
    if progress == "preparing":
        return (
            "EXISTS(SELECT 1 FROM auto_application_attempts a WHERE a.vacancy_id=v.id "
            "AND a.status IN ('queued','preparing','regenerating')) "
            "AND NOT EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=v.id)"
        )
    if progress == "needs_confirmation":
        return (
            "EXISTS(SELECT 1 FROM auto_application_attempts a WHERE a.vacancy_id=v.id "
            "AND a.status='needs_confirmation' AND a.draft_id IS NULL)"
        )
    raise ValueError(f"Unsupported application progress filter: {progress}")


def enrich_jobs(db: Database, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    identifiers = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in identifiers)

    draft_rows = db.all(
        "SELECT id,vacancy_id,status,updated_at,seen_at FROM application_drafts "
        f"WHERE vacancy_id IN ({placeholders}) ORDER BY updated_at DESC",
        tuple(identifiers),
    )
    drafts: dict[str, dict[str, Any]] = {}
    for item in draft_rows:
        drafts.setdefault(item["vacancy_id"], item)

    attempt_rows = db.all(
        "SELECT vacancy_id,status,draft_id,detail,requested_by,updated_at FROM auto_application_attempts "
        f"WHERE vacancy_id IN ({placeholders}) ORDER BY updated_at DESC",
        tuple(identifiers),
    )
    attempts: dict[str, dict[str, Any]] = {}
    for item in attempt_rows:
        attempts.setdefault(item["vacancy_id"], item)

    submission_rows = db.all(
        "SELECT vacancy_id,status,sent_at,updated_at FROM submissions "
        f"WHERE vacancy_id IN ({placeholders}) ORDER BY sent_at DESC,updated_at DESC",
        tuple(identifiers),
    )
    submissions: dict[str, dict[str, Any]] = {}
    confirmed_vacancies: set[str] = set()
    for item in submission_rows:
        submissions.setdefault(item["vacancy_id"], item)
        if item["status"] in CONFIRMED_SUBMISSIONS:
            confirmed_vacancies.add(item["vacancy_id"])

    for row in rows:
        submission = submissions.get(row["id"])
        draft = drafts.get(row["id"])
        attempt = attempts.get(row["id"])
        status = submission["status"] if submission else None
        if status in UNCERTAIN_SUBMISSIONS:
            progress = "attention"
        elif row["id"] in confirmed_vacancies:
            progress = "applied"
        elif row.get("manual_applied_at"):
            progress = "applied_external"
        elif attempt and attempt["status"] in ("queued", "preparing", "regenerating"):
            progress = "preparing"
        elif attempt and attempt["status"] == "needs_confirmation":
            progress = "needs_confirmation"
        elif draft:
            progress = "draft_ready"
        else:
            progress = "not_started"
        row["application_progress"] = progress
        row["latest_submission_status"] = status
        row["has_confirmed_submission"] = row["id"] in confirmed_vacancies
        row["latest_submission_at"] = submission["sent_at"] if submission else None
        row["latest_draft_status"] = draft["status"] if draft else None
        row["latest_draft_id"] = draft["id"] if draft else None
        row["latest_draft_seen_at"] = draft.get("seen_at") if draft else None
        row["preparation_status"] = attempt["status"] if attempt else None
        row["preparation_detail"] = attempt["detail"] if attempt else None
        row["preparation_requested_by"] = attempt["requested_by"] if attempt else None
        if not row.get("seen_at") and draft and draft.get("seen_at"):
            row["seen_at"] = draft["seen_at"]
        row["read_state"] = "seen" if row.get("seen_at") else "unseen"
    return rows
