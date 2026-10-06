from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import new_id, now
from job_radar.ingest import ObservedJob, ingest
from job_radar.settings import Settings
from job_radar.web import create_app


def test_job_detail_exposes_dedup_provenance_and_split_preserves_sightings(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    source_rows = db.all("SELECT id FROM sources WHERE kind='linkedin' ORDER BY name LIMIT 2")
    first_source, second_source = source_rows[0]["id"], source_rows[1]["id"]
    posting = ObservedJob(
        "https://www.linkedin.com/jobs/view/999001/",
        "AI Engineer",
        "Example Robotics",
        "Build reliable Python machine learning systems for production.",
        location="Hanoi",
    )
    vacancy_id, created = ingest(db, first_source, posting)
    assert created is True
    duplicate_id, created = ingest(db, second_source, posting)
    assert duplicate_id == vacancy_id and created is False

    detail = client.get(f"/api/jobs/{vacancy_id}").json()
    assert detail["sighting_count"] == 2
    reasons = {item["merge_reason"] for item in detail["observations"]}
    assert reasons == {"new_vacancy", "same_url"}
    assert {item["merge_reason_label"] for item in detail["observations"]} == {"First sighting", "Same posting URL"}

    split_observation = next(item for item in detail["observations"] if item["merge_reason"] == "same_url")
    response = client.post(f"/api/jobs/{vacancy_id}/observations/{split_observation['id']}/split")
    assert response.status_code == 201, response.text
    new_vacancy_id = response.json()["id"]
    assert new_vacancy_id != vacancy_id

    original = client.get(f"/api/jobs/{vacancy_id}").json()
    separated = client.get(f"/api/jobs/{new_vacancy_id}").json()
    assert original["sighting_count"] == 1
    assert separated["sighting_count"] == 1
    assert separated["observations"][0]["id"] == split_observation["id"]
    assert separated["observations"][0]["merge_reason"] == "user_split"
    assert separated["observations"][0]["merge_reason_label"] == "Marked distinct by you"


def test_source_coverage_reports_collection_limit_and_discovery_summary(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    source = db.one("SELECT id,config FROM sources WHERE kind='linkedin' ORDER BY name LIMIT 1")
    import json
    config = json.loads(source["config"])
    cap = int(config["max_results"])
    timestamp = now()
    db.execute(
        "INSERT INTO scan_runs(id,source_id,started_at,finished_at,status,observed_count,new_count) "
        "VALUES(?,?,?,?,?,?,?)",
        (new_id(), source["id"], timestamp, timestamp, "success", cap, 3),
    )
    db.execute(
        "UPDATE sources SET last_attempt_at=?,last_success_at=?,last_status='success' WHERE id=?",
        (timestamp, timestamp, source["id"]),
    )

    listed = {row["id"]: row for row in client.get("/api/sources?kind=linkedin").json()}
    assert listed[source["id"]]["coverage"]["level"] == "limited"
    assert "collection limit" in listed[source["id"]]["coverage"]["detail"]

    summary = client.get("/api/discovery/coverage").json()
    assert summary["counts"]["linkedin"] >= 1
    assert summary["counts"]["career"] >= 1
    assert source["id"] in summary["limited_source_ids"]
    assert summary["channels"]["linkedin"]


def test_repeated_source_failures_are_user_visible_degradation(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    source_id = db.one("SELECT id FROM sources WHERE kind='career' ORDER BY name LIMIT 1")["id"]
    for offset in range(2):
        stamp = f"2026-10-05T0{8 + offset}:00:00+00:00"
        db.execute(
            "INSERT INTO scan_runs(id,source_id,started_at,finished_at,status,detail) VALUES(?,?,?,?,?,?)",
            (new_id(), source_id, stamp, stamp, "failed", "parser changed"),
        )
    db.execute("UPDATE sources SET last_attempt_at=?,last_status='failed' WHERE id=?",
               ("2026-10-05T09:00:00+00:00", source_id))
    source = next(row for row in client.get("/api/sources?kind=career").json() if row["id"] == source_id)
    assert source["coverage"]["level"] == "degraded"
    assert source["coverage"]["actionable"] is True
    assert "failed" in source["coverage"]["detail"]


def test_employer_api_uses_user_facing_coverage_states(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    employer_id = client.post("/api/employers", json={"name": "Coverage Example"}).json()["id"]
    row = client.get("/api/employers", params={"q": "Coverage Example"}).json()[0]
    assert row["live_coverage"] == "career_page_needed"

    assert client.patch(
        f"/api/employers/{employer_id}",
        json={"career_url": "https://example.org/careers"},
    ).status_code == 200
    row = client.get("/api/employers", params={"q": "Coverage Example"}).json()[0]
    assert row["live_coverage"] == "watching"

    app.state.db.execute(
        "UPDATE sources SET last_status='failed' WHERE employer_id=? AND enabled=1",
        (employer_id,),
    )
    row = client.get("/api/employers", params={"q": "Coverage Example"}).json()[0]
    assert row["live_coverage"] == "temporarily_unavailable"


def test_check_now_api_uses_all_enabled_sources_without_scheduler_categories(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    captured = {}

    def fake_queue(source_ids, *, manual=False):
        captured["ids"] = list(source_ids)
        captured["manual"] = manual
        return len(source_ids)

    monkeypatch.setattr(app.state.scan_manager, "queue_sources", fake_queue)
    monkeypatch.setattr(app.state.scan_manager, "queue_position", lambda _source_id: None)
    monkeypatch.setattr(app.state.scan_manager, "active", set())
    response = client.post("/api/scan/now")
    assert response.status_code == 200
    assert captured["manual"] is True
    assert response.json()["total_sources"] == len(captured["ids"])
    assert response.json()["queued"] == len(captured["ids"])


def test_discovery_trust_ui_uses_user_first_language() -> None:
    root = Path(__file__).parents[1] / "job_radar" / "static"
    html = (root / "index.html").read_text()
    js = (root / "app.js").read_text()
    css = (root / "app.css").read_text()

    assert 'id="scan-now">Check for jobs now' in html
    assert "Discovery coverage" in html
    assert "Scan diagnostics and advanced controls" in html
    assert "Filter discovery coverage" in html

    assert "Seen on ${job.sighting_count} source" in js
    assert "Mark as a different job" in js
    assert "Preferred source" in js
    assert "coverageLabel" in js
    assert "active_scan" not in js
    assert "sourceScanStatusLabel" in js
    assert "/api/scan/now" in js

    assert ".job-sightings" in css
    assert ".source-coverage-grid" in css
