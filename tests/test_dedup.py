import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_radar.db import Database, new_id, now
from job_radar.dedup import deduplicate_pool, merge_vacancies, posting_key
from job_radar.discovery import split_observation
from job_radar.ingest import ObservedJob, ingest
from job_radar.settings import Settings
from job_radar.web import create_app


DESCRIPTION = (
    "Build and maintain production computer vision models for robotics perception. "
    "Develop detection, segmentation, tracking, and model evaluation pipelines with Python and PyTorch. "
    "Collaborate with engineers to deploy inference services, monitor accuracy, improve latency, "
    "design reproducible experiments, and document technical decisions for customers."
)


@pytest.fixture
def pool(tmp_path):
    db = Database(tmp_path / "jobs.sqlite3")
    sources = {}
    for kind in ("career", "linkedin", "facebook"):
        sources[kind] = new_id()
        db.execute("INSERT INTO sources(id,kind,name,url,created_at) VALUES(?,?,?,?,?)",
                   (sources[kind], kind, kind, f"https://example.org/{kind}", now()))
    return db, sources


def posting(url="https://acme.talent.vn/job/vision-engineer-42", **kwargs):
    return ObservedJob(url, "AI Engineer (Computer Vision)", "Acme Robotics", DESCRIPTION, **kwargs)


def test_new_career_linkedin_and_facebook_sightings_combine_and_rescans_prefer_career(pool):
    db, sources = pool
    career = posting(location="Hanoi", apply_url="https://acme.talent.vn/job/vision-engineer-42")
    identifier, _ = ingest(db, sources["career"], career)
    linkedin = replace(career, url="https://www.linkedin.com/jobs/view/123", title="Computer Vision AI Engineer", description=DESCRIPTION + " Apply through LinkedIn.")
    assert ingest(db, sources["linkedin"], linkedin) == (identifier, False)
    facebook = replace(career, url="https://www.facebook.com/groups/one/posts/456", company="Facebook post")
    assert ingest(db, sources["facebook"], facebook) == (identifier, False)
    assert ingest(db, sources["linkedin"], linkedin) == (identifier, False)
    row = db.one("SELECT title,description,apply_url FROM vacancies WHERE id=?", (identifier,))
    assert row == {"title": career.title, "description": DESCRIPTION, "apply_url": career.apply_url}
    assert db.one("SELECT COUNT(*) AS n FROM observations")["n"] == 3
    assert db.one("SELECT COUNT(*) AS n FROM vacancies")["n"] == 1


def test_rescan_combines_a_previous_short_sighting_when_full_description_arrives(pool):
    db, sources = pool
    career = posting(location="Hanoi")
    identifier, _ = ingest(db, sources["career"], career)
    social = replace(career, url="https://www.linkedin.com/jobs/view/2468", description="A short teaser without enough matching evidence.")
    separate, _ = ingest(db, sources["linkedin"], social)
    assert separate != identifier
    social.description = DESCRIPTION
    combined, is_new = ingest(db, sources["linkedin"], social)
    assert not is_new
    assert db.one("SELECT COUNT(*) AS n FROM vacancies")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM observations")["n"] == 2
    assert db.one("SELECT description FROM vacancies WHERE id=?", (combined,))["description"] == DESCRIPTION


