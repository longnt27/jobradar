"""Bounded background analysis of discovered jobs, independent of drafting."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess

from .db import Database, now
from .auto_apply import AutoApplyManager
from .local_analysis import LocalModelUnavailable, RECOMMENDED_MODEL, analyze_job, validate_local_model
from .settings import Settings


log = logging.getLogger(__name__)


class MatchManager:
    def __init__(self, db: Database, settings: Settings, auto_apply: AutoApplyManager | None = None):
        self.db = db
        self.settings = settings
        self.auto_apply = auto_apply
        self.task: asyncio.Task | None = None
        self.tasks: list[asyncio.Task] = []
        self.pull_task: asyncio.Task | None = None
        self.pull_state = "idle"
        self.pull_error: str | None = None
        self.service_error: str | None = None
        self.wake_event = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    def status(self) -> dict:
        counts = {row["analysis_status"]: row["count"] for row in self.db.all(
            "SELECT analysis_status,COUNT(*) AS count FROM vacancies GROUP BY analysis_status")}
        return {"model": self.db.get_setting("matching_model", ""), "recommended": RECOMMENDED_MODEL,
                "pending": counts.get("pending", 0) + counts.get("running", 0),
                "completed": counts.get("done", 0), "failed": counts.get("failed", 0),
                "download_state": self.pull_state, "download_error": self.pull_error,
                "service_error": self.service_error}

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        model = self.db.get_setting("matching_model", "")
        if model:
            self.db.execute(
                "UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL WHERE analysis_status='running' "
                "OR analysis_status='not_configured' OR (analysis_status='done' AND analysis_model<>?)",
                (model,),
            )
        self.tasks = [asyncio.create_task(self._loop()) for _ in range(2)]
        self.task = self.tasks[0]

    async def stop(self) -> None:
        for task in (*self.tasks, self.pull_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def select_model(self, model: str) -> str:
        selected = validate_local_model(model)
        if selected != self.db.get_setting("matching_model", ""):
            self.db.set_setting("matching_model", selected)
            self.db.set_setting("matching_model_activated_at", now())
            self.invalidate_all()
        return selected

    def invalidate_all(self) -> None:
        if not self.db.get_setting("matching_model", ""):
            return
        self.db.execute("UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL,analysis_error=NULL WHERE analysis_status NOT IN ('pending','dismissed')")
        self._wake()

    def _wake(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.wake_event.set)

    def wake(self) -> None:
        self._wake()

    def retry(self, vacancy_id: str) -> None:
        if not self.db.get_setting("matching_model", ""):
            raise ValueError("Choose a local job matching model in My profile first")
        if not self.db.one("SELECT id FROM vacancies WHERE id=?", (vacancy_id,)):
            raise KeyError("Job not found")
        self.db.execute("UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL,analysis_error=NULL WHERE id=?", (vacancy_id,))
        self._wake()

    def dismiss_failure(self, vacancy_id: str) -> None:
        if not self.db.one("SELECT id FROM vacancies WHERE id=?", (vacancy_id,)):
            raise KeyError("Job not found")
        with self.db.connection() as conn:
            changed = conn.execute(
                "UPDATE vacancies SET analysis_status='dismissed',analysis_stage=NULL,analysis_error=NULL,score=NULL,score_detail=NULL "
                "WHERE id=? AND analysis_status='failed'", (vacancy_id,)).rowcount
        if not changed:
            raise ValueError("Only a failed analysis can be dismissed")

    def failures(self) -> list[dict]:
        return self.db.all(
            "SELECT id,title,company,analysis_error AS error FROM vacancies "
            "WHERE analysis_status='failed' ORDER BY updated_at DESC"
        )

    def retry_failed(self) -> int:
        if not self.db.get_setting("matching_model", ""):
            raise ValueError("Choose a local job matching model in My profile first")
        with self.db.connection() as conn:
            count = conn.execute(
                "UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL,analysis_error=NULL WHERE analysis_status='failed'"
            ).rowcount
        self._wake()
        return count

    def download_recommended(self) -> dict:
        if self.pull_task and not self.pull_task.done():
            return self.status()
        if not shutil.which("ollama"):
            raise ValueError("Install Ollama before downloading a local model")
        self.pull_state = "downloading"
        self.pull_error = None
        self.pull_task = asyncio.create_task(self._pull())
        return self.status()

    async def _pull(self) -> None:
        try:
            result = await asyncio.to_thread(
                subprocess.run, ["ollama", "pull", RECOMMENDED_MODEL],
                capture_output=True, text=True, timeout=1200, check=False,
            )
            if result.returncode:
                raise RuntimeError("Ollama could not download the model. Check its connection and try again.")
            await asyncio.to_thread(self.select_model, RECOMMENDED_MODEL)
            self.pull_state = "ready"
        except asyncio.CancelledError:
            self.pull_state = "idle"
            raise
        except Exception as error:
            self.pull_state = "failed"
            self.pull_error = str(error)[:240]

    async def _loop(self) -> None:
        while True:
            model = self.db.get_setting("matching_model", "")
            job = self.db.one(
                "SELECT * FROM vacancies WHERE analysis_status='pending' ORDER BY first_seen_at DESC LIMIT 1") if model else None
            if not job:
                self.wake_event.clear()
                try:
                    await asyncio.wait_for(self.wake_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                continue
            self.db.execute("UPDATE vacancies SET analysis_status='running',analysis_stage='extracting' WHERE id=?", (job["id"],))
            profile = self.db.get_setting("profile", {})
            projects = self.db.all("SELECT title,claim,details FROM evidence WHERE kind='project' AND approved=1 ORDER BY created_at DESC LIMIT 8")
            for project in projects:
                try:
                    project["details"] = json.loads(project["details"])
                except ValueError:
                    project["details"] = {}
            try:
                score, detail = await asyncio.to_thread(
                    analyze_job, job, profile, projects, model,
                    lambda stage: self.db.execute(
                        "UPDATE vacancies SET analysis_stage=? WHERE id=? AND analysis_status='running'",
                        (stage, job["id"])),
                )
                self.service_error = None
                if model != self.db.get_setting("matching_model", ""):
                    self.db.execute("UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL WHERE id=? AND analysis_status='running'", (job["id"],))
                    continue
                with self.db.connection() as conn:
                    changed = conn.execute(
                        "UPDATE vacancies SET score=?,score_detail=?,analysis_status='done',analysis_stage=NULL,analysis_model=?,"
                        "analysis_error=NULL,analyzed_at=?,updated_at=? WHERE id=? AND analysis_status='running' AND updated_at=?",
                        (score, json.dumps(detail, ensure_ascii=False), model, now(), now(), job["id"], job["updated_at"]),
                    ).rowcount
                if not changed:
                    self.db.execute("UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL WHERE id=? AND analysis_status='running'", (job["id"],))
                elif self.auto_apply:
                    self.auto_apply.wake()
            except asyncio.CancelledError:
                self.db.execute("UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL WHERE id=? AND analysis_status='running'", (job["id"],))
                raise
            except LocalModelUnavailable as error:
                self.service_error = str(error)
                self.db.execute("UPDATE vacancies SET analysis_status='pending',analysis_stage=NULL WHERE id=? AND analysis_status='running'", (job["id"],))
                await asyncio.sleep(15)
            except Exception as error:
                log.warning("Local analysis failed for job %s: %s", job["id"], error)
                self.db.execute("UPDATE vacancies SET analysis_status='failed',analysis_stage=NULL,analysis_error=? WHERE id=? AND analysis_status='running'",
                                (str(error)[:240], job["id"]))
            await asyncio.sleep(0.1)
