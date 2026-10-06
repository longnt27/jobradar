"""Home prioritization built from canonical product state.

Home is an integration surface. It ranks already-defined job, application,
submission, and discovery state without inventing parallel lifecycle concepts.
"""

from __future__ import annotations

import json
from typing import Any

from .db import Database
from .job_inbox import application_filter_sql
from .search_intent import fit_summary


USER_PRIORITY = {
    "submission_uncertain": 120,
    "application_review": 110,
    "application_confirmation": 105,
    "shortlisted_to_prepare": 100,
    "strong_unseen_job": 90,
    "unseen_job": 75,
    "track_application": 55,
}


def _route(tab: str, **params: str) -> dict[str, Any]:
    return {"tab": tab, "params": {key: value for key, value in params.items() if value}}


def _priority_item(
    *,
    kind: str,
    title: str,
    subtitle: str,
    priority: int,
    route: dict[str, Any],
    identifier: str | None = None,
    tone: str = "neutral",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": identifier,
        "title": title,
        "subtitle": subtitle,
        "priority": priority,
        "route": route,
        "tone": tone,
        "occurred_at": occurred_at,
    }


def _strong_jobs(db: Database, threshold: int, preferences: dict) -> list[dict[str, Any]]:
    rows = db.all(
        "SELECT id,title,company,score,score_detail,seen_at,first_seen_at "
        "FROM vacancies WHERE decision_state='undecided' AND snoozed_until IS NULL "
        "AND analysis_status='done' AND score>=? "
        "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=vacancies.employer_id "
        "AND e.coverage_status='excluded_hcm') "
        "ORDER BY score DESC,first_seen_at DESC",
        (threshold,),
    )
    strong = []
    for row in rows:
        try:
            detail = json.loads(row.get("score_detail") or "{}")
        except (TypeError, ValueError):
            detail = {}
        summary = fit_summary(row.get("score"), detail, preferences)
        if summary.get("fit_class") == "strong":
            row.update(summary)
            strong.append(row)
    return strong


