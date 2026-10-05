from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.ingest import ObservedJob, ingest
from job_radar.ranking import score_job
from job_radar.web import create_app


def test_jobs_page_returns_lightweight_nonoverlapping_pages(tmp_path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    for number in range(27):
        client.post("/api/jobs/import", json={"company": "Example", "title": f"Engineer {number}",
                     "description": "Build Python systems in Hanoi."})
    first = client.get("/api/jobs/page", params={"page_size": 25}).json()
    second = client.get("/api/jobs/page", params={"page": 2, "page_size": 25}).json()
    assert first["total"] == 27 and first["pages"] == 2
    assert len(first["items"]) == 25 and len(second["items"]) == 2
    assert not ({job["id"] for job in first["items"]} & {job["id"] for job in second["items"]})
    assert "description" not in first["items"][0]


def test_first_run_seeds_employers_and_four_hour_linkedin_searches(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    status = client.get("/api/status").json()
    assert status["counts"]["employers"] >= 150
    searches = client.get("/api/sources?kind=linkedin").json()
    assert len(searches) == 9
    assert all(source["interval_minutes"] == 240 for source in searches)
    assert all("f_TPR=" not in source["url"] for source in searches)
    assert all("location=" not in source["url"] for source in searches)
    assert all("Hanoi" not in source["url"] and "Vietnam" not in source["url"]
               and "remote" not in source["url"].lower() for source in searches)
    assert all(source["config"]["max_results"] >= 150 for source in searches)
    assert not client.get("/api/sources?kind=facebook").json()
    gsm = next(row for row in client.get("/api/employers?q=GSM").json() if row["name"] == "GSM / Xanh SM")
    assert "GreenSM" in gsm["aliases"]


def test_old_location_searches_merge_into_one_title_feed_without_losing_jobs(tmp_path: Path) -> None:
    from job_radar.db import Database
    from job_radar.db import new_id, now
    from job_radar.seeds import ROLE_TERMS, seed
    from urllib.parse import urlencode
    import json

    db = Database(Settings(tmp_path).database_path)
    old_ids = []
    for role in ROLE_TERMS:
        for location in ("Hanoi, Vietnam", "Vietnam", "Remote"):
            source_id = new_id()
            old_ids.append(source_id)
            url = "https://www.linkedin.com/jobs/search/?" + urlencode(
                {"keywords": f"{role} in {location}"})
            db.execute("INSERT INTO sources(id,kind,name,url,config,created_at) VALUES(?,'linkedin',?,?,?,?)",
                       (source_id, f"{role} · {location}", url, '{"max_results": 150}', now()))
    job_id, _ = ingest(db, old_ids[1], ObservedJob(
        "https://www.linkedin.com/jobs/view/123456", "AI Engineer", "Example",
        "Build AI systems with Python."))
    seed(db)
    client = TestClient(create_app(Settings(tmp_path)))
    visible = client.get("/api/sources?kind=linkedin").json()
    assert len(visible) == 9
    assert {row["name"] for row in visible} == set(ROLE_TERMS)
    assert all(row["url"].endswith(urlencode({"keywords": row["name"]})) for row in visible)
    assert db.one("SELECT COUNT(*) AS count FROM sources WHERE kind='linkedin' AND enabled=1")["count"] == 9
    retired = db.all("SELECT config,enabled FROM sources WHERE kind='linkedin' AND name LIKE '% · %'")
    assert len(retired) == 18 and all(not row["enabled"] and json.loads(row["config"])["retired"] for row in retired)
    assert next(row for row in visible if row["name"] == "AI Engineer")["job_count"] == 1
    assert db.one("SELECT id FROM vacancies WHERE id=?", (job_id,))
    seed(db)
    assert db.one("SELECT COUNT(*) AS count FROM sources WHERE kind='linkedin'")["count"] == 27


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
    assert counts["active_sources"] == 52

    career = client.get("/api/sources?kind=career").json()[0]
    assert client.patch(f"/api/sources/{career['id']}", json={"enabled": False}).status_code == 200
    counts = client.get("/api/status").json()["counts"]
    assert counts["career_sources_enabled"] == 42
    assert counts["active_sources"] == 51


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
