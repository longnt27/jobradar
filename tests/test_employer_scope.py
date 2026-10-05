from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import Database, new_id, now
from job_radar.employer_scope import HCMC_BASED
from job_radar.seeds import EMPLOYERS, seed
from job_radar.settings import Settings
from job_radar.web import create_app


def test_hcm_based_companies_are_absent_from_new_directory(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    employers = client.get("/api/employers?limit=2000").json()
    names = {row["name"] for row in employers}
    assert names == set(EMPLOYERS)
    assert not names & HCMC_BASED.keys()
    assert {"Eximbank", "Vikki Bank", "Standard Chartered Vietnam"} <= names


def test_upgrade_disables_old_hcm_feed_and_hides_its_history(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    timestamp = now()
    employer_id, source_id, vacancy_id = new_id(), new_id(), new_id()
    with db.connection() as conn:
        conn.execute("INSERT INTO employers(id,name,category,created_at,updated_at) VALUES(?,?,?,?,?)",
                     (employer_id, "ACB", "bank", timestamp, timestamp))
        conn.execute("INSERT INTO sources(id,kind,name,url,employer_id,created_at) VALUES(?,?,?,?,?,?)",
                     (source_id, "career", "ACB careers", "https://acbjobs.talent.vn/jobs", employer_id, timestamp))
        conn.execute("INSERT INTO vacancies(id,employer_id,company,title,description,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (vacancy_id, employer_id, "ACB", "AI Engineer", "Build models", timestamp, timestamp, timestamp, timestamp))
    seed(db)
    client = TestClient(create_app(settings))
    assert not client.get("/api/employers?q=ACB").json()
    assert not client.get("/api/jobs").json()
    assert all(source["name"] != "ACB careers" for source in client.get("/api/sources?kind=career").json())
    assert db.one("SELECT enabled FROM sources WHERE id=?", (source_id,))["enabled"] == 0


def test_changing_career_url_disables_superseded_source(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    employer_id = client.post("/api/employers", json={"name": "Example QA", "career_url": "https://example.org/jobs/old"}).json()["id"]
    assert client.patch(f"/api/employers/{employer_id}", json={"career_url": "https://example.org/jobs/new"}).status_code == 200
    rows = client.app.state.db.all("SELECT url,enabled FROM sources WHERE employer_id=? ORDER BY url", (employer_id,))
    assert rows == [{"url": "https://example.org/jobs/new", "enabled": 1}, {"url": "https://example.org/jobs/old", "enabled": 0}]
    assert client.patch(f"/api/employers/{employer_id}", json={"career_url": "https://example.org/jobs/old"}).status_code == 200
    rows = client.app.state.db.all("SELECT url,enabled FROM sources WHERE employer_id=? ORDER BY url", (employer_id,))
    assert rows == [{"url": "https://example.org/jobs/new", "enabled": 0}, {"url": "https://example.org/jobs/old", "enabled": 1}]


def test_employer_directory_page_paginates_and_filters(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    for index in range(55):
        response = client.post("/api/employers", json={
            "name": f"Paging Employer {index:03d}",
            "category": "paging-test",
        })
        assert response.status_code == 201

    first = client.get("/api/employers/page", params={
        "q": "Paging Employer", "page": 1, "page_size": 20,
    }).json()
    second = client.get("/api/employers/page", params={
        "q": "Paging Employer", "page": 2, "page_size": 20,
    }).json()
    last = client.get("/api/employers/page", params={
        "q": "Paging Employer", "page": 3, "page_size": 20,
    }).json()

    assert first["total"] == 55
    assert first["pages"] == 3
    assert len(first["items"]) == 20
    assert len(second["items"]) == 20
    assert len(last["items"]) == 15
    assert {row["id"] for row in first["items"]}.isdisjoint(row["id"] for row in second["items"])
    assert first["items"][0]["name"] == "Paging Employer 000"
    assert second["items"][0]["name"] == "Paging Employer 020"
