import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.ingest import ObservedJob, ingest
from job_radar.ranking import score_job
from job_radar.web import create_app


def test_upgrade_quarantines_untouched_linkedin_join_pages(tmp_path: Path) -> None:
    from job_radar.db import Database, new_id, now

    path = Settings(tmp_path).database_path
    db = Database(path)
    timestamp = now()
    suspicious = new_id()
    genuine = new_id()
    for job_id, description in (
        (suspicious, "Join LinkedIn Email Password (6+ characters) By clicking Agree & Join"),
        (genuine, "A genuine job posting for an AI engineer at a company."),
    ):
        db.execute(
            "INSERT INTO vacancies(id,company,title,description,first_seen_at,last_seen_at,"
            "analysis_status,created_at,updated_at) VALUES(?,?,'Join LinkedIn',?,?,?,'pending',?,?)",
            (job_id, "Unknown employer", description, timestamp, timestamp, timestamp, timestamp),
        )
    db.execute("DELETE FROM settings WHERE key='linkedin_join_quarantine_v1'")

    upgraded = Database(path)
    junk = upgraded.one("SELECT decision_state,analysis_status,score FROM vacancies WHERE id=?", (suspicious,))
    good = upgraded.one("SELECT decision_state,analysis_status FROM vacancies WHERE id=?", (genuine,))
    assert junk == {"decision_state": "ignored", "analysis_status": "dismissed", "score": None}
    assert good == {"decision_state": "undecided", "analysis_status": "pending"}