def test_new_multi_role_announcements_keep_roles_distinct_and_merge_social_sightings(pool):
    from job_radar.baoviet import parse_baoviet_announcement

    db, sources = pool
    html = f'''<p>Hòm thư nhận CV: careers@baoviet.com.vn</p><table>
      <tr><td>Mã số: CV-01<br>Chức danh: AI Engineer (Computer Vision)<br>Nơi làm việc: Hanoi</td>
          <td>{DESCRIPTION}</td><td>Production model evaluation and Python experience.</td></tr>
      <tr><td>Mã số: DE-02<br>Chức danh: Data Engineer<br>Nơi làm việc: Hanoi</td>
          <td>Build production data pipelines, warehouses and orchestration systems with SQL and Python.</td>
          <td>Data modelling, ETL, database administration and reliable data platform operations.</td></tr>
    </table>'''
    jobs = parse_baoviet_announcement(html, "https://baoviet.com.vn/vi/it-recruitment-2026")
    first = ingest(db, sources["career"], jobs[0])[0]
    second = ingest(db, sources["career"], jobs[1])[0]
    assert first != second
    social = replace(jobs[0], url="https://www.linkedin.com/jobs/view/314159", external_id="314159")
    assert ingest(db, sources["linkedin"], social) == (first, False)
    assert ingest(db, sources["career"], jobs[0]) == (first, False)
    assert db.one("SELECT COUNT(*) AS n FROM vacancies")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM observations")["n"] == 3
    with db.connection() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("change", [
    {"title": "Junior AI Engineer (Computer Vision)"},
    {"title": "Senior AI Engineer (LLM)"},
    {"location": "Ho Chi Minh City"},
    {"company": "Different Employer"},
    {"published_at": "2027-10-01T00:00:00Z"},
])
def test_contradictions_stay_distinct_even_with_identical_description(pool, change):
    db, sources = pool
    original = replace(posting(location="Hanoi", published_at="2026-10-01T00:00:00Z"), title="Senior AI Engineer (Computer Vision)")
    first, _ = ingest(db, sources["career"], original)
    second = replace(original, url="https://www.linkedin.com/jobs/view/222", **change)
    assert ingest(db, sources["linkedin"], second)[0] != first


def test_aliases_and_different_posting_urls_match(pool):
    db, sources = pool
    db.execute("INSERT INTO employers(id,name,category,aliases,created_at,updated_at) VALUES(?,?,?,?,?,?)",
               (new_id(), "Acme Robotics", "technology", '["Acme Robotics JSC"]', now(), now()))
    first, _ = ingest(db, sources["career"], posting())
    social = replace(posting(), url="https://www.linkedin.com/jobs/view/111", company="Acme Robotics JSC")
    assert ingest(db, sources["linkedin"], social) == (first, False)


def test_same_ats_different_requisitions_do_not_merge(pool):
    db, sources = pool
    first, _ = ingest(db, sources["career"], posting(external_id="42"))
    other = replace(posting(external_id="43"), url="https://acme.talent.vn/job/vision-engineer-43")
    assert ingest(db, sources["career"], other)[0] != first


@pytest.mark.parametrize("titles", [
    ("AI Motion Engineer", "AI Motion Intern"),
    ("Business Analyst - Sản phẩm CRM", "Business Analyst - Sản phẩm AMIS Chat"),
    ("Agentic Engineer (2026TD450887)", "Agentic Engineer (2026TD450888)"),
    ("MNQ322 PQA Assistant Engineer", "MNQ162 PQA Assistant Engineer"),
    ("Field Application Scientist - LC-MS", "Field Application Scientist - GC/GCMS"),
    ("Senior Staff Computer Vision Engineer", "Senior Computer Vision Engineer"),
])
def test_distinct_roles_from_real_pool_do_not_merge_even_with_copied_content(pool, titles):
    db, sources = pool
    first = replace(posting(apply_url="https://acme.vn/jobs/shared-application-42"), title=titles[0])
    identifier, _ = ingest(db, sources["career"], first)
    other = replace(first, url="https://www.linkedin.com/jobs/view/112233", title=titles[1])
    assert ingest(db, sources["linkedin"], other)[0] != identifier


@pytest.mark.parametrize("apply_url", ["mailto:jobs@acme.vn", "https://acme.vn/jobs", "https://acme.vn/apply"])
def test_shared_contact_or_generic_application_is_not_identity(pool, apply_url):
    db, sources = pool
    first, _ = ingest(db, sources["career"], posting(apply_url=apply_url))
    other = replace(posting(apply_url=apply_url), url="https://www.linkedin.com/jobs/view/444", description="Work on sensor integration and a separate manufacturing project. " * 8)
    assert ingest(db, sources["linkedin"], other)[0] != first


def test_specific_application_link_requires_matching_requirements(pool):
    db, sources = pool
    first, _ = ingest(db, sources["career"], posting(apply_url="https://acme.talent.vn/job/vision-engineer-42"))
    other = replace(posting(apply_url="https://acme.talent.vn/job/vision-engineer-42"), url="https://www.linkedin.com/jobs/view/555", description="Work on sensor integration and a separate manufacturing project. " * 8)
    assert ingest(db, sources["linkedin"], other)[0] != first