def build_home_dashboard(
    db: Database,
    discovery: dict[str, Any],
    threshold: int,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    preferences = db.get_setting("search_intent", {}) or {}
    strong_jobs = _strong_jobs(db, threshold, preferences)

    counts = {
        "unseen_jobs": db.one(
            "SELECT COUNT(*) AS count FROM vacancies WHERE seen_at IS NULL "
            "AND decision_state='undecided' AND snoozed_until IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=vacancies.employer_id "
            "AND e.coverage_status='excluded_hcm')"
        )["count"],
        "strong_matches": len(strong_jobs),
        "drafts_to_review": db.one(
            "SELECT COUNT(*) AS count FROM auto_application_attempts "
            "WHERE status IN ('awaiting_review','needs_review') AND draft_id IS NOT NULL"
        )["count"],
        "submission_uncertain": db.one(
            "SELECT COUNT(*) AS count FROM auto_application_attempts "
            "WHERE status='submission_uncertain' AND draft_id IS NOT NULL"
        )["count"],
        "preparation_confirmation": db.one(
            "SELECT COUNT(*) AS count FROM auto_application_attempts "
            "WHERE status='needs_confirmation'"
        )["count"],
        "ready_to_prepare": db.one(
            "SELECT COUNT(*) AS count FROM vacancies v WHERE v.decision_state='shortlisted' "
            "AND v.snoozed_until IS NULL AND " + application_filter_sql("not_started")
        )["count"],
        "tracking": db.one(
            "SELECT COUNT(*) AS count FROM vacancies v WHERE v.recruiting_outcome='none' "
            "AND (v.manual_applied_at IS NOT NULL OR EXISTS("
            "SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id "
            "AND s.status IN ('sent_confirmed','submitted_confirmed')))"
        )["count"],
    }

    items: list[dict[str, Any]] = []

    uncertain = db.all(
        "SELECT a.draft_id AS id,a.updated_at,v.title,v.company FROM auto_application_attempts a "
        "JOIN vacancies v ON v.id=a.vacancy_id "
        "WHERE a.status='submission_uncertain' AND a.draft_id IS NOT NULL "
        "ORDER BY a.updated_at DESC LIMIT 20"
    )
    for row in uncertain:
        items.append(_priority_item(
            kind="submission_uncertain",
            identifier=row["id"],
            title=row["title"],
            subtitle=f"{row['company']} · Confirm what happened before retrying",
            priority=USER_PRIORITY["submission_uncertain"],
            route=_route("applications", view="drafts", draft=row["id"]),
            tone="danger",
            occurred_at=row["updated_at"],
        ))

    reviews = db.all(
        "SELECT a.draft_id AS id,a.status,a.updated_at,v.title,v.company FROM auto_application_attempts a "
        "JOIN vacancies v ON v.id=a.vacancy_id "
        "WHERE a.status IN ('awaiting_review','needs_review') AND a.draft_id IS NOT NULL "
        "ORDER BY CASE a.status WHEN 'needs_review' THEN 0 ELSE 1 END,a.updated_at DESC LIMIT 30"
    )
    for row in reviews:
        label = "Needs changes before sending" if row["status"] == "needs_review" else "Ready for your review"
        items.append(_priority_item(
            kind="application_review",
            identifier=row["id"],
            title=row["title"],
            subtitle=f"{row['company']} · {label}",
            priority=USER_PRIORITY["application_review"] + (2 if row["status"] == "needs_review" else 0),
            route=_route("applications", view="drafts", draft=row["id"]),
            tone="warning" if row["status"] == "needs_review" else "info",
            occurred_at=row["updated_at"],
        ))

    confirmations = db.all(
        "SELECT a.vacancy_id AS id,a.updated_at,v.title,v.company FROM auto_application_attempts a "
        "JOIN vacancies v ON v.id=a.vacancy_id WHERE a.status='needs_confirmation' "
        "ORDER BY a.updated_at DESC LIMIT 20"
    )
    for row in confirmations:
        items.append(_priority_item(
            kind="application_confirmation",
            identifier=row["id"],
            title=row["title"],
            subtitle=f"{row['company']} · Confirm the application method",
            priority=USER_PRIORITY["application_confirmation"],
            route=_route("jobs", job=row["id"], inbox="all"),
            tone="warning",
            occurred_at=row["updated_at"],
        ))

    shortlisted = db.all(
        "SELECT v.id,v.title,v.company,v.updated_at FROM vacancies v "
        "WHERE v.decision_state='shortlisted' AND v.snoozed_until IS NULL AND "
        + application_filter_sql("not_started")
        + " ORDER BY v.updated_at DESC LIMIT 30"
    )
    for row in shortlisted:
        items.append(_priority_item(
            kind="shortlisted_to_prepare",
            identifier=row["id"],
            title=row["title"],
            subtitle=f"{row['company']} · Shortlisted · ready to prepare",
            priority=USER_PRIORITY["shortlisted_to_prepare"],
            route=_route("jobs", job=row["id"], decision="shortlisted", application="not_started", inbox="all"),
            tone="info",
            occurred_at=row["updated_at"],
        ))

    for job in strong_jobs:
        if job.get("seen_at"):
            continue
        items.append(_priority_item(
            kind="strong_unseen_job",
            identifier=job["id"],
            title=job["title"],
            subtitle=f"{job['company']} · Strong match · {job['score']}/100",
            priority=USER_PRIORITY["strong_unseen_job"] + min(9, max(0, int(job["score"] or 0) - threshold) // 2),
            route=_route("jobs", job=job["id"], inbox="unseen"),
            tone="success",
            occurred_at=job["first_seen_at"],
        ))

    strong_ids = {job["id"] for job in strong_jobs}
    unseen = db.all(
        "SELECT id,title,company,score,first_seen_at FROM vacancies "
        "WHERE seen_at IS NULL AND decision_state='undecided' AND snoozed_until IS NULL "
        "AND analysis_status='done' "
        "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=vacancies.employer_id "
        "AND e.coverage_status='excluded_hcm') "
        "ORDER BY first_seen_at DESC LIMIT 30"
    )
    for row in unseen:
        if row["id"] in strong_ids:
            continue
        items.append(_priority_item(
            kind="unseen_job",
            identifier=row["id"],
            title=row["title"],
            subtitle=f"{row['company']} · New job to triage"
            + (f" · {row['score']}/100" if row.get("score") is not None else ""),
            priority=USER_PRIORITY["unseen_job"],
            route=_route("jobs", job=row["id"], inbox="unseen"),
            tone="neutral",
            occurred_at=row["first_seen_at"],
        ))

    tracking = db.all(
        "SELECT v.id,v.title,v.company,v.updated_at FROM vacancies v "
        "WHERE v.recruiting_outcome='none' AND (v.manual_applied_at IS NOT NULL OR EXISTS("
        "SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id "
        "AND s.status IN ('sent_confirmed','submitted_confirmed'))) "
        "ORDER BY v.updated_at DESC LIMIT 20"
    )
    for row in tracking:
        items.append(_priority_item(
            kind="track_application",
            identifier=row["id"],
            title=row["title"],
            subtitle=f"{row['company']} · Applied · track the recruiting outcome",
            priority=USER_PRIORITY["track_application"],
            route=_route("jobs", job=row["id"], inbox="all"),
            tone="neutral",
            occurred_at=row["updated_at"],
        ))

    items.sort(
        key=lambda item: (item["priority"], item.get("occurred_at") or ""),
        reverse=True,
    )

    analysis = {
        "pending": db.one(
            "SELECT COUNT(*) AS count FROM vacancies WHERE analysis_status IN ('pending','running')"
        )["count"],
        "failed": db.one(
            "SELECT COUNT(*) AS count FROM vacancies WHERE analysis_status='failed'"
        )["count"],
    }
    discovery_degraded = discovery.get("level") in {"degraded", "limited", "unknown"}
    analysis_degraded = bool(analysis["failed"] or analysis["pending"])
    health_degraded = discovery_degraded or analysis_degraded

    health = {
        "degraded": health_degraded,
        "discovery_level": discovery.get("level", "unknown"),
        "discovery_label": discovery.get("label", "Discovery status unavailable"),
        "analysis": analysis,
        "message": (
            "Your radar needs attention before an empty inbox can be trusted."
            if health_degraded and analysis["failed"] else
            "Some jobs are still being reviewed, so the current empty inbox is not final."
            if analysis["pending"] else
            "Discovery coverage needs attention before an empty inbox can be trusted."
            if discovery_degraded else
            "Discovery and match review look healthy."
        ),
        "route": _route("sources") if discovery_degraded else _route("queue"),
    }

    visible = items[:limit]
    user_action_count = sum(
        counts[key] for key in (
            "unseen_jobs", "drafts_to_review", "submission_uncertain",
            "preparation_confirmation", "ready_to_prepare", "tracking",
        )
    )
    if visible:
        state = "action_needed"
        headline = f"{len(items)} priorities for this visit"
    elif health_degraded:
        state = "radar_degraded"
        headline = "No user action is queued, but the radar needs attention"
    else:
        state = "clear"
        headline = "You're caught up"

    stages = [
        {
            "key": "triage",
            "label": "Triage new jobs",
            "count": counts["unseen_jobs"],
            "detail": "Open unseen jobs and decide shortlist, later, or ignore.",
            "route": _route("jobs", inbox="unseen"),
        },
        {
            "key": "prepare",
            "label": "Prepare applications",
            "count": counts["ready_to_prepare"],
            "detail": "Open shortlisted jobs that do not have an application draft yet.",
            "route": _route("jobs", decision="shortlisted", application="not_started", inbox="all"),
        },
        {
            "key": "review",
            "label": "Review and send",
            "count": counts["drafts_to_review"] + counts["submission_uncertain"],
            "detail": "Review prepared applications and resolve uncertain submissions.",
            "route": _route("applications", view="drafts"),
        },
        {
            "key": "track",
            "label": "Track outcomes",
            "count": counts["tracking"],
            "detail": "Record interview, offer, or rejection outcomes for applied jobs.",
            "route": _route("jobs", application="applied", outcome="none", inbox="all"),
        },
    ]

    return {
        "state": state,
        "headline": headline,
        "counts": counts,
        "priority_items": visible,
        "priority_total": len(items),
        "user_action_count": user_action_count,
        "health": health,
        "stages": stages,
        "routes": {
            "all_priorities": _route("jobs", inbox="unseen"),
            "strong_matches": _route("jobs", score=str(threshold), decision="undecided", inbox="all"),
            "applications": _route("applications", view="drafts"),
        },
    }
