from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.web import create_app


def test_first_run_seeds_employers_and_four_hour_linkedin_searches(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    status = client.get("/api/status").json()
    assert status["counts"]["employers"] >= 150
    searches = client.get("/api/sources?kind=linkedin").json()
    assert len(searches) == 27
    assert all(source["interval_minutes"] == 240 for source in searches)
    assert not client.get("/api/sources?kind=facebook").json()
    gsm = next(row for row in client.get("/api/employers?q=GSM").json() if row["name"] == "GSM / Xanh SM")
    assert "GreenSM" in gsm["aliases"]


def test_manual_job_search_and_state_history(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    result = client.post("/api/jobs/import", json={
        "company": "Example AI", "title": "Research Engineer",
        "description": "Build computer vision models for medical images with Python.",
        "location": "Hanoi", "apply_url": "https://example.org/apply",
    })
    assert result.status_code == 201
    identifier = result.json()["id"]
    assert [job["id"] for job in client.get("/api/jobs?q=vision").json()] == [identifier]
    assert client.post(f"/api/jobs/{identifier}/state", json={"state": "interesting"}).json() == {"state": "interesting"}
    assert client.get(f"/api/jobs/{identifier}").json()["state"] == "interesting"


def test_source_can_be_paused(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    source = client.get("/api/sources?kind=linkedin").json()[0]
    assert client.patch(f"/api/sources/{source['id']}", json={"enabled": False}).status_code == 200
    updated = next(row for row in client.get("/api/sources?kind=linkedin").json() if row["id"] == source["id"])
    assert updated["enabled"] is False


def test_status_separates_company_feeds_from_social_searches(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    counts = client.get("/api/status").json()["counts"]
    assert counts["career_sources_enabled"] == 43
    assert counts["active_sources"] == 70

    career = client.get("/api/sources?kind=career").json()[0]
    assert client.patch(f"/api/sources/{career['id']}", json={"enabled": False}).status_code == 200
    counts = client.get("/api/status").json()["counts"]
    assert counts["career_sources_enabled"] == 42
    assert counts["active_sources"] == 69
