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