def test_browser_assets_use_current_content_versions_after_an_update(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    html = client.get("/")
    assert html.status_code == 200
    assert html.headers["cache-control"] == "no-store"
    static = Path(__file__).parents[1] / "job_radar" / "static"
    for asset, attribute in (("app.js", "src"), ("app.css", "href")):
        version = hashlib.sha256((static / asset).read_bytes()).hexdigest()[:12]
        assert f'{attribute}="/{asset}?v={version}"' in html.text
        response = client.get(f"/{asset}?v={version}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


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


def test_jobs_pages_put_completed_matches_before_analyzing_jobs(tmp_path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    ids = [client.post("/api/jobs/import", json={"company": "Example", "title": f"AI Engineer {number}",
           "description": "Build Python systems in Hanoi."}).json()["id"] for number in range(3)]
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=40 WHERE id=?", (ids[0],))
    app.state.db.execute("UPDATE vacancies SET analysis_status='pending',score=99 WHERE id=?", (ids[1],))
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=90 WHERE id=?", (ids[2],))
    pages = [client.get("/api/jobs/page", params={"page": page, "page_size": 1}).json()["items"][0]["id"]
             for page in (1, 2, 3)]
    assert pages == [ids[2], ids[0], ids[1]]
    assert [job["id"] for job in client.get("/api/jobs").json()] == pages


def test_first_run_seeds_employers_and_recent_twelve_hour_linkedin_searches(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    status = client.get("/api/status").json()
    assert status["counts"]["employers"] >= 150
    searches = client.get("/api/sources?kind=linkedin").json()
    assert len(searches) == 9
    assert all(source["interval_minutes"] == 720 for source in searches)
    assert all("f_TPR=r86400" in source["url"] for source in searches)
    assert all("location=" not in source["url"] for source in searches)
    assert all("Hanoi" not in source["url"] and "Vietnam" not in source["url"]
               and "remote" not in source["url"].lower() for source in searches)
    assert all(source["config"]["max_results"] >= 150 for source in searches)
    assert not client.get("/api/sources?kind=facebook").json()
    gsm = next(row for row in client.get("/api/employers?q=GSM").json() if row["name"] == "GSM / Xanh SM")
    assert "GreenSM" in gsm["aliases"]


def test_existing_linkedin_searches_gain_recent_filter_and_twelve_hour_schedule(tmp_path: Path) -> None:
    from job_radar.db import Database, new_id, now

    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    db.execute(
        "INSERT INTO sources(id,kind,name,url,interval_minutes,config,created_at) "
        "VALUES(?,'linkedin','AI Engineer','https://www.linkedin.com/jobs/search/?keywords=AI+Engineer',240,'{}',?)",
        (new_id(), now()),
    )
    client = TestClient(create_app(settings))
    matching = [source for source in client.get("/api/sources?kind=linkedin").json()
                if source["name"] == "AI Engineer"]
    assert len(matching) == 1
    assert matching[0]["interval_minutes"] == 720
    assert "f_TPR=r86400" in matching[0]["url"]


def test_paused_linkedin_sources_do_not_start_browser_checks(tmp_path: Path) -> None:
    import asyncio

    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("linkedin_automation_paused", True)
    client = TestClient(app)
    source = client.get("/api/sources?kind=linkedin").json()[0]
    assert source["scan_state"] == "paused"
    assert source["coverage"]["label"] == "LinkedIn checks paused"
    assert client.get("/api/setup").json()["linkedin_automation_paused"]
    response = client.post(f"/api/sources/{source['id']}/scan")
    assert response.status_code == 409
    assert asyncio.run(app.state.scan_manager.run_source(source["id"]))["status"] == "paused"


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
    assert all(row["url"].endswith(urlencode({"keywords": row["name"], "f_TPR": "r86400"})) for row in visible)
    assert all(row["interval_minutes"] == 720 for row in visible)
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


def test_negative_role_requires_explicit_user_preference() -> None:
    job = {"title": "Sales Manager", "description": "AI research with Python.", "location": "Hanoi"}
    profile = {"skills": ["Python"], "location": "Hanoi"}
    score, detail = score_job(job, profile)
    assert score > 20
    assert detail["hard_exclusions"] == []

    deprioritized, detail = score_job(job, profile, {"negative_keywords": ["sales"]})
    assert deprioritized < score
    assert detail["hard_exclusions"] == []


def test_literal_cpp_search_returns_matching_job(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "C++ Engineer",
        "description": "Build native systems.", "apply_url": "https://example.org/apply"}).json()
    response = client.get("/api/jobs", params={"q": "C++"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [job["id"]]
    assert client.get("/api/jobs", params={"q": "C%"}).json() == []



def test_jobs_page_supports_discovery_filters_and_sorting(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timedelta, timezone

    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    source = db.one("SELECT id,kind FROM sources WHERE enabled=1 LIMIT 1")
    assert source is not None

    senior_id, _ = ingest(db, source["id"], ObservedJob(
        "https://example.org/senior", "Senior AI Engineer", "Zulu Robotics",
        "Build Python systems.", location="Hanoi"))
    junior_id, _ = ingest(db, source["id"], ObservedJob(
        "https://example.org/junior", "Junior Engineer", "Alpha Labs",
        "Build backend systems.", location="Da Nang"))

    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    db.execute(
        "UPDATE vacancies SET score=92,analysis_status='done',work_mode='Remote',published_at=?,score_detail=? WHERE id=?",
        (recent, json.dumps({"facts":{"seniority":"Senior","work_mode":"Remote"},
                            "criteria":{"role":{"score":9,"reason":"Strong role fit"}}}), senior_id),
    )
    db.execute(
        "UPDATE vacancies SET score=55,analysis_status='done',work_mode='On-site',published_at=?,score_detail=? WHERE id=?",
        (old, json.dumps({"facts":{"seniority":"Junior","work_mode":"On-site"},
                         "criteria":{"role":{"score":5,"reason":"Partial role fit"}}}), junior_id),
    )

    assert [row["id"] for row in client.get("/api/jobs/page", params={"min_score": 80}).json()["items"]] == [senior_id]
    assert [row["id"] for row in client.get("/api/jobs/page", params={"freshness": 1}).json()["items"]] == [senior_id]
    assert [row["id"] for row in client.get("/api/jobs/page", params={"work_mode": "remote"}).json()["items"]] == [senior_id]
    assert [row["id"] for row in client.get("/api/jobs/page", params={"location": "Hanoi"}).json()["items"]] == [senior_id]
    assert [row["id"] for row in client.get("/api/jobs/page", params={"seniority": "senior"}).json()["items"]] == [senior_id]
    source_rows = client.get("/api/jobs/page", params={"source": source["kind"]}).json()["items"]
    assert {row["id"] for row in source_rows} >= {senior_id, junior_id}
    company_sorted = client.get("/api/jobs/page", params={"sort": "company"}).json()["items"]
    selected = [row["company"] for row in company_sorted if row["id"] in {senior_id, junior_id}]
    assert selected == ["Alpha Labs", "Zulu Robotics"]
    senior = next(row for row in client.get("/api/jobs/page", params={"q": "Senior AI"}).json()["items"] if row["id"] == senior_id)
    assert senior["work_mode"] == "Remote"
    assert senior["seniority"] == "Senior"
    assert senior["match_signals"][0]["label"] == "Role"
