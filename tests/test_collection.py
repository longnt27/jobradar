import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import Database, new_id, now
from job_radar.ingest import ObservedJob, ingest, normalize_url
from job_radar.scanner import ScanManager
from job_radar.seeds import seed
from job_radar.settings import Settings
from job_radar.web import create_app


def test_normalization_preserves_identity_and_ingest_updates(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    seed(db)
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    assert normalize_url("https://facebook.com/story.php?story_fbid=42&fbclid=x") == "https://facebook.com/story.php?story_fbid=42"
    first = ObservedJob("https://example.org/jobs/5?utm_source=x", "AI Engineer", "Example", "Build AI systems with Python.")
    identifier, new = ingest(db, source, first)
    assert new
    first.description = "Build AI systems with Python and machine learning."
    assert ingest(db, source, first) == (identifier, False)
    assert db.one("SELECT description FROM vacancies WHERE id=?", (identifier,))["description"] == first.description
    assert db.one("SELECT COUNT(*) AS count FROM observations")["count"] == 1


def test_scan_records_observations(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    seed(db)
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]

    async def fake_collect(_settings, _source):
        return [ObservedJob("https://example.org/job/1", "ML Engineer", "Example", "Machine learning with Python.")]

    monkeypatch.setattr("job_radar.scanner.collect_source", fake_collect)
    manager = ScanManager(db, settings)
    result = asyncio.run(manager.run_source(source))
    assert result["status"] == "success"
    assert result["new"] == 1
    assert db.one("SELECT status FROM scan_runs WHERE id=?", (result["run_id"],))["status"] == "success"


def test_scan_due_endpoint_starts_background_task(monkeypatch, tmp_path: Path) -> None:
    async def fake_run_due(self, due):
        return None

    monkeypatch.setattr(ScanManager, "_run_due", fake_run_due)
    app = create_app(Settings(tmp_path))
    response = TestClient(app).post("/api/scan/due")
    assert response.status_code == 200
    assert response.json()["queued"] > 0


def test_interrupted_scan_is_requeued_on_restart(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    seed(db)
    source_id = db.one("SELECT id FROM sources WHERE enabled=1 LIMIT 1")["id"]
    db.execute("UPDATE sources SET last_attempt_at=?,last_status='running' WHERE id=?", (now(), source_id))
    run_id = new_id()
    db.execute("INSERT INTO scan_runs(id,source_id,started_at,status) VALUES(?,?,?,'running')",
               (run_id, source_id, now()))
    manager = ScanManager(db, settings)
    assert manager.recover_interrupted() == 1
    assert db.one("SELECT status,finished_at FROM scan_runs WHERE id=?", (run_id,))["status"] == "interrupted"
    assert db.one("SELECT last_attempt_at,last_status FROM sources WHERE id=?", (source_id,)) == {
        "last_attempt_at": None, "last_status": "interrupted"}
    assert manager.recover_interrupted() == 0


def test_generic_group_posts_do_not_merge_by_title(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    seed(db)
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    first = ObservedJob("https://facebook.com/groups/1/posts/10", "AI Engineer", "Facebook post", "Hiring AI engineer for vision models.")
    second = ObservedJob("https://facebook.com/groups/1/posts/11", "AI Engineer", "Facebook post", "Hiring AI engineer for language models.")
    assert ingest(db, source, first)[0] != ingest(db, source, second)[0]


def test_same_posting_refreshes_destination_even_without_description_change(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    seed(db)
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    job = ObservedJob("https://example.org/jobs/5", "AI Engineer", "Example", "Build AI systems with Python.", apply_url="https://example.org/apply/old")
    identifier, _ = ingest(db, source, job)
    job.apply_url = "https://example.org/apply/new"
    assert ingest(db, source, job) == (identifier, False)
    assert db.one("SELECT apply_url FROM vacancies WHERE id=?", (identifier,))["apply_url"] == job.apply_url


def test_new_requisition_url_with_same_title_stays_distinct(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    seed(db)
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    first = ObservedJob("https://example.org/jobs/old", "AI Engineer", "Example", "Build vision systems.", location="Hanoi", apply_url="https://example.org/apply/old")
    second = ObservedJob("https://example.org/jobs/new", "AI Engineer", "Example", "Build language systems.", location="Hanoi", apply_url="https://example.org/apply/new")
    first_id, _ = ingest(db, source, first)
    second_id, new = ingest(db, source, second)
    assert new
    assert first_id != second_id
    assert db.one("SELECT description,apply_url FROM vacancies WHERE id=?", (second_id,)) == {"description": second.description, "apply_url": second.apply_url}
