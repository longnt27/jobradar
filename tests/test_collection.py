import asyncio
from pathlib import Path

from job_radar.db import Database
from job_radar.ingest import ObservedJob, ingest, normalize_url
from job_radar.scanner import ScanManager
from job_radar.seeds import seed
from job_radar.settings import Settings


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
