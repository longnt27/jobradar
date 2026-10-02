from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.web import create_app


def test_employer_career_page_becomes_scan_source(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    employer = client.post("/api/employers", json={"name": "Example Robotics", "career_url": "https://example.org/careers"})
    assert employer.status_code == 201
    identifier = employer.json()["id"]
    source = next(source for source in client.get("/api/sources?kind=career").json() if source["employer_id"] == identifier)
    assert source["interval_minutes"] == 240
    assert next(row for row in client.get("/api/employers?q=Example Robotics").json() if row["id"] == identifier)["live_coverage"] == "active_scan"


def test_github_repository_discovery_endpoint(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    monkeypatch.setattr("job_radar.web.list_public_repositories", lambda username: [{"name": username, "url": "https://github.com/test/project"}])
    response = client.get("/api/github/test/repositories")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "test"
