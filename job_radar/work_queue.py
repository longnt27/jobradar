"""A read-only view of the work the background workers will actually perform."""

from __future__ import annotations

from .auto_apply import AutoApplyManager
from .db import Database
from .matching import MatchManager
from .scanner import ScanManager


def work_queue(db: Database, scans: ScanManager, matching: MatchManager,
               drafts: AutoApplyManager) -> dict:
    source_ids = set(scans.active) | {source_id for source_id, _ in scans.pending}
    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        sources = {row["id"]: row for row in db.all(
            f"SELECT id,name,kind,url FROM sources WHERE id IN ({placeholders})", tuple(source_ids))}
        running_scans = {row["source_id"]: row["started_at"] for row in db.all(
            f"SELECT source_id,MAX(started_at) AS started_at FROM scan_runs "
            f"WHERE status='running' AND source_id IN ({placeholders}) GROUP BY source_id", tuple(source_ids))}
    else:
        sources, running_scans = {}, {}
    scan_active = [{**sources[source_id], "started_at": running_scans.get(source_id)}
                   for source_id in scans.active if source_id in sources]
    scan_active.sort(key=lambda row: row["started_at"] or "")
    scan_waiting = [{**sources[source_id], "position": position, "requested_by": "you" if manual else "schedule"}
                    for position, (source_id, manual) in enumerate(scans.pending, 1) if source_id in sources]

    analysis_active = db.all(
        "SELECT id,title,company,analysis_stage AS stage,first_seen_at FROM vacancies "
        "WHERE analysis_status='running' ORDER BY first_seen_at DESC")
    analysis_waiting = db.all(
        "SELECT id,title,company,first_seen_at FROM vacancies WHERE analysis_status='pending' "
        "ORDER BY first_seen_at DESC")
    analysis_failed = db.all(
        "SELECT id,title,company,analysis_error AS detail FROM vacancies "
        "WHERE analysis_status='failed' ORDER BY updated_at DESC")
    for position, row in enumerate(analysis_waiting, 1):
        row["position"] = position

    draft_config = drafts.config()
    draft_active = db.all(
        "SELECT a.vacancy_id AS id,a.draft_id,v.title,v.company,v.score,a.status AS stage,a.updated_at "
        "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
        "WHERE a.status IN ('preparing','regenerating') ORDER BY a.updated_at")
    explicit_ready = db.all(
        "SELECT a.vacancy_id AS id,a.draft_id,v.title,v.company,v.score,a.created_at,'queued' AS stage "
        "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
        "WHERE a.status='queued' AND v.analysis_status='done' ORDER BY a.created_at")
    implicit_ready = db.all(
        "SELECT v.id,NULL AS draft_id,v.title,v.company,v.score,v.first_seen_at AS created_at,'queued' AS stage "
        "FROM vacancies v WHERE v.analysis_status='done' AND v.score>=? AND v.state='new' "
        "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm') "
        "AND NOT EXISTS(SELECT 1 FROM auto_application_attempts a WHERE a.vacancy_id=v.id) "
        "ORDER BY v.score DESC,v.first_seen_at DESC", (draft_config["threshold"],)) if draft_config["enabled"] else []
    waiting_for_score = db.all(
        "SELECT a.vacancy_id AS id,a.draft_id,v.title,v.company,v.score,a.created_at,v.analysis_status AS stage "
        "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
        "WHERE a.status='queued' AND v.analysis_status IN ('pending','running') "
        "ORDER BY a.created_at")
    draft_blocked = db.all(
        "SELECT a.vacancy_id AS id,a.draft_id,v.title,v.company,v.score,"
        "v.analysis_status AS stage,v.analysis_error AS detail "
        "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
        "WHERE a.status='queued' AND v.analysis_status NOT IN ('done','pending','running') "
        "ORDER BY a.created_at")
    draft_waiting = explicit_ready + implicit_ready + waiting_for_score
    for position, row in enumerate(draft_waiting, 1):
        row["position"] = position
        row["waiting_for_score"] = position > len(explicit_ready) + len(implicit_ready)
    review_ready = db.one(
        "SELECT COUNT(*) AS count FROM auto_application_attempts "
        "WHERE status IN ('awaiting_review','needs_review')")["count"]

    return {
        "scans": {"active": scan_active, "waiting": scan_waiting},
        "analysis": {"active": analysis_active, "waiting": analysis_waiting, "failed": analysis_failed,
                     "model": db.get_setting("matching_model", ""), "service_error": matching.service_error},
        "drafts": {"active": draft_active, "waiting": draft_waiting, "blocked": draft_blocked,
                   "enabled": draft_config["enabled"], "threshold": draft_config["threshold"],
                   "review_ready": review_ready},
    }
