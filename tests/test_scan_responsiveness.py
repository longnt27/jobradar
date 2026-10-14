import asyncio
import threading

import httpx
import pytest

from job_radar.ingest import ObservedJob, ingest
from job_radar.settings import Settings
from job_radar.web import create_app


def test_queue_remains_responsive_while_ingestion_transaction_is_open(tmp_path):
    app = create_app(Settings(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def writer():
        with app.state.db.connection() as conn:
            conn.execute("INSERT INTO settings VALUES('responsiveness_probe','true')")
            started.set()
            assert release.wait(5)

    async def run():
        task = asyncio.create_task(asyncio.to_thread(writer))
        await asyncio.to_thread(started.wait)
        timeout = threading.Timer(1, release.set)
        timeout.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/queue")
                assert response.status_code == 200
                assert not release.is_set(), "Queue waited for the whole ingestion transaction"
        finally:
            timeout.cancel()
            release.set()
            await task

    asyncio.run(run())


@pytest.mark.parametrize("cancellations", [1, 2])
def test_cancelled_scan_keeps_ingestion_owned_until_its_transaction_finishes(tmp_path, monkeypatch, cancellations):
    app = create_app(Settings(tmp_path))
    source_id = app.state.db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")["id"]
    started = threading.Event()
    release = threading.Event()

    async def collect(_settings, _source):
        return [ObservedJob("https://example.org/job/cancel-responsive", "ML Engineer", "Example",
                            "Build machine learning systems with Python.")]

    def slow_ingest(database, source, job):
        with database.connection() as conn:
            conn.execute("SELECT COUNT(*) FROM observations").fetchone()
            started.set()
            assert release.wait(5)
        return ingest(database, source, job)

    monkeypatch.setattr("job_radar.scanner.collect_source", collect)
    monkeypatch.setattr("job_radar.scanner.ingest", slow_ingest)

    async def run():
        manager = app.state.scan_manager
        task = asyncio.create_task(manager.run_source(source_id))
        await asyncio.to_thread(started.wait)
        timeout = threading.Timer(1, release.set)
        timeout.start()
        try:
            for _ in range(cancellations):
                task.cancel()
                await asyncio.sleep(.02)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/queue")
                assert response.status_code == 200
            assert not release.is_set(), "Cancellation blocked the event loop on ingestion"
            assert source_id in manager.active
        finally:
            timeout.cancel()
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert source_id not in manager.active
        assert app.state.db.one("SELECT COUNT(*) AS count FROM observations WHERE source_id=?", (source_id,))["count"] == 1
        assert app.state.db.one("SELECT status FROM scan_runs WHERE source_id=?", (source_id,))["status"] == "interrupted"

    asyncio.run(run())


def test_repeated_cancellation_waits_for_interruption_bookkeeping(tmp_path, monkeypatch):
    app = create_app(Settings(tmp_path))
    manager = app.state.scan_manager
    source_id = app.state.db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")["id"]
    collecting = asyncio.Event()
    bookkeeping = threading.Event()
    release = threading.Event()
    original = manager._record_interrupted

    async def collect(_settings, _source):
        collecting.set()
        await asyncio.Event().wait()

    def record(run_id, source):
        bookkeeping.set()
        assert release.wait(5)
        original(run_id, source)

    monkeypatch.setattr("job_radar.scanner.collect_source", collect)
    monkeypatch.setattr(manager, "_record_interrupted", record)

    async def run():
        task = asyncio.create_task(manager.run_source(source_id))
        await collecting.wait()
        task.cancel()
        await asyncio.to_thread(bookkeeping.wait)
        try:
            task.cancel()
            await asyncio.sleep(.02)
            assert source_id in manager.active
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert source_id not in manager.active
        assert app.state.db.one("SELECT status FROM scan_runs WHERE source_id=?", (source_id,))["status"] == "interrupted"

    asyncio.run(run())
