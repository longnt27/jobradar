"""App-wide Home totals and routes into the underlying workflows."""

from __future__ import annotations

from typing import Any

from .db import Database
from .job_inbox import APPLICATION_REVIEW_SQL, application_filter_sql


VISIBLE_JOB = (
    "NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id "
    "AND e.coverage_status='excluded_hcm')"
)


def _route(tab: str, **params: str) -> dict[str, Any]:
    return {"tab": tab, "params": {key: value for key, value in params.items() if value}}


def _count(db: Database, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(db.one(sql, params)["count"])


def build_home_dashboard(
    db: Database,
    discovery: dict[str, Any],
    threshold: int,
) -> dict[str, Any]:
    counts = {
        "total_jobs": _count(db, f"SELECT COUNT(*) AS count FROM vacancies v WHERE {VISIBLE_JOB}"),
        "strong_matches": _count(
            db,
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {VISIBLE_JOB} "
            "AND v.analysis_status='done' AND v.score>=? "
            "AND (json_extract(v.score_detail,'$.method') IS NOT 'deterministic' OR "
            "COALESCE(json_extract(v.score_detail,'$.confidence'),0)>=50)",
            (threshold,),
        ),
        "applications_drafted": _count(db, "SELECT COUNT(*) AS count FROM application_drafts"),
        "applications_sent": _count(
            db,
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {VISIBLE_JOB} "
            f"AND {application_filter_sql('applied')}"
        ),
        "unseen_jobs": _count(
            db,
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {VISIBLE_JOB} "
            "AND v.seen_at IS NULL AND v.decision_state='undecided' AND v.snoozed_until IS NULL"
        ),
        "drafts_to_review": _count(
            db, "SELECT COUNT(*) AS count FROM application_drafts d "
            "LEFT JOIN auto_application_attempts a ON a.vacancy_id=d.vacancy_id "
            f"WHERE ({APPLICATION_REVIEW_SQL}) IN ('awaiting_review','needs_review')"
        ),
        "submission_uncertain": _count(
            db, "SELECT COUNT(*) AS count FROM application_drafts d "
            "LEFT JOIN auto_application_attempts a ON a.vacancy_id=d.vacancy_id "
            f"WHERE ({APPLICATION_REVIEW_SQL})='submission_uncertain'"
        ),
        "ready_to_prepare": _count(
            db,
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {VISIBLE_JOB} "
            "AND v.decision_state='shortlisted' AND v.snoozed_until IS NULL "
            f"AND {application_filter_sql('not_started')}"
        ),
        "tracking": _count(
            db,
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {VISIBLE_JOB} "
            "AND v.recruiting_outcome='none' "
            f"AND {application_filter_sql('applied')}"
        ),
    }

    analysis = {
        "pending": _count(db, "SELECT COUNT(*) AS count FROM vacancies WHERE analysis_status IN ('pending','running')"),
        "failed": _count(db, "SELECT COUNT(*) AS count FROM vacancies WHERE analysis_status='failed'"),
    }
    discovery_degraded = discovery.get("level") in {"degraded", "limited", "unknown"}

    return {
        "counts": counts,
        "health": {
            "degraded": discovery_degraded or bool(analysis["failed"] or analysis["pending"]),
            "analysis": analysis,
        },
        "stages": [
            {
                "key": "triage",
                "label": "Triage new jobs",
                "count": counts["unseen_jobs"],
                "detail": "Open unseen jobs and decide shortlist, later, or ignore.",
                "route": _route("jobs", inbox="unseen", fit="all"),
            },
            {
                "key": "prepare",
                "label": "Prepare applications",
                "count": counts["ready_to_prepare"],
                "detail": "Review shortlisted jobs without a draft, then prepare an application.",
                "route": _route("jobs", decision="shortlisted", application="not_started", inbox="all", fit="all"),
            },
            {
                "key": "review",
                "label": "Review and send",
                "count": counts["drafts_to_review"] + counts["submission_uncertain"],
                "detail": "Review prepared applications and resolve uncertain submissions.",
                "route": _route("applications", view="drafts", review="actionable"),
            },
            {
                "key": "track",
                "label": "Update application status",
                "count": counts["tracking"],
                "detail": "Record interview, offer, or rejection outcomes for sent applications.",
                "route": _route("jobs", application="applied", outcome="none", inbox="all", fit="all"),
            },
        ],
        "routes": {
            "total_jobs": _route("jobs", inbox="all", fit="all"),
            "strong_matches": _route("jobs", score=str(threshold), inbox="all", fit="all"),
            "applications_drafted": _route("applications", view="drafts"),
            "applications_sent": _route("jobs", application="applied", inbox="all", fit="all"),
        },
    }
