import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import SCHEMA, Database, new_id, now
from job_radar.ingest import ObservedJob, ingest
from job_radar.location import is_hcm_only, job_location
from job_radar.settings import Settings
from job_radar.web import create_app


def test_hcm_only_location_variants_and_multi_city_roles() -> None:
    for location in ("Hồ Chí Minh", "TP.HCM", "HCMC", "Ho Chi Minh City, Vietnam", "Sài Gòn"):
        assert is_hcm_only(location)
    for location in ("Hanoi / Ho Chi Minh City", "HCMC or remote", "HCMC / Da Nang", "Hanoi", "Vietnam", ""):
        assert not is_hcm_only(location)


def test_location_is_inferred_only_from_posting_header() -> None:
    assert is_hcm_only(job_location("", "[HCM] AI Engineer", "About us"))
    assert is_hcm_only(job_location("", "AI Engineer", "AI Engineer\n\nHồ Chí Minh\n\nJob details"))
    assert is_hcm_only(job_location("", "AI Engineer", "Địa điểm làm việc:\n\n• Văn phòng, Hồ Chí Minh\n\nMô tả công việc:"))
    assert not is_hcm_only(job_location("", "AI Engineer", "Data Engineer\n\n• Hà Nội, TP. Hồ Chí Minh,\n\nJob description"))
    assert job_location("", "AI Engineer", "About our HCMC office and company culture.\n\nRequirements") == ""


def test_existing_hcm_postings_are_hidden_after_database_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "job_radar.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA.replace("  excluded_location INTEGER NOT NULL DEFAULT 0,\n", ""))
        timestamp = now()
        conn.execute(
            "INSERT INTO vacancies(id,company,title,location,description,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id(), "Example", "AI Engineer", "", "AI Engineer\n\nHồ Chí Minh\n\nBuild AI models", timestamp, timestamp, timestamp, timestamp),
        )
    client = TestClient(create_app(Settings(tmp_path)))
    assert client.get("/api/jobs").json() == []
    assert client.get("/api/status").json()["counts"]["vacancies"] == 0
    assert Database(path).one("SELECT excluded_location FROM vacancies")["excluded_location"] == 1


def test_new_hcm_job_is_hidden_but_hanoi_option_is_visible(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = Database(tmp_path / "job_radar.sqlite3")
    source = db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")
    hcm_id, new_hcm = ingest(db, source["id"], ObservedJob(
        url="https://example.org/hcm", company="Example", title="AI Engineer HCMC",
        description="Build AI models in our office", location="TP. HCM",
    ))
    mixed_id, new_mixed = ingest(db, source["id"], ObservedJob(
        url="https://example.org/mixed", company="Example", title="AI Engineer Hanoi",
        description="Build AI models in our office", location="Hanoi / Ho Chi Minh City",
    ))
    assert not new_hcm and new_mixed
    assert [job["id"] for job in client.get("/api/jobs").json()] == [mixed_id]
    assert [job["id"] for job in client.get("/api/jobs?q=Engineer").json()] == [mixed_id]
    assert client.get("/api/status").json()["counts"]["vacancies"] == 1
    assert db.one("SELECT excluded_location FROM vacancies WHERE id=?", (hcm_id,))["excluded_location"] == 1
    rejected = client.post("/api/jobs/import", json={
        "company": "Example", "title": "Research Engineer", "location": "Ho Chi Minh City",
        "description": "Build machine learning systems",
    })
    assert rejected.status_code == 422