def test_distinct_international_locations_and_work_modes_do_not_merge(pool):
    db, sources = pool
    first = posting(location="New York, NY (On-site)")
    identifier, _ = ingest(db, sources["career"], first)
    elsewhere = replace(first, url="https://www.linkedin.com/jobs/view/9898", location="Richmond, VA (On-site)")
    assert ingest(db, sources["linkedin"], elsewhere)[0] != identifier
    remote = replace(first, url="https://www.linkedin.com/jobs/view/9899", location="New York, NY (Remote)")
    assert ingest(db, sources["linkedin"], remote)[0] != identifier
    vietnam = replace(first, url="https://www.linkedin.com/jobs/view/9900", location="Hanoi")
    assert ingest(db, sources["linkedin"], vietnam)[0] != identifier


def test_split_and_distinct_decisions_survive_backfill_and_future_scans(pool):
    db, sources = pool
    first, _ = ingest(db, sources["career"], posting())
    social = replace(posting(), url="https://www.linkedin.com/jobs/view/666")
    assert ingest(db, sources["linkedin"], social)[0] == first
    observation = db.one("SELECT id FROM observations WHERE source_id=?", (sources["linkedin"],))["id"]
    second = split_observation(db, first, observation)
    assert ingest(db, sources["linkedin"], social)[0] == second
    assert deduplicate_pool(db, apply=True)["merged"] == []


def test_backfill_is_atomic_preserves_history_and_is_idempotent(pool):
    db, sources = pool
    first, _ = ingest(db, sources["career"], posting())
    # Manufacture an older pre-dedup duplicate with a full independent sighting.
    second = new_id()
    original = db.one("SELECT * FROM vacancies WHERE id=?", (first,))
    original["id"] = second
    with db.connection() as conn:
        conn.execute(f"INSERT INTO vacancies({','.join(original)}) VALUES({','.join('?' for _ in original)})", tuple(original.values()))
        observation = new_id()
        social = replace(posting(), url="https://www.linkedin.com/jobs/view/777")
        conn.execute("INSERT INTO observations(id,source_id,url,content_hash,raw_text,payload,first_seen_at,last_seen_at) VALUES(?,?,?,'digest',?,?,?,?)", (observation, sources["linkedin"], social.url, DESCRIPTION, json.dumps(social.__dict__), now(), now()))
        conn.execute("INSERT INTO vacancy_observations VALUES(?,?,'new_vacancy')", (second, observation))
        conn.execute("INSERT INTO vacancy_fts VALUES(?,?,?,?)", (second, social.title, social.company, DESCRIPTION))
    db.execute("INSERT INTO feedback VALUES(?,?,'shortlisted','Relevant robotics work',?)", (new_id(), second, now()))
    db.execute("UPDATE vacancies SET decision_state='shortlisted' WHERE id=?", (second,))
    for identifier in (first, second):
        db.execute("INSERT INTO notification_attempts(vacancy_id,channel,status,attempts) VALUES(?,'telegram','sent',1)", (identifier,))
        draft = new_id()
        db.execute("INSERT INTO application_drafts(id,vacancy_id,status,provider,provider_mode,evidence_ids,resume_data,message_data,form_data,destination,created_at,updated_at) VALUES(?,?,'sent','local','template','[]','{}','{}','{}','{}',?,?)",
                   (draft, identifier, now(), now()))
        db.execute("INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,destination,status,sent_at,updated_at) VALUES(?,?,?,'original-package','{}','sent',?,?)",
                   (new_id(), draft, identifier, now(), now()))
        db.execute("INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,created_at,updated_at) VALUES(?,'sent',?,?,?)",
                   (identifier, draft, now(), now()))
    before = deduplicate_pool(db)
    assert len(before["merged"]) == 1
    assert db.one("SELECT COUNT(*) AS n FROM vacancies")["n"] == 2
    result = deduplicate_pool(db, apply=True)
    assert result["before"] == 2 and result["after"] == 1
    survivor = result["merged"][0]["survivor_id"]
    assert db.one("SELECT COUNT(*) AS n FROM observations")["n"] == 2
    assert db.one("SELECT vacancy_id FROM feedback")["vacancy_id"] == survivor
    assert db.one("SELECT decision_state FROM vacancies")["decision_state"] == "shortlisted"
    assert db.one("SELECT COUNT(*) AS n FROM notification_attempts")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM vacancy_merge_log")["n"] == 1
    assert db.one("SELECT vacancy_id FROM vacancy_redirects")["vacancy_id"] == survivor
    assert db.all("SELECT DISTINCT vacancy_id FROM application_drafts") == [{"vacancy_id": survivor}]
    assert db.one("SELECT COUNT(*) AS n FROM application_drafts")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM submissions WHERE vacancy_id=? AND package_hash='original-package'", (survivor,))["n"] == 2
    assert db.one("SELECT status FROM auto_application_attempts")["status"] == "sent"
    with db.connection() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert deduplicate_pool(db, apply=True)["merged"] == []
    assert ingest(db, sources["linkedin"], social) == (survivor, False)


