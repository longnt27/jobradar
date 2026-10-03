"""Opt-in automatic applications for newly discovered, locally scored jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import re

from .apply import inspect_form, send_application, send_readiness
from .db import Database, now
from .drafting import get_draft, prepare_draft
from .settings import Settings


log = logging.getLogger(__name__)


class AutoApplyManager:
    def __init__(self, db: Database, settings: Settings, browser_lock: asyncio.Lock):
        self.db = db
        self.settings = settings
        self.browser_lock = browser_lock
        self.task: asyncio.Task | None = None
        self.wake_event = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    def config(self) -> dict:
        saved = self.db.get_setting("auto_apply", {})
        return {"enabled": bool(saved.get("enabled", False)), "threshold": int(saved.get("threshold", 85))}

    def status(self) -> dict:
        counts = {row["status"]: row["count"] for row in self.db.all(
            "SELECT status,COUNT(*) AS count FROM auto_application_attempts GROUP BY status")}
        recent = self.db.all(
            "SELECT a.vacancy_id,a.status,a.draft_id,a.detail,a.updated_at,v.title,v.company,v.score "
            "FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id "
            "WHERE a.status!='skipped' ORDER BY a.updated_at DESC LIMIT 20")
        return {**self.config(), "counts": counts, "recent": recent}

    def configure(self, enabled: bool, threshold: int) -> dict:
        if not 0 <= threshold <= 100:
            raise ValueError("Threshold must be between 0 and 100")
        previous = self.config()
        if enabled and not previous["enabled"]:
            # Mark every job already present, including the analysis backlog, before enabling.
            with self.db.connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO auto_application_attempts(vacancy_id,status,detail,created_at,updated_at) "
                    "SELECT id,'skipped','Found before automatic applications were enabled',?,? FROM vacancies",
                    (now(), now()),
                )
        self.db.set_setting("auto_apply", {"enabled": enabled, "threshold": threshold})
        self.wake()
        return self.status()

    def wake(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.wake_event.set)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.db.execute(
            "UPDATE auto_application_attempts SET status='needs_review',detail='Job Radar restarted during preparation; review before sending',updated_at=? "
            "WHERE status='preparing'", (now(),))
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def _set_status(self, job_id: str, status: str, detail: str = "", draft_id: str | None = None) -> None:
        self.db.execute(
            "UPDATE auto_application_attempts SET status=?,detail=?,draft_id=COALESCE(?,draft_id),updated_at=? WHERE vacancy_id=?",
            (status, detail[:1000], draft_id, now(), job_id),
        )

    def _still_eligible(self, job_id: str) -> bool:
        config = self.config()
        job = self.db.one("SELECT score,analysis_status,state FROM vacancies WHERE id=?", (job_id,))
        return bool(config["enabled"] and job and job["analysis_status"] == "done"
                    and job["score"] is not None and job["score"] > config["threshold"]
                    and job["state"] in ("new", "prepare"))

    async def _process(self, job_id: str) -> None:
        job = self.db.one("SELECT apply_url FROM vacancies WHERE id=?", (job_id,))
        if not job or not job["apply_url"]:
            self._set_status(job_id, "needs_review", "No verified application destination. Open the job and prepare it manually.")
            return
        prior = self.db.one("SELECT id FROM submissions WHERE vacancy_id=? LIMIT 1", (job_id,))
        if prior:
            self._set_status(job_id, "needs_review", "An application submission already exists for this job.")
            return
        existing = self.db.one("SELECT id FROM application_drafts WHERE vacancy_id=? LIMIT 1", (job_id,))
        if existing:
            self._set_status(job_id, "needs_review", "An application draft already exists. Review it before sending.", existing["id"])
            return
        provider = self.db.get_setting("profile", {}).get("drafting_provider", "")
        if not provider:
            self._set_status(job_id, "needs_review", "Choose an application drafting provider in My profile.")
            return
        draft = await asyncio.to_thread(prepare_draft, self.db, self.settings, job_id, provider)
        self._set_status(job_id, "preparing", "Draft prepared", draft["id"])
        if not self._still_eligible(job_id):
            self._set_status(job_id, "needs_review", "Automatic applications paused or job score changed.")
            return
        if draft["destination"].get("kind") == "web":
            async with self.browser_lock:
                draft = await inspect_form(self.db, self.settings, draft["id"])
            attachments = draft["form_data"].get("attachments", {})
            for field in draft["form_data"].get("fields", []):
                if field["type"] == "file" and re.search(r"\b(resume|cv|curriculum vitae)\b", f"{field['name']} {field['label']}", re.I):
                    attachments[str(field["index"])] = {"kind": "resume"}
            if attachments:
                form_data = {**draft["form_data"], "attachments": attachments}
                self.db.execute("UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
                                (json.dumps(form_data, ensure_ascii=False), now(), draft["id"]))
                draft = get_draft(self.db, draft["id"])
        blockers = send_readiness(self.db, self.settings, draft)
        if blockers:
            self._set_status(job_id, "needs_review", "; ".join(blockers), draft["id"])
            return
        if not self._still_eligible(job_id):
            self._set_status(job_id, "needs_review", "Automatic applications paused or job score changed.", draft["id"])
            return
        async with self.browser_lock:
            result = await send_application(self.db, self.settings, draft["id"], draft["package_hash"])
        if result["status"] in ("sent_confirmed", "submitted_confirmed"):
            self._set_status(job_id, "sent", result.get("receipt", ""), draft["id"])
        else:
            self._set_status(job_id, "needs_review", result.get("error") or result.get("receipt") or result["status"], draft["id"])

    async def _loop(self) -> None:
        while True:
            config = self.config()
            job = self.db.one(
                "SELECT v.id FROM vacancies v WHERE v.analysis_status='done' AND v.score>? AND v.state='new' "
                "AND NOT EXISTS(SELECT 1 FROM auto_application_attempts a WHERE a.vacancy_id=v.id) "
                "ORDER BY v.score DESC,v.first_seen_at DESC LIMIT 1", (config["threshold"],)) if config["enabled"] else None
            if not job:
                self.wake_event.clear()
                try:
                    await asyncio.wait_for(self.wake_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                continue
            job_id = job["id"]
            self.db.execute(
                "INSERT OR IGNORE INTO auto_application_attempts(vacancy_id,status,created_at,updated_at) VALUES(?,'preparing',?,?)",
                (job_id, now(), now()))
            try:
                await self._process(job_id)
            except asyncio.CancelledError:
                self._set_status(job_id, "needs_review", "Job Radar stopped during preparation; review before sending")
                raise
            except Exception as error:
                log.exception("Automatic application failed for %s", job_id)
                self._set_status(job_id, "needs_review", f"Automatic application stopped: {str(error)[:800]}")
            await asyncio.sleep(0.1)
