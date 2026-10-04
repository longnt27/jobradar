from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import new_id, now
from job_radar.settings import Settings
from job_radar.web import create_app


def test_one_queue_reports_real_worker_order_and_analysis_stage(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    source_ids = [row["id"] for row in db.all("SELECT id FROM sources WHERE kind='career' LIMIT 3")]
    db.execute("UPDATE sources SET enabled=0")
    app.state.scan_manager.active.add(source_ids[0])
    app.state.scan_manager.pending[:] = [(source_ids[2], True), (source_ids[1], False)]
    db.set_setting("auto_apply", {"enabled": True, "threshold": 80})

    def job(title: str, status: str, score: int | None, first_seen: str, stage: str | None = None) -> str:
        identifier = new_id()
        db.execute(
            "INSERT INTO vacancies(id,company,title,description,first_seen_at,last_seen_at,created_at,updated_at,"
            "analysis_status,analysis_stage,score) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, "Example", title, "Build AI systems with Python.", first_seen,
             first_seen, now(), now(), status, stage, score),
        )
        return identifier

    running = job("Running analysis", "running", None, "2026-10-04T12:00:00+00:00", "scoring")
    pending = job("Pending analysis", "pending", None, "2026-10-04T11:00:00+00:00")
    queued_draft = job("Explicit draft", "done", 90, "2026-10-04T10:00:00+00:00")
    automatic_draft = job("Automatic draft", "done", 85, "2026-10-04T09:00:00+00:00")
    waiting_score = job("Draft after scoring", "pending", None, "2026-10-04T08:00:00+00:00")
    failed = job("Failed analysis", "failed", None, "2026-10-04T07:30:00+00:00")
    reviewed = job("Ready for review", "done", 88, "2026-10-04T07:00:00+00:00")
    for identifier, status in ((queued_draft, "queued"), (waiting_score, "queued"),
                               (failed, "queued"), (reviewed, "awaiting_review")):
        db.execute(
            "INSERT INTO auto_application_attempts(vacancy_id,status,created_at,updated_at) VALUES(?,?,?,?)",
            (identifier, status, now(), now()),
        )

    response = TestClient(app).get("/api/queue")
    assert response.status_code == 200
    data = response.json()
    assert [row["id"] for row in data["scans"]["active"]] == [source_ids[0]]
    assert [row["id"] for row in data["scans"]["waiting"]] == [source_ids[2], source_ids[1]]
    assert [row["position"] for row in data["scans"]["waiting"]] == [1, 2]
    assert data["scans"]["waiting"][0]["requested_by"] == "you"
    assert data["analysis"]["active"][0]["id"] == running
    assert data["analysis"]["active"][0]["stage"] == "scoring"
    assert [row["id"] for row in data["analysis"]["waiting"]] == [pending, waiting_score]
    assert [row["id"] for row in data["drafts"]["waiting"]] == [queued_draft, automatic_draft]
    assert [row["id"] for row in data["analysis"]["failed"]] == [failed]
    assert "blocked" not in data["drafts"]
    assert data["drafts"]["review_ready"] == 1
