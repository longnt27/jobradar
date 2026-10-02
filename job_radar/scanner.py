from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from .collectors import AuthRequired, collect_source
from .db import Database, new_id, now
from .ingest import ingest
from .notifications import notify_new_jobs
from .settings import Settings


log = logging.getLogger(__name__)


class ScanManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.browser_lock = asyncio.Lock()
        self.active: set[str] = set()
        self._due_task: asyncio.Task | None = None
        self._scheduler_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._scheduler_task = asyncio.create_task(self._scheduler())

    async def stop(self) -> None:
        for task in (self._scheduler_task, self._due_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _scheduler(self) -> None:
        while True:
            try:
                self.queue_due()
            except Exception:
                log.exception("Unable to schedule sources")
            await asyncio.sleep(60)

    def queue_due(self) -> int:
        if self._due_task and not self._due_task.done():
            return 0
        sources = self.db.all("SELECT id,last_attempt_at,interval_minutes FROM sources WHERE enabled=1")
        current = datetime.now(timezone.utc)
        due = []
        for source in sources:
            try:
                last = datetime.fromisoformat(source["last_attempt_at"]) if source["last_attempt_at"] else None
            except ValueError:
                last = None
            if last is None or last + timedelta(minutes=source["interval_minutes"]) <= current:
                due.append(source["id"])
        if due:
            self._due_task = asyncio.create_task(self._run_due(due))
        return len(due)

    async def _run_due(self, due: list[str]) -> None:
        for source_id in due:
            try:
                await self.run_source(source_id)
            except Exception:
                log.exception("Source scan failed: %s", source_id)

    async def run_source(self, source_id: str) -> dict:
        source = self.db.one(
            "SELECT sources.*,employers.name AS employer_name FROM sources LEFT JOIN employers ON employers.id=sources.employer_id WHERE sources.id=?",
            (source_id,),
        )
        if not source:
            raise KeyError("Source not found")
        if source_id in self.active:
            return {"status": "already_running"}
        source["config"] = json.loads(source["config"])
        self.active.add(source_id)
        run_id = new_id()
        started = now()
        self.db.execute("INSERT INTO scan_runs(id,source_id,started_at,status) VALUES(?,?,?,?)", (run_id, source_id, started, "running"))
        self.db.execute("UPDATE sources SET last_attempt_at=?,last_status=? WHERE id=?", (started, "running", source_id))
        try:
            if source["kind"] in ("linkedin", "facebook"):
                async with self.browser_lock:
                    jobs = await collect_source(self.settings, source)
            else:
                jobs = await collect_source(self.settings, source)
            new_count = 0
            new_ids = []
            for job in jobs:
                vacancy_id, is_new = ingest(self.db, source_id, job)
                new_count += int(is_new)
                if is_new:
                    new_ids.append(vacancy_id)
            finished = now()
            status = "success" if jobs else "empty"
            self.db.execute("UPDATE scan_runs SET finished_at=?,status=?,observed_count=?,new_count=? WHERE id=?",
                            (finished, status, len(jobs), new_count, run_id))
            self.db.execute("UPDATE sources SET last_success_at=?,last_status=? WHERE id=?", (finished, status, source_id))
            if new_ids:
                try:
                    await notify_new_jobs(self.db, self.settings, new_ids)
                except Exception as error:
                    log.warning("Job alerts failed after scan %s: %s", source["name"], error)
            return {"run_id": run_id, "status": status, "observed": len(jobs), "new": new_count}
        except Exception as error:
            status = "auth_required" if isinstance(error, AuthRequired) else "failed"
            self.db.execute("UPDATE scan_runs SET finished_at=?,status=?,detail=? WHERE id=?", (now(), status, str(error)[:1000], run_id))
            self.db.execute("UPDATE sources SET last_status=? WHERE id=?", (status, source_id))
            log.warning("Scan %s failed: %s", source["name"], error)
            return {"run_id": run_id, "status": status, "error": str(error)}
        finally:
            self.active.discard(source_id)
