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
            "UPDATE vacancies SET decision_state=?,snoozed_until=?,state=?,updated_at=? WHERE id=?",
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
            "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id)"
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
    raise ValueError(f"Unsupported application progress filter: {progress}")


def enrich_jobs(db: Database, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    identifiers = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in identifiers)

    draft_rows = db.all(
        "SELECT id,vacancy_id,status,updated_at FROM application_drafts "
        f"WHERE vacancy_id IN ({placeholders}) ORDER BY updated_at DESC",
        tuple(identifiers),
    )
    drafts: dict[str, dict[str, Any]] = {}
    for item in draft_rows:
        drafts.setdefault(item["vacancy_id"], item)

    submission_rows = db.all(
        "SELECT vacancy_id,status,sent_at,updated_at FROM submissions "
        f"WHERE vacancy_id IN ({placeholders}) ORDER BY sent_at DESC,updated_at DESC",
        tuple(identifiers),
    )
    submissions: dict[str, dict[str, Any]] = {}
    for item in submission_rows:
        submissions.setdefault(item["vacancy_id"], item)

    for row in rows:
        submission = submissions.get(row["id"])
        draft = drafts.get(row["id"])
        status = submission["status"] if submission else None
        if status in CONFIRMED_SUBMISSIONS:
            progress = "applied"
        elif status in UNCERTAIN_SUBMISSIONS:
            progress = "attention"
        elif row.get("manual_applied_at"):
            progress = "applied_external"
        elif draft:
            progress = "draft_ready"
        else:
            progress = "not_started"
        row["application_progress"] = progress
        row["latest_submission_status"] = status
        row["latest_submission_at"] = submission["sent_at"] if submission else None
        row["latest_draft_status"] = draft["status"] if draft else None
        row["latest_draft_id"] = draft["id"] if draft else None
        row["read_state"] = "seen" if row.get("seen_at") else "unseen"
    return rows
