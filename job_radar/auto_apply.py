"""Opt-in automatic drafting for locally scored jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncContextManager, Callable

import httpx
from playwright.async_api import Error as PlaywrightError

from .apply import inspect_form, send_application, send_readiness
from .automation_policy import (
    automation_eligibility,
    automation_policy,
    daily_auto_drafts_used,
    daily_review_notifications_used,
    normalize_automation_policy,
)
from .db import Database, new_id, now
from .drafting import get_draft, prepare_draft, regenerate_draft, set_discovered_linkedin_destination, set_discovered_web_destination, set_unavailable_linkedin_destination
from .linkedin_application import discover_linkedin_apply
from .notifications import telegram_config, telegram_mode_enabled, telegram_quiet_now
from .preparation import preparation_preflight
from .review_telegram import _post, send_preparation_notice, send_review_packet
from .settings import Settings
from .search_intent import fit_summary, normalize_search_intent


log = logging.getLogger(__name__)
BROWSER_LOCK_WAIT_SECONDS = 30


@asynccontextmanager
async def _no_browser_priority():
    yield

EXISTING_MATCHES_SQL = (
    "FROM vacancies v LEFT JOIN auto_application_attempts a ON a.vacancy_id=v.id "
    "WHERE v.decision_state IN ('undecided','shortlisted') "
    "AND (a.vacancy_id IS NULL OR a.status='skipped') "
    "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm') "
    "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id) "
    "AND NOT EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=v.id)"
)


def _safe_attachments(fields: list[dict]) -> dict[str, dict]:
    files = [field for field in fields if field["type"] == "file"]
    resume_fields = [field for field in files if re.search(
        r"\b(resume|cv|curriculum vitae)\b", f"{field['name']} {field['label']}", re.I)]
    assignments = {}
    for field in files:
        if len(resume_fields) == 1 and field is resume_fields[0]:
            assignments[str(field["index"])] = {"kind": "resume"}
        elif not field["required"] and field not in resume_fields:
            assignments[str(field["index"])] = {"kind": "none"}
    return assignments


class AutoApplyManager:
    def __init__(self, db: Database, settings: Settings, browser_lock: asyncio.Lock,
                 priority_browser: Callable[[], AsyncContextManager[None]] | None = None):
        self.db = db
        self.settings = settings
        self.browser_lock = browser_lock
        self.priority_browser = priority_browser or _no_browser_priority
        self.task: asyncio.Task | None = None
        self.telegram_task: asyncio.Task | None = None
        self.wake_event = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    def config(self) -> dict:
        return automation_policy(self.db)

    def status(self, include_preview: bool = True) -> dict:
        config = self.config()
        counts = {row["status"]: row["count"] for row in self.db.all(
            "SELECT status,COUNT(*) AS count FROM auto_application_attempts "
            "WHERE requested_by='automation' GROUP BY status")}
        recent = self.db.all(
            "SELECT a.vacancy_id,a.status,a.draft_id,a.detail,a.updated_at,a.requested_by,"
            "a.requested_provider,a.prepare_anyway,v.title,v.company,v.score,v.analysis_status "
            "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
            "WHERE a.status!='skipped' ORDER BY a.updated_at DESC LIMIT 20")
        used = daily_auto_drafts_used(self.db)
        review_used = daily_review_notifications_used(self.db)
        summary = {
            **config,
            "counts": counts,
            "daily_auto_drafts_used": used,
            "daily_auto_drafts_remaining": max(0, config["max_auto_drafts_per_day"] - used),
            "daily_review_notifications_used": review_used,
            "daily_review_notifications_remaining": max(
                0, config["max_review_notifications_per_day"] - review_used
            ),
            "recent": recent,
        }
        if not include_preview:
            return {
                **summary,
                "eligible_existing": None,
                "waiting_existing": None,
                "highest_existing_score": None,
            }
        preview_policy = {**config, "enabled": True}
        highest_row = self.db.one(
            "SELECT MAX(v.score) AS score " + EXISTING_MATCHES_SQL
            + " AND v.analysis_status='done'"
        )
        highest_existing_score = highest_row["score"] if highest_row else None
        candidates = self.db.all(
            "SELECT v.* " + EXISTING_MATCHES_SQL
            + " AND (v.analysis_status IN ('pending','running') OR (v.analysis_status='done' AND v.score>=?))"
            + " AND datetime(v.first_seen_at)>=datetime('now',?)"
            + " AND v.snoozed_until IS NULL AND v.manual_applied_at IS NULL "
            + "AND v.recruiting_outcome='none'"
            + " ORDER BY v.score DESC,v.first_seen_at DESC",
            (config["threshold"], f"-{config['max_job_age_days']} days"),
        )
        preferences = normalize_search_intent(self.db.get_setting("search_intent", {}))
        excluded_employer_ids = {row["id"] for row in self.db.all(
            "SELECT id FROM employers WHERE coverage_status='excluded_hcm'")}
        observations_by_job: dict[str, list[dict]] = {}
        if config["require_verified_destination"]:
            for offset in range(0, len(candidates), 500):
                ids = [item["id"] for item in candidates[offset:offset + 500]]
                if not ids:
                    continue
                placeholders = ",".join("?" for _ in ids)
                rows = self.db.all(
                    "SELECT vo.vacancy_id,s.kind AS source_kind,o.url AS observation_url,"
                    "o.raw_text,o.payload,o.last_seen_at "
                    "FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id "
                    "JOIN sources s ON s.id=o.source_id "
                    f"WHERE vo.vacancy_id IN ({placeholders}) "
                    "ORDER BY o.last_seen_at DESC",
                    tuple(ids),
                )
                for row in rows:
                    observations_by_job.setdefault(row["vacancy_id"], []).append(row)
        eligible_existing = 0
        waiting_existing = 0
        for candidate in candidates:
            if candidate["analysis_status"] == "done":
                if automation_eligibility(
                    self.db, candidate["id"], policy=preview_policy,
                    require_analysis=True, check_daily_limit=False,
                    check_existing_artifacts=False, job_data=candidate,
                    preferences_data=preferences, excluded_employer_ids=excluded_employer_ids,
                    observations=observations_by_job.get(candidate["id"], []),
                )["eligible"]:
                    eligible_existing += 1
            elif candidate["analysis_status"] in ("pending", "running"):
                if automation_eligibility(
                    self.db, candidate["id"], policy=preview_policy,
                    require_analysis=False, check_daily_limit=False,
                    check_existing_artifacts=False, job_data=candidate,
                    preferences_data=preferences, excluded_employer_ids=excluded_employer_ids,
                    observations=observations_by_job.get(candidate["id"], []),
                )["eligible"]:
                    waiting_existing += 1
        return {
            **summary,
            "eligible_existing": eligible_existing,
            "waiting_existing": waiting_existing,
            "highest_existing_score": highest_existing_score,
        }

    def configure(self, enabled: bool, threshold: int | None = None, policy: dict | None = None) -> dict:
        explicit_threshold = threshold is not None
        threshold = self.config()["threshold"] if threshold is None else threshold
        if not 0 <= threshold <= 100:
            raise ValueError("Threshold must be between 0 and 100")
        previous = self.config()
        if enabled and not previous["enabled"]:
            # Existing jobs require the explicit "Include existing jobs" action.
            with self.db.connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO auto_application_attempts(vacancy_id,status,detail,created_at,updated_at) "
                    "SELECT id,'skipped','Found before automatic draft preparation was enabled',?,? FROM vacancies",
                    (now(), now()),
                )
        intent = normalize_search_intent(
            self.db.get_setting("search_intent", {}) or {"strong_match_threshold": threshold}
        )
        intent["strong_match_threshold"] = threshold
        if explicit_threshold:
            intent["preference_modes"]["strong_match_threshold"] = "custom"
        self.db.set_setting("search_intent", intent)
        merged = {**self.db.get_setting("auto_apply", {}), **(policy or {}), "enabled": enabled}
        normalized = normalize_automation_policy(merged, threshold=threshold)
        self.db.set_setting("auto_apply", {key: value for key, value in normalized.items() if key != "threshold"})
        self.wake()
        return self.status(include_preview=False)

    def queue_existing(self) -> dict:
        config = self.config()
        if not config["enabled"]:
            raise ValueError("Enable automatic draft preparation first")
        jobs = self.db.all(
            "SELECT v.id,v.analysis_status " + EXISTING_MATCHES_SQL
            + " AND v.analysis_status IN ('done','pending','running') "
            "ORDER BY v.score DESC,v.first_seen_at DESC"
        )
        eligible = []
        for job in jobs:
            result = automation_eligibility(
                self.db,
                job["id"],
                policy=config,
                require_analysis=job["analysis_status"] == "done",
                check_daily_limit=False,
            )
            if result["eligible"]:
                eligible.append(job)
        timestamp = now()
        with self.db.connection() as conn:
            for job in eligible:
                detail = (
                    "Waiting for local match review before draft preparation"
                    if job["analysis_status"] != "done"
                    else "Queued by the saved automation policy"
                )
                conn.execute(
                    "INSERT INTO auto_application_attempts("
                    "vacancy_id,status,detail,requested_by,created_at,updated_at"
                    ") VALUES(?,'queued',?,'automation',?,?) "
                    "ON CONFLICT(vacancy_id) DO UPDATE SET status='queued',detail=excluded.detail,"
                    "requested_by='automation',updated_at=excluded.updated_at "
                    "WHERE auto_application_attempts.status='skipped'",
                    (job["id"], detail, timestamp, timestamp),
                )
        self.wake()
        return {"queued": len(eligible)}

    def queue_manual(self, job_id: str, provider: str, prepare_anyway: bool = False) -> dict:
        job = self.db.one("SELECT id,title,company FROM vacancies WHERE id=?", (job_id,))
        if not job:
            raise KeyError("Job not found")
        if self.db.one("SELECT id FROM submissions WHERE vacancy_id=? LIMIT 1", (job_id,)):
            raise ValueError("An application submission already exists for this job")
        existing_draft = self.db.one(
            "SELECT id FROM application_drafts WHERE vacancy_id=? ORDER BY updated_at DESC LIMIT 1",
            (job_id,),
        )
        if existing_draft:
            return {"status": "ready", "draft_id": existing_draft["id"], "already_prepared": True}

        preflight = preparation_preflight(self.db, job_id)
        if preflight["requires_confirmation"] and not prepare_anyway:
            return {"status": "confirmation_required", "preflight": preflight}

        existing = self.db.one(
            "SELECT status,draft_id,requested_by FROM auto_application_attempts WHERE vacancy_id=?",
            (job_id,),
        )
        if existing and existing["status"] in ("sent", "sending", "submission_uncertain"):
            raise ValueError("This application already has a submission in progress or an uncertain outcome")
        if existing and existing["status"] == "queued":
            if existing["requested_by"] != "manual":
                self.db.execute(
                    "UPDATE auto_application_attempts SET requested_by='manual',requested_provider=?,"
                    "preflight_action=?,prepare_anyway=?,detail='Queued by you for application preparation',"
                    "updated_at=? WHERE vacancy_id=?",
                    (
                        provider,
                        json.dumps(preflight["action"], ensure_ascii=False),
                        int(prepare_anyway),
                        now(),
                        job_id,
                    ),
                )
                self.wake()
                return {
                    "status": "queued",
                    "draft_id": existing["draft_id"],
                    "preflight": preflight,
                    "already_queued": False,
                    "promoted_from_automation": True,
                }
            return {
                "status": "queued",
                "draft_id": existing["draft_id"],
                "preflight": preflight,
                "already_queued": True,
            }
        if existing and existing["status"] == "preparing":
            return {
                "status": "preparing",
                "draft_id": existing["draft_id"],
                "preflight": preflight,
                "already_queued": True,
            }

        timestamp = now()
        self.db.execute(
            "INSERT INTO auto_application_attempts("
            "vacancy_id,status,draft_id,review_hash,telegram_status,detail,requested_by,"
            "requested_provider,preflight_action,prepare_anyway,created_at,updated_at"
            ") VALUES(?,'queued',NULL,NULL,'pending',?,'manual',?,?,?, ?,?) "
            "ON CONFLICT(vacancy_id) DO UPDATE SET status='queued',draft_id=NULL,review_hash=NULL,"
            "telegram_status='pending',telegram_error=NULL,telegram_message_id=NULL,detail=excluded.detail,"
            "requested_by='manual',requested_provider=excluded.requested_provider,"
            "preflight_action=excluded.preflight_action,prepare_anyway=excluded.prepare_anyway,"
            "updated_at=excluded.updated_at",
            (
                job_id,
                "Queued by you for application preparation",
                provider,
                json.dumps(preflight["action"], ensure_ascii=False),
                int(prepare_anyway),
                timestamp,
                timestamp,
            ),
        )
        self.wake()
        return {"status": "queued", "preflight": preflight, "already_queued": False}

    def wake(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.wake_event.set)

    def failed_preparations(self) -> list[dict]:
        return self.db.all(
            "SELECT vacancy_id,detail FROM auto_application_attempts a WHERE status='needs_review' "
            "AND draft_id IS NULL AND (detail LIKE 'AI drafting failed:%' "
            "OR detail LIKE '% preparation stopped: % drafting failed:%' "
            "OR detail LIKE '% preparation stopped: % returned an invalid draft:%') "
            "AND NOT EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=a.vacancy_id) "
            "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=a.vacancy_id)"
        )

    def retry_failed_preparations(self) -> int:
        failures = self.failed_preparations()
        for item in failures:
            self.db.execute(
                "UPDATE auto_application_attempts SET status='queued',requested_by='manual',"
                "detail='Retry requested after AI drafting failed',telegram_status='pending',updated_at=? "
                "WHERE vacancy_id=? AND status='needs_review' AND draft_id IS NULL",
                (now(), item["vacancy_id"]),
            )
        if failures:
            self.wake()
        return len(failures)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.wake_event = asyncio.Event()
        stuck = self.db.all("SELECT id,draft_id FROM submissions WHERE status='sending'")
        for submission in stuck:
            detail = "Job Radar restarted while sending. The submission may have completed; verify on the employer site before retrying."
            self.db.execute("UPDATE submissions SET status='submitted_unconfirmed',error=?,updated_at=? WHERE id=?",
                            (detail, now(), submission["id"]))
            self.db.execute("UPDATE application_drafts SET status='submission_uncertain',updated_at=? WHERE id=?",
                            (now(), submission["draft_id"]))
        self.db.execute(
            "UPDATE auto_application_attempts SET status='awaiting_review',"
            "detail='Interrupted before submission started; review and try again',updated_at=? "
            "WHERE status='sending' AND NOT EXISTS("
            "SELECT 1 FROM submissions s WHERE s.draft_id=auto_application_attempts.draft_id "
            "AND s.status IN ('sending','submitted_unconfirmed','sent_confirmed','submitted_confirmed'))",
            (now(),),
        )
        self.db.execute(
            "UPDATE auto_application_attempts SET status='submission_uncertain',telegram_status='pending',"
            "detail='Submission status uncertain after restart; verify on the employer site before retrying',updated_at=? "
            "WHERE status='sending'", (now(),))
        self.db.execute(
            "UPDATE auto_application_attempts SET status='queued',"
            "detail='Application preparation will resume after restart',updated_at=? "
            "WHERE status='preparing' AND draft_id IS NULL", (now(),))
        self.db.execute(
            "UPDATE auto_application_attempts SET status='needs_review',telegram_status='pending',"
            "detail='Job Radar restarted after draft work began; review the saved draft before sending',updated_at=? "
            "WHERE status='regenerating' OR (status='preparing' AND draft_id IS NOT NULL)", (now(),))
        self.task = asyncio.create_task(self._loop())
        self.telegram_task = asyncio.create_task(self._telegram_loop())

    async def stop(self) -> None:
        for task in (self.task, self.telegram_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def _set_status(self, job_id: str, status: str, detail: str = "", draft_id: str | None = None) -> None:
        self.db.execute(
            "UPDATE auto_application_attempts SET status=?,detail=?,draft_id=COALESCE(?,draft_id),updated_at=? WHERE vacancy_id=?",
            (status, detail[:1000], draft_id, now(), job_id),
        )

    def register_review(self, draft: dict) -> None:
        self.db.execute(
            "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,detail,created_at,updated_at) "
            "VALUES(?,'needs_review',?,'Preparing review',?,?) "
            "ON CONFLICT(vacancy_id) DO UPDATE SET status='needs_review',draft_id=excluded.draft_id,"
            "review_hash=NULL,telegram_status='pending',telegram_error=NULL,telegram_message_id=NULL,"
            "detail='Preparing review',updated_at=excluded.updated_at",
            (draft["vacancy_id"], draft["id"], now(), now()),
        )

    def _still_eligible(self, job_id: str) -> bool:
        return automation_eligibility(
            self.db,
            job_id,
            policy=self.config(),
            require_analysis=True,
            check_daily_limit=False,
            check_existing_artifacts=False,
        )["eligible"]

    async def _process(self, job_id: str) -> None:
        job = self.db.one("SELECT * FROM vacancies WHERE id=?", (job_id,))
        if not job:
            self._set_status(job_id, "needs_review", "Job no longer exists.")
            return
        attempt = self.db.one(
            "SELECT requested_by,requested_provider,prepare_anyway FROM auto_application_attempts WHERE vacancy_id=?",
            (job_id,),
        ) or {"requested_by": "automation", "requested_provider": None, "prepare_anyway": 0}
        manual = attempt["requested_by"] == "manual"
        preflight = preparation_preflight(self.db, job_id)
        unverified_opt_in = (
            bool(attempt["prepare_anyway"])
            if manual
            else not self.config()["require_verified_destination"]
        )
        if preflight["requires_confirmation"] and not unverified_opt_in:
            self.db.execute(
                "UPDATE auto_application_attempts SET status='needs_confirmation',detail=?,preflight_action=?,updated_at=? "
                "WHERE vacancy_id=?",
                (
                    preflight["reason"][:1000],
                    json.dumps(preflight["action"], ensure_ascii=False),
                    now(),
                    job_id,
                ),
            )
            return
        prior = self.db.one("SELECT id FROM submissions WHERE vacancy_id=? LIMIT 1", (job_id,))
        if prior:
            self._set_status(job_id, "needs_review", "An application submission already exists for this job.")
            return
        existing = self.db.one("SELECT id FROM application_drafts WHERE vacancy_id=? LIMIT 1", (job_id,))
        if existing:
            self._set_status(job_id, "needs_review", "An application draft already exists. Review it before sending.", existing["id"])
            return
        provider = attempt["requested_provider"] if manual else self.db.get_setting("profile", {}).get("drafting_provider", "")
        if not provider:
            self._set_status(job_id, "needs_review", "Choose an application drafting provider in My profile.")
            return
        draft = await asyncio.to_thread(prepare_draft, self.db, self.settings, job_id, provider)
        self._set_status(job_id, "preparing", "Draft prepared; checking application details", draft["id"])
        if not manual and not self._still_eligible(job_id):
            self._set_status(job_id, "needs_review", "Automatic preparation paused or job score changed.")
            return
        if manual and draft.get("job_source_kind") == "linkedin" and draft["destination"].get("kind") not in {"web", "email"}:
            try:
                async with self.browser_lock:
                    action = await discover_linkedin_apply(self.settings, draft["job_posting_url"])
                if action["kind"] == "web":
                    draft = set_discovered_web_destination(self.db, draft["id"], action["url"])
                elif action["kind"] == "linkedin_easy_apply":
                    draft = set_discovered_linkedin_destination(self.db, draft["id"])
                elif action["kind"] in {"closed", "already_applied"}:
                    draft = set_unavailable_linkedin_destination(self.db, draft["id"], action["detail"])
            except (ValueError, RuntimeError, PlaywrightError) as error:
                self._set_status(job_id, "needs_review", f"Application button inspection needs attention: {error}", draft["id"])
                await self.notify_review(draft["id"])
                return
        if draft["destination"].get("kind") in {"web", "linkedin_easy_apply"}:
            try:
                async with self.browser_lock:
                    draft = await inspect_form(self.db, self.settings, draft["id"])
            except (ValueError, RuntimeError, PlaywrightError) as error:
                self._set_status(job_id, "needs_review", f"Form inspection needs attention: {error}", draft["id"])
                await self.notify_review(draft["id"])
                return
            if draft["destination"].get("kind") == "web":
                attachments = {**draft["form_data"].get("attachments", {}),
                               **_safe_attachments(draft["form_data"].get("fields", []))}
                if attachments:
                    form_data = {**draft["form_data"], "attachments": attachments}
                    self.db.execute("UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
                                    (json.dumps(form_data, ensure_ascii=False), now(), draft["id"]))
                    draft = get_draft(self.db, draft["id"])
        blockers = send_readiness(self.db, self.settings, draft)
        if blockers:
            self._set_status(job_id, "needs_review", "; ".join(blockers), draft["id"])
            await self.notify_review(draft["id"])
            return
        if not manual and not self._still_eligible(job_id):
            self._set_status(job_id, "needs_review", "Automatic preparation paused or job score changed.", draft["id"])
            return
        self._set_status(job_id, "awaiting_review", "Review the complete application before approving.", draft["id"])
        await self.notify_review(draft["id"])

    @staticmethod
    def _same_telegram_destination(left: dict, right: dict) -> bool:
        return (
            left.get("token") == right.get("token")
            and str(left.get("chat_id", "")) == str(right.get("chat_id", ""))
        )

    def _record_notification(
        self,
        vacancy_id: str,
        channel: str,
        *,
        status: str,
        error: str | None = None,
        sent: bool = False,
    ) -> None:
        timestamp = now()
        self.db.execute(
            "INSERT INTO notification_attempts("
            "vacancy_id,channel,status,attempts,last_error,last_attempt_at,sent_at"
            ") VALUES(?,?,?,1,?,?,?) "
            "ON CONFLICT(vacancy_id,channel) DO UPDATE SET status=excluded.status,"
            "attempts=notification_attempts.attempts+1,last_error=excluded.last_error,"
            "last_attempt_at=excluded.last_attempt_at,"
            "sent_at=CASE WHEN excluded.sent_at IS NOT NULL THEN excluded.sent_at "
            "ELSE notification_attempts.sent_at END",
            (vacancy_id, channel, status, error, timestamp, timestamp if sent else None),
        )
        self.db.execute(
            "INSERT INTO notification_events(id,vacancy_id,channel,status,error,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (new_id(), vacancy_id, channel, status, error, timestamp),
        )

    async def notify_review(self, draft_id: str, *, deliver_telegram: bool = True) -> None:
        attempt = self.db.one(
            "SELECT vacancy_id,status,review_hash,telegram_status,telegram_message_id,requested_by "
            "FROM auto_application_attempts WHERE draft_id=?",
            (draft_id,),
        )
        if not attempt or attempt["status"] in ("sent", "sending", "skipped", "preparing", "regenerating"):
            return
        draft = get_draft(self.db, draft_id)
        blockers = send_readiness(self.db, self.settings, draft)
        status = "needs_review" if blockers else "awaiting_review"
        if (
            deliver_telegram and attempt["review_hash"] == draft["package_hash"]
            and attempt["status"] == status
            and attempt["telegram_status"] == "sent"
            and attempt["telegram_message_id"]
        ):
            return
        previous_message_id = attempt["telegram_message_id"]
        self.db.execute(
            "UPDATE auto_application_attempts SET review_hash=?,status=?,detail=?,"
            "telegram_status=?,telegram_message_id=?,telegram_error=NULL,updated_at=? WHERE draft_id=?",
            (
                draft["package_hash"],
                status,
                "; ".join(blockers) if blockers else "Review the complete application before approving.",
                "pending" if deliver_telegram else "web_only",
                previous_message_id if deliver_telegram else None,
                now(),
                draft_id,
            ),
        )
        if not deliver_telegram:
            return
        config = telegram_config(self.settings)
        if not config.get("token") or not config.get("chat_id"):
            self.db.execute(
                "UPDATE auto_application_attempts SET telegram_status='not_configured' WHERE draft_id=?",
                (draft_id,),
            )
            return
        if not telegram_mode_enabled(config, "application_reviews"):
            self.db.execute(
                "UPDATE auto_application_attempts SET telegram_status='disabled',telegram_error=NULL WHERE draft_id=?",
                (draft_id,),
            )
            return
        if telegram_quiet_now(config):
            self.db.execute(
                "UPDATE auto_application_attempts SET telegram_status='deferred',"
                "telegram_error='Deferred during Telegram quiet hours' WHERE draft_id=?",
                (draft_id,),
            )
            return

        policy = self.config()
        review_channel = "telegram_application_review"
        if (
            attempt["requested_by"] == "automation"
            and daily_review_notifications_used(self.db) >= policy["max_review_notifications_per_day"]
        ):
            self.db.execute(
                "UPDATE auto_application_attempts SET telegram_status='deferred_limit',"
                "telegram_error='Daily Telegram review limit reached' WHERE draft_id=?",
                (draft_id,),
            )
            return

        try:
            message_id = await send_review_packet(self.settings, draft, blockers)
            current = telegram_config(self.settings)
            if self._same_telegram_destination(current, config):
                self.db.execute(
                    "UPDATE auto_application_attempts SET telegram_status='sent',telegram_message_id=?,"
                    "telegram_error=NULL WHERE draft_id=? AND review_hash=?",
                    (message_id, draft_id, draft["package_hash"]),
                )
                self._record_notification(
                    attempt["vacancy_id"], review_channel, status="sent", sent=True
                )
                if previous_message_id and previous_message_id != message_id:
                    try:
                        async with httpx.AsyncClient(timeout=10) as client:
                            await _post(client, config["token"], "editMessageText", json={
                                "chat_id": config["chat_id"],
                                "message_id": previous_message_id,
                                "text": "Superseded application review. Use the latest review message before approving or editing.",
                                "reply_markup": {"inline_keyboard": []},
                            })
                    except (httpx.HTTPError, RuntimeError):
                        log.info("Could not invalidate superseded Telegram review %s", previous_message_id)
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
            log.warning("Could not deliver application review %s: %s", draft_id, type(error).__name__)
            current = telegram_config(self.settings)
            if self._same_telegram_destination(current, config):
                message = f"Telegram delivery failed ({type(error).__name__})"
                self.db.execute(
                    "UPDATE auto_application_attempts SET telegram_status='failed',telegram_error=? "
                    "WHERE draft_id=? AND review_hash=?",
                    (message, draft_id, draft["package_hash"]),
                )
                self._record_notification(
                    attempt["vacancy_id"], review_channel, status="failed", error=message
                )

    async def notify_preparation_issue(self, vacancy_id: str) -> None:
        """Tell the user when preparation stopped before a reviewable draft existed."""
        attempt = self.db.one(
            "SELECT a.status,a.draft_id,a.telegram_status,a.requested_by,v.title,v.company "
            "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
            "WHERE a.vacancy_id=?", (vacancy_id,),
        )
        if not attempt or attempt["status"] != "needs_review" or attempt["draft_id"]:
            return
        if attempt["telegram_status"] == "sent":
            return
        config = telegram_config(self.settings)
        if not config.get("token") or not config.get("chat_id"):
            self.db.execute("UPDATE auto_application_attempts SET telegram_status='not_configured' WHERE vacancy_id=?", (vacancy_id,))
            return
        if not telegram_mode_enabled(config, "application_reviews"):
            self.db.execute("UPDATE auto_application_attempts SET telegram_status='disabled' WHERE vacancy_id=?", (vacancy_id,))
            return
        if telegram_quiet_now(config):
            self.db.execute("UPDATE auto_application_attempts SET telegram_status='deferred',"
                            "telegram_error='Deferred during Telegram quiet hours' WHERE vacancy_id=?", (vacancy_id,))
            return
        if (attempt["requested_by"] == "automation" and
                daily_review_notifications_used(self.db) >= self.config()["max_review_notifications_per_day"]):
            self.db.execute("UPDATE auto_application_attempts SET telegram_status='deferred_limit',"
                            "telegram_error='Daily Telegram review limit reached' WHERE vacancy_id=?", (vacancy_id,))
            return
        try:
            message_id = await send_preparation_notice(self.settings, attempt["title"], attempt["company"])
            if self._same_telegram_destination(telegram_config(self.settings), config):
                self.db.execute(
                    "UPDATE auto_application_attempts SET telegram_status='sent',telegram_message_id=?,"
                    "telegram_error=NULL WHERE vacancy_id=? AND status='needs_review' AND draft_id IS NULL",
                    (message_id, vacancy_id),
                )
                self._record_notification(vacancy_id, "telegram_application_review", status="sent", sent=True)
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
            log.warning("Could not deliver preparation notice %s: %s", vacancy_id, type(error).__name__)
            if self._same_telegram_destination(telegram_config(self.settings), config):
                message = f"Telegram delivery failed ({type(error).__name__})"
                self.db.execute(
                    "UPDATE auto_application_attempts SET telegram_status='failed',telegram_error=? "
                    "WHERE vacancy_id=? AND status='needs_review' AND draft_id IS NULL",
                    (message, vacancy_id),
                )
                self._record_notification(vacancy_id, "telegram_application_review", status="failed", error=message)

    async def regenerate(self, draft_id: str, prompt: str, section: str = "all",
                         *, deliver_telegram: bool = True) -> dict:
        attempt = self.db.one("SELECT vacancy_id,status FROM auto_application_attempts WHERE draft_id=?", (draft_id,))
        if not attempt or attempt["status"] in ("sent", "sending", "submission_uncertain", "skipped", "preparing", "regenerating"):
            raise ValueError("This application cannot be regenerated")
        self._set_status(attempt["vacancy_id"], "regenerating", "Generating a new draft from your instructions.", draft_id)
        try:
            draft = await asyncio.to_thread(regenerate_draft, self.db, self.settings, draft_id, prompt, section)
            regeneration_changes = draft.get("changes", [])
            regenerated_section = draft.get("regenerated_section", section)
            if section == "all" and draft["destination"].get("kind") == "web":
                try:
                    async with self.browser_lock:
                        draft = await inspect_form(self.db, self.settings, draft_id)
                    attachments = _safe_attachments(draft["form_data"].get("fields", []))
                    if attachments:
                        form_data = {**draft["form_data"], "attachments": attachments}
                        self.db.execute("UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
                                        (json.dumps(form_data, ensure_ascii=False), now(), draft_id))
                except (ValueError, RuntimeError) as error:
                    self._set_status(attempt["vacancy_id"], "needs_review", f"Form inspection needs attention: {error}", draft_id)
            self._set_status(attempt["vacancy_id"], "needs_review", "Review the revised application.", draft_id)
            self.db.execute("UPDATE auto_application_attempts SET retry_payload=NULL WHERE draft_id=?", (draft_id,))
            await self.notify_review(draft_id, deliver_telegram=deliver_telegram)
            result = get_draft(self.db, draft_id)
            result["changes"] = regeneration_changes
            result["regenerated_section"] = regenerated_section
            return result
        except Exception as error:
            self._set_status(attempt["vacancy_id"], "needs_review", f"Regeneration failed: {str(error)[:500]}", draft_id)
            if isinstance(error, RuntimeError) and ("drafting failed" in str(error) or "invalid draft" in str(error)):
                self.db.execute("UPDATE auto_application_attempts SET retry_payload=? WHERE draft_id=?",
                                (json.dumps({"prompt": prompt, "section": section}), draft_id))
            raise

    async def approve(self, draft_id: str, expected_hash: str) -> dict:
        attempt = self.db.one("SELECT * FROM auto_application_attempts WHERE draft_id=?", (draft_id,))
        if not attempt or attempt["status"] != "awaiting_review":
            raise ValueError("This application is not awaiting review")
        draft = get_draft(self.db, draft_id)
        if expected_hash != attempt["review_hash"] or expected_hash != draft["package_hash"]:
            raise ValueError("Application changed since review. Review the current draft first")
        blockers = send_readiness(self.db, self.settings, draft)
        if blockers:
            self._set_status(attempt["vacancy_id"], "needs_review", "; ".join(blockers), draft_id)
            raise ValueError("; ".join(blockers))
        with self.db.connection() as conn:
            claimed = conn.execute(
                "UPDATE auto_application_attempts SET status='sending',detail='Approval received.',updated_at=? "
                "WHERE draft_id=? AND status='awaiting_review' AND review_hash=?",
                (now(), draft_id, expected_hash),
            ).rowcount
        if not claimed:
            raise ValueError("This application is already being sent or has changed")
        try:
            async with self.priority_browser():
                try:
                    await asyncio.wait_for(self.browser_lock.acquire(), timeout=BROWSER_LOCK_WAIT_SECONDS)
                except TimeoutError as error:
                    self._set_status(attempt["vacancy_id"], "awaiting_review",
                                     "Browser is busy. Try sending again after the other browser task finishes.", draft_id)
                    raise ValueError("Browser is busy. No submission started; try again shortly.") from error
                try:
                    result = await send_application(self.db, self.settings, draft_id, expected_hash)
                finally:
                    self.browser_lock.release()
        except Exception as error:
            if not isinstance(error, ValueError) or "No submission started" not in str(error):
                self._set_status(attempt["vacancy_id"], "needs_review", str(error), draft_id)
            raise
        if result["status"] in ("sent_confirmed", "submitted_confirmed"):
            self._set_status(attempt["vacancy_id"], "sent", result.get("receipt", ""), draft_id)
        elif result.get("outcome", {}).get("key") == "submission_uncertain":
            self._set_status(attempt["vacancy_id"], "submission_uncertain",
                             result["outcome"]["guidance"], draft_id)
        elif result.get("outcome", {}).get("key") == "send_failed":
            self._set_status(attempt["vacancy_id"], "awaiting_review",
                             result["outcome"]["guidance"], draft_id)
        else:
            self._set_status(attempt["vacancy_id"], "needs_review",
                             result.get("error") or result.get("receipt") or result.get("outcome", {}).get("label") or result["status"], draft_id)
        return result

    async def handle_telegram_update(self, update: dict, client: httpx.AsyncClient) -> None:
        config = telegram_config(self.settings)
        token, chat_id = config.get("token"), str(config.get("chat_id", ""))
        if not token or not chat_id:
            return
        callback = update.get("callback_query")
        if not callback:
            message = update.get("message", {})
            chat = message.get("chat", {})
            if (str(chat.get("id")) != chat_id or chat.get("type") != "private"
                    or str(message.get("from", {}).get("id")) != chat_id):
                return
            reply_id = message.get("reply_to_message", {}).get("message_id")
            pending = self.db.one("SELECT draft_id,review_hash,action FROM telegram_review_prompts WHERE message_id=?", (reply_id,)) if reply_id else None
            if not pending:
                return
            self.db.execute("DELETE FROM telegram_review_prompts WHERE message_id=?", (reply_id,))
            attempt = self.db.one("SELECT status,review_hash FROM auto_application_attempts WHERE draft_id=?", (pending["draft_id"],))
            if not attempt or attempt["status"] not in ("awaiting_review", "needs_review") or attempt["review_hash"] != pending["review_hash"]:
                await _post(client, token, "sendMessage", json={"chat_id": chat_id, "text": "That review has changed. Use Regenerate on the latest review message."})
                return
            instructions = str(message.get("text", "")).strip()
            if not instructions or len(instructions) > 2000:
                await _post(client, token, "sendMessage", json={"chat_id": chat_id, "text": "Send custom instructions under 2000 characters."})
                return
            instructions = ("Make only these requested corrections to the existing application. "
                            "Preserve all other verified facts and wording: " + instructions
                            if pending["action"] == "edit" else instructions)
            try:
                await self.regenerate(pending["draft_id"], instructions)
            except (ValueError, RuntimeError, KeyError):
                await _post(client, token, "sendMessage", json={"chat_id": chat_id,
                    "text": "Regeneration failed. Open Applications in Job Radar to review the draft and retry."})
            return
        async def answer(value: str) -> None:
            await _post(client, token, "answerCallbackQuery", json={"callback_query_id": callback["id"],
                                                              "text": value[:200], "show_alert": False})
        chat = callback.get("message", {}).get("chat", {})
        if (str(chat.get("id")) != chat_id or chat.get("type") != "private"
                or str(callback.get("from", {}).get("id")) != chat_id):
            await answer("Use the configured private chat to review applications.")
            return
        parts = str(callback.get("data", "")).split(":")
        if len(parts) != 4 or parts[0] != "review" or parts[1] not in ("approve", "edit", "retry"):
            await answer("Unknown review action.")
            return
        _, action, draft_id, version = parts
        attempt = self.db.one("SELECT status,review_hash FROM auto_application_attempts WHERE draft_id=?", (draft_id,))
        if not attempt or not attempt["review_hash"] or attempt["review_hash"][:12] != version:
            await answer("This review is outdated. Open the latest draft in Job Radar.")
            return
        draft = get_draft(self.db, draft_id)
        if draft["package_hash"] != attempt["review_hash"] or attempt["status"] in ("sent", "sending", "skipped"):
            await answer("This review is outdated. Open the latest draft in Job Radar.")
            return
        if action == "approve":
            if attempt["status"] != "awaiting_review":
                await answer("This draft needs changes before it can be sent.")
                return
            await answer("Approval received. Sending the reviewed version.")
            try:
                result = await self.approve(draft_id, draft["package_hash"])
                outcome = result.get("outcome", {})
                reply = f"{outcome.get('label', 'Application updated')}: {result.get('receipt') or result.get('error') or outcome.get('guidance', '')}"
            except (ValueError, KeyError) as error:
                reply = f"Application was not sent: {error}"
            await _post(client, token, "sendMessage", json={"chat_id": chat_id, "text": reply[:4000]})
        elif action == "edit":
            await answer("Reply with the exact corrections you want.")
            result = await _post(client, token, "sendMessage", json={"chat_id": chat_id,
                "text": f"Reply with what to change in {draft['job_title']}, for example: correct my phone number to ... or replace the email body with ... . I will send the revised application and PDF for review. For direct field-by-field editing, open Applications in Job Radar on your Mac.",
                "reply_markup": {"force_reply": True, "selective": True}})
            self.db.execute("INSERT OR REPLACE INTO telegram_review_prompts(message_id,draft_id,review_hash,action,created_at) VALUES(?,?,?,'edit',?)",
                            (result["message_id"], draft_id, draft["package_hash"], now()))
        else:
            await answer("Reply with your custom instructions for a new draft.")
            result = await _post(client, token, "sendMessage", json={"chat_id": chat_id,
                "text": f"Reply to this message with instructions to regenerate {draft['job_title']}. Include only changes you want; Job Radar will preserve your verified facts.",
                "reply_markup": {"force_reply": True, "selective": True}})
            self.db.execute("INSERT OR REPLACE INTO telegram_review_prompts(message_id,draft_id,review_hash,action,created_at) VALUES(?,?,?,'retry',?)",
                            (result["message_id"], draft_id, draft["package_hash"], now()))

    def _strong_jobs_for_notifications(self, limit: int = 10) -> list[dict]:
        preferences = normalize_search_intent(self.db.get_setting("search_intent", {}))
        threshold = preferences["strong_match_threshold"]
        rows = self.db.all(
            "SELECT id,title,company,location,score,score_detail,first_seen_at FROM vacancies "
            "WHERE analysis_status='done' AND score>=? AND decision_state='undecided' "
            "AND snoozed_until IS NULL AND datetime(first_seen_at)>=datetime('now','-1 day') "
            "ORDER BY score DESC,first_seen_at DESC LIMIT 50",
            (threshold,),
        )
        strong = []
        for row in rows:
            try:
                detail = json.loads(row.get("score_detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            summary = fit_summary(row.get("score"), detail, preferences)
            if summary["fit_class"] == "strong":
                strong.append(row)
            if len(strong) >= limit:
                break
        return strong

    async def _send_strong_job_alerts(self, config: dict) -> None:
        if not telegram_mode_enabled(config, "strong_job_alerts") or telegram_quiet_now(config):
            return
        channel = "telegram_strong_job"
        candidates = [
            job for job in self._strong_jobs_for_notifications(limit=10)
            if not self.db.one(
                "SELECT 1 AS sent FROM notification_attempts "
                "WHERE vacancy_id=? AND channel=? AND status='sent'",
                (job["id"], channel),
            )
        ][:5]
        if not candidates:
            return
        async with httpx.AsyncClient(timeout=12) as client:
            for job in candidates:
                text = (
                    f"Strong match: {job['title']} at {job['company']}\n"
                    f"{job['score']}/100"
                    + (f" · {job['location']}" if job.get("location") else "")
                    + "\nOpen Job Radar → Jobs to review it."
                )
                try:
                    await _post(
                        client,
                        config["token"],
                        "sendMessage",
                        json={"chat_id": config["chat_id"], "text": text[:4000]},
                    )
                    self._record_notification(job["id"], channel, status="sent", sent=True)
                except (httpx.HTTPError, RuntimeError, ValueError) as error:
                    self._record_notification(
                        job["id"],
                        channel,
                        status="failed",
                        error=f"Telegram delivery failed ({type(error).__name__})",
                    )
                    break

    async def _send_daily_digest(self, config: dict) -> None:
        if not telegram_mode_enabled(config, "daily_digest") or telegram_quiet_now(config):
            return
        local = datetime.now().astimezone()
        digest_time = config.get("digest_time") or "18:00"
        hour, minute = (int(part) for part in digest_time.split(":"))
        if (local.hour, local.minute) < (hour, minute):
            return
        today = local.date().isoformat()
        if self.db.get_setting("telegram_digest_last_date", "") == today:
            return
        jobs = self._strong_jobs_for_notifications(limit=5)
        lines = [f"Job Radar daily digest · {len(jobs)} strong match{'es' if len(jobs) != 1 else ''} in the last 24h"]
        for job in jobs:
            lines.append(f"• {job['score']}/100 · {job['title']} · {job['company']}")
        if not jobs:
            lines.append("No new strong matches today.")
        lines.append("Open Job Radar → Jobs for the full inbox.")
        async with httpx.AsyncClient(timeout=12) as client:
            await _post(
                client,
                config["token"],
                "sendMessage",
                json={"chat_id": config["chat_id"], "text": "\n".join(lines)[:4000]},
            )
        current = telegram_config(self.settings)
        if self._same_telegram_destination(current, config):
            self.db.set_setting("telegram_digest_last_date", today)

    async def _telegram_loop(self) -> None:
        next_retry = 0.0
        while True:
            config = telegram_config(self.settings)
            if not config.get("token") or not config.get("chat_id"):
                await asyncio.sleep(5)
                continue
            token = config["token"]
            offset = int(self.db.get_setting("telegram_review_offset", 0))
            try:
                current = asyncio.get_running_loop().time()
                if current >= next_retry:
                    next_retry = current + 60
                    await self._send_strong_job_alerts(config)
                    await self._send_daily_digest(config)
                    if telegram_mode_enabled(config, "application_reviews"):
                        undelivered = self.db.all(
                            "SELECT vacancy_id,draft_id FROM auto_application_attempts "
                            "WHERE telegram_status IN ('pending','failed','not_configured','deferred','deferred_limit') "
                            "AND status IN ('awaiting_review','needs_review') "
                            "ORDER BY updated_at DESC LIMIT 10"
                        )
                        for item in undelivered:
                            if item["draft_id"]:
                                await self.notify_review(item["draft_id"])
                            else:
                                await self.notify_preparation_issue(item["vacancy_id"])
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.get(
                        f"https://api.telegram.org/bot{token}/getUpdates",
                        params={
                            "offset": offset,
                            "timeout": 5,
                            "allowed_updates": json.dumps(["callback_query", "message"]),
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not payload.get("ok"):
                        raise RuntimeError("Telegram update polling failed")
                    for update in payload.get("result", []):
                        try:
                            await self.handle_telegram_update(update, client)
                        except Exception:
                            log.exception("Could not process Telegram review update")
                        offset = max(offset, int(update["update_id"]) + 1)
                        current_config = telegram_config(self.settings)
                        if not self._same_telegram_destination(current_config, config):
                            break
                        self.db.set_setting("telegram_review_offset", offset)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("Telegram polling paused: %s", type(error).__name__)
                await asyncio.sleep(10)

    def _next_automatic_candidate(self, config: dict) -> dict | None:
        rows = self.db.all(
            "SELECT v.id,'automation' AS requested_by FROM vacancies v "
            "WHERE v.analysis_status='done' AND v.score>=? "
            "AND v.decision_state IN ('undecided','shortlisted') AND v.snoozed_until IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm') "
            "AND NOT EXISTS(SELECT 1 FROM auto_application_attempts a WHERE a.vacancy_id=v.id) "
            "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=v.id) "
            "AND NOT EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=v.id) "
            "ORDER BY v.score DESC,v.first_seen_at DESC LIMIT 50",
            (config["threshold"],),
        )
        for row in rows:
            if automation_eligibility(
                self.db,
                row["id"],
                policy=config,
                require_analysis=True,
                check_daily_limit=False,
            )["eligible"]:
                return row
        return None

    async def _loop(self) -> None:
        while True:
            config = self.config()
            daily_room = daily_auto_drafts_used(self.db) < config["max_auto_drafts_per_day"]

            queued = self.db.one(
                "SELECT a.vacancy_id AS id,a.requested_by FROM auto_application_attempts a "
                "JOIN vacancies v ON v.id=a.vacancy_id "
                "WHERE a.status='queued' AND a.requested_by='manual' "
                "ORDER BY a.created_at ASC LIMIT 1"
            )
            if not queued and config["enabled"] and daily_room:
                queued = self.db.one(
                    "SELECT a.vacancy_id AS id,a.requested_by FROM auto_application_attempts a "
                    "JOIN vacancies v ON v.id=a.vacancy_id "
                    "WHERE a.status='queued' AND a.requested_by='automation' "
                    "AND v.analysis_status='done' ORDER BY a.created_at ASC LIMIT 1"
                )

            job = queued
            if not job and config["enabled"] and daily_room:
                job = self._next_automatic_candidate(config)

            if not job:
                self.wake_event.clear()
                timeout = 60 if config["enabled"] and not daily_room else 5
                try:
                    await asyncio.wait_for(self.wake_event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
                continue

            job_id = job["id"]
            request = self.db.one(
                "SELECT requested_by FROM auto_application_attempts WHERE vacancy_id=?",
                (job_id,),
            )
            requested_by = (request or {}).get("requested_by") or job.get("requested_by") or "automation"

            if queued and requested_by == "automation":
                eligibility = automation_eligibility(
                    self.db,
                    job_id,
                    policy=config,
                    require_analysis=True,
                    check_daily_limit=False,
                )
                if not eligibility["eligible"]:
                    self._set_status(
                        job_id,
                        "skipped",
                        "Automatic policy changed: " + "; ".join(eligibility["reasons"]),
                    )
                    continue

            if queued:
                detail = (
                    "Preparing the application you requested"
                    if requested_by == "manual"
                    else "Preparing an application allowed by your automation policy"
                )
                self._set_status(job_id, "preparing", detail)
            else:
                self.db.execute(
                    "INSERT OR IGNORE INTO auto_application_attempts("
                    "vacancy_id,status,requested_by,detail,created_at,updated_at"
                    ") VALUES(?,'preparing','automation','Preparing an application allowed by your automation policy',?,?)",
                    (job_id, now(), now()),
                )

            try:
                await self._process(job_id)
            except asyncio.CancelledError:
                self._set_status(
                    job_id,
                    "needs_review",
                    "Job Radar stopped during preparation; review before sending",
                )
                raise
            except Exception as error:
                label = "Manual" if requested_by == "manual" else "Automatic"
                log.exception("%s application preparation failed for %s", label, job_id)
                provider_failure = isinstance(error, RuntimeError) and (
                    "drafting failed" in str(error) or "invalid draft" in str(error))
                self._set_status(
                    job_id,
                    "needs_review",
                    (f"AI drafting failed: {str(error)[:800]}" if provider_failure else
                     f"{label} preparation stopped: {str(error)[:800]}"),
                )
            await asyncio.sleep(0.1)
