from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from .collectors import AuthRequired, collect_source
from .db import Database, new_id, now
from .employer_scope import HCMC_NAMES
from .ingest import ingest
from .notifications import notify_social_sign_in_required
from .settings import Settings
from .social_browser import social_login_at


log = logging.getLogger(__name__)


class ScanManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.browser_lock = asyncio.Lock()
        self.active: set[str] = set()
        self.pending: list[tuple[str, bool]] = []
        self._due_task: asyncio.Task | None = None
        self._scheduler_task: asyncio.Task | None = None

    async def start(self) -> None:
        self.recover_interrupted()
        self._scheduler_task = asyncio.create_task(self._scheduler())

    def recover_interrupted(self) -> int:
        """A prior process cannot finish its scans; make those sources due again."""
        with self.db.connection() as conn:
            source_ids = [row[0] for row in conn.execute(
                "SELECT DISTINCT source_id FROM scan_runs WHERE status='running'").fetchall()]
            if not source_ids:
                return 0
            conn.execute(
                "UPDATE scan_runs SET status='interrupted',finished_at=?,"
                "detail='Service restarted during scan; source queued to retry' WHERE status='running'",
                (now(),),
            )
            conn.executemany(
                "UPDATE sources SET last_attempt_at=NULL,last_status='interrupted' WHERE id=?",
                [(source_id,) for source_id in source_ids],
            )
        return len(source_ids)

    async def stop(self) -> None:
        for task in (self._scheduler_task, self._due_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.pending.clear()

    async def _scheduler(self) -> None:
        while True:
            try:
                self.queue_due()
            except Exception:
                log.exception("Unable to schedule sources")
            await asyncio.sleep(60)

    def queue_due(self) -> int:
        sources = self.db.all("SELECT id,last_attempt_at,interval_minutes,kind FROM sources WHERE enabled=1 "
                              "AND COALESCE(json_extract(config,'$.retired'),0)=0")
        current = datetime.now(timezone.utc)
        due = []
        for source in sources:
            if source["kind"] in ("linkedin", "facebook") and (
                not social_login_at(self.db, source["kind"]) or self.db.get_setting(f"social_reauth_required_{source['kind']}")
            ):
                continue
            try:
                last = datetime.fromisoformat(source["last_attempt_at"]) if source["last_attempt_at"] else None
            except ValueError:
                last = None
            if last is None or last + timedelta(minutes=source["interval_minutes"]) <= current:
                due.append(source["id"])
        return self.queue_sources(due)

    def queue_unscanned(self) -> dict[str, int]:
        sources = self.db.all("SELECT id,kind FROM sources WHERE enabled=1 AND last_success_at IS NULL "
                              "AND COALESCE(json_extract(config,'$.retired'),0)=0")
        ids = [source["id"] for source in sources]
        old_positions = {source_id: index for index, (source_id, _) in enumerate(self.pending)}
        added = self.queue_sources(ids)
        selected = set(ids)
        self.pending[:] = ([item for item in self.pending if item[0] in selected] +
                           [item for item in self.pending if item[0] not in selected])
        promoted = sum(self.queue_position(source_id) is not None and
                       self.queue_position(source_id) - 1 < old_position
                       for source_id, old_position in old_positions.items() if source_id in selected)
        return {"queued": added, "prioritized": promoted}

    def queue_sources(self, source_ids: list[str], *, manual: bool = False) -> int:
        waiting = {source_id for source_id, _ in self.pending}
        added = 0
        for source_id in source_ids:
            if source_id in waiting or source_id in self.active:
                continue
            source = self.db.one("SELECT kind FROM sources WHERE id=? "
                                 "AND COALESCE(json_extract(config,'$.retired'),0)=0", (source_id,))
            if not source:
                continue
            kind = source["kind"]
            if kind in ("linkedin", "facebook") and (
                not social_login_at(self.db, kind) or self.db.get_setting(f"social_reauth_required_{kind}")
            ):
                continue
            self.pending.append((source_id, manual))
            waiting.add(source_id)
            added += 1
        if self.pending and (self._due_task is None or self._due_task.done()):
            self._due_task = asyncio.create_task(self._run_due(self.pending))
        return added

    def queue_position(self, source_id: str) -> int | None:
        return next((index for index, (queued_id, _) in enumerate(self.pending, 1)
                     if queued_id == source_id), None)

    async def _run_due(self, due: list[tuple[str, bool]]) -> None:
        while due:
            source_id, manual = due.pop(0)
            try:
                if not self.db.one("SELECT id FROM sources WHERE id=? "
                                   "AND COALESCE(json_extract(config,'$.retired'),0)=0 "
                                   + ("" if manual else "AND enabled=1"), (source_id,)):
                    continue
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
        if source["config"] and json.loads(source["config"]).get("retired"):
            raise ValueError("Retired source cannot be scanned")
        if source["employer_id"] and self.db.one("SELECT id FROM employers WHERE id=? AND coverage_status='excluded_hcm'", (source["employer_id"],)):
            raise ValueError("HCMC-based employer is outside the crawl scope")
        if source["kind"] in ("linkedin", "facebook") and self.db.get_setting(f"social_reauth_required_{source['kind']}"):
            return {"status": "auth_required", "error": f"Sign in to {source['kind'].capitalize()} again in Profile"}
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
            if source["kind"] == "facebook" and source.get("resolved_name") and source["resolved_name"] != source["name"]:
                self.db.execute("UPDATE sources SET name=? WHERE id=?", (source["resolved_name"], source_id))
                source["name"] = source["resolved_name"]
            new_count = 0
            for job in jobs:
                if job.company.casefold() in HCMC_NAMES:
                    continue
                _, is_new = ingest(self.db, source_id, job)
                new_count += int(is_new)
            finished = now()
            status = "success" if jobs else "empty"
            self.db.execute("UPDATE scan_runs SET finished_at=?,status=?,observed_count=?,new_count=? WHERE id=?",
                            (finished, status, len(jobs), new_count, run_id))
            self.db.execute("UPDATE sources SET last_success_at=?,last_status=? WHERE id=?", (finished, status, source_id))
            return {"run_id": run_id, "status": status, "observed": len(jobs), "new": new_count}
        except Exception as error:
            status = "auth_required" if isinstance(error, AuthRequired) else "failed"
            self.db.execute("UPDATE scan_runs SET finished_at=?,status=?,detail=? WHERE id=?", (now(), status, str(error)[:1000], run_id))
            self.db.execute("UPDATE sources SET last_status=? WHERE id=?", (status, source_id))
            if status == "auth_required" and source["kind"] in ("linkedin", "facebook"):
                key = f"social_reauth_required_{source['kind']}"
                if not self.db.get_setting(key):
                    self.db.set_setting(key, {"source_id": source_id, "detected_at": now()})
                    try:
                        await asyncio.to_thread(notify_social_sign_in_required, source["kind"])
                    except Exception as alert_error:
                        log.warning("Could not show local sign-in alert: %s", alert_error)
            log.warning("Scan %s failed: %s", source["name"], error)
            return {"run_id": run_id, "status": status, "error": str(error)}
        finally:
            self.active.discard(source_id)