def test_possible_duplicate_review_and_old_links(tmp_path: Path):
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    a = client.post("/api/jobs/import", json={"company": "Acme Robotics", "title": "AI Engineer", "description": DESCRIPTION}).json()["id"]
    b = client.post("/api/jobs/import", json={"company": "Acme Robotics", "title": "AI Engineer", "description": "A different short summary of the advertised job."}).json()["id"]
    result = deduplicate_pool(app.state.db, apply=True)
    assert len(result["possible"]) == 1 and not result["merged"]
    assert client.get(f"/api/jobs/{a}").json()["possible_duplicates"][0]["id"] == b
    assert client.post(f"/api/jobs/{a}/duplicates/{b}/distinct").status_code == 200
    assert deduplicate_pool(app.state.db, apply=True)["possible"] == []
    app.state.db.execute("UPDATE duplicate_reviews SET status='possible'")
    response = client.post(f"/api/jobs/{a}/duplicates/{b}/merge")
    assert response.status_code == 200, response.text
    survivor = response.json()["id"]
    assert client.get(f"/api/jobs/{a}").json()["id"] == survivor
    assert client.get(f"/api/jobs/{b}").json()["id"] == survivor


def test_posting_keys_reject_shared_pages_and_normalize_tracking():
    assert not posting_key("mailto:jobs@example.com")
    assert not posting_key("https://example.com/jobs")
    assert not posting_key("https://example.com/Job/Search")
    assert posting_key("https://www.linkedin.com/jobs/view/ai-engineer-123/?trk=x") == posting_key("https://linkedin.com/jobs/view/123")
    assert posting_key("https://acme.talent.vn/job/ai-engineer-42?utm_source=linkedin") == posting_key("https://acme.talent.vn/job/ai-engineer-42")


@pytest.mark.parametrize("role_code", ["Mã số: CV-01<br>", ""])
def test_announcements_with_reused_local_role_identity_stay_distinct(tmp_path, role_code):
    from job_radar.baoviet import parse_baoviet_announcement
    from job_radar.seeds import seed

    db = Database(tmp_path / "jobs.db")
    seed(db)
    source_id = db.one("SELECT id FROM sources WHERE name='Bao Viet careers'")["id"]
    for announcement, description in (
        ("language-model-team", "Train multilingual language models using transformer architectures and distributed GPU infrastructure. " * 5),
        ("fraud-model-team", "Detect financial fraud through transaction graph analysis and statistical anomaly detection. " * 5),
    ):
        html = (f"<table><tr><td>{role_code}Chức danh: AI Engineer<br>Nơi làm việc: Hanoi</td>"
                f"<td>{description}</td><td>Python engineering experience</td></tr></table>")
        job = parse_baoviet_announcement(html, f"https://baoviet.com.vn/vi/{announcement}-tuyen-dung")[0]
        ingest(db, source_id, job)
        ingest(db, source_id, job)
    assert db.one("SELECT COUNT(*) AS n FROM vacancies")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM observations")["n"] == 2
    assert deduplicate_pool(db, apply=True)["merged"] == []
