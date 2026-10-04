from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.ingest import ObservedJob, ingest
from job_radar.ranking import score_job
from job_radar.web import create_app


def test_first_run_seeds_employers_and_four_hour_linkedin_searches(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    status = client.get("/api/status").json()
    assert status["counts"]["employers"] >= 150
    searches = client.get("/api/sources?kind=linkedin").json()
    assert len(searches) == 27
    assert all(source["interval_minutes"] == 240 for source in searches)
    assert all("f_TPR=" not in source["url"] for source in searches)
    assert all("location=" not in source["url"] for source in searches)
    assert any("Hanoi" in source["url"] for source in searches)
    assert all(source["config"]["max_results"] >= 150 for source in searches)
    assert not client.get("/api/sources?kind=facebook").json()
    gsm = next(row for row in client.get("/api/employers?q=GSM").json() if row["name"] == "GSM / Xanh SM")
    assert "GreenSM" in gsm["aliases"]


def test_existing_default_linkedin_searches_lose_the_24_hour_filter(tmp_path: Path) -> None:
    from job_radar.db import Database
    from job_radar.seeds import seed

    db = Database(tmp_path / "old.sqlite3")
    seed(db)
    default = db.one("SELECT id,url FROM sources WHERE kind='linkedin' ORDER BY name LIMIT 1")
    from urllib.parse import urlencode
    role, location = db.one("SELECT name FROM sources WHERE id=?", (default["id"],))["name"].split(" · ")
    old_query = {"keywords": role, "location": location, "f_TPR": "r86400"}
    if location == "Remote":
        old_query["f_WT"] = "2"
    old_url = "https://www.linkedin.com/jobs/search/?" + urlencode(old_query)
    db.execute("UPDATE sources SET url=?,config=?,last_attempt_at=? WHERE id=?",
               (old_url, '{"max_results": 40}',
                "2026-10-04T08:00:00+00:00", default["id"]))
    seed(db)
    changed = db.one("SELECT url,config,last_attempt_at FROM sources WHERE id=?", (default["id"],))
    assert "f_TPR=" not in changed["url"]
    assert changed["last_attempt_at"] is None
    assert __import__("json").loads(changed["config"])["max_results"] >= 150


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


def test_profile_skill_edit_rescores_existing_jobs(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    identifier, _ = ingest(db, source, ObservedJob("https://example.org/job", "AI Engineer", "Example", "Build C++ inference systems.", location="Hanoi"))
    before = client.get(f"/api/jobs/{identifier}").json()
    profile = client.get("/api/profile").json()
    profile["skills"] = ["C++"]
    assert client.put("/api/profile", json=profile).status_code == 200
    after = client.get(f"/api/jobs/{identifier}").json()
    import json
    assert "C++" in json.loads(after["score_detail"])["matched_skills"]
    assert after["score"] > before["score"]


def test_negative_role_is_capped_despite_matching_skills() -> None:
    score, detail = score_job({"title": "Sales Manager", "description": "AI research with Python.", "location": "Hanoi"}, {"skills": ["Python"], "location": "Hanoi"})
    assert score <= 20
    assert detail["excluded_role"] == "sales"
    assert "Excluded role" in detail["explanation"]


def test_literal_cpp_search_returns_matching_job(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "C++ Engineer",
        "description": "Build native systems.", "apply_url": "https://example.org/apply"}).json()
    response = client.get("/api/jobs", params={"q": "C++"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [job["id"]]
    assert client.get("/api/jobs", params={"q": "C%"}).json() == []
