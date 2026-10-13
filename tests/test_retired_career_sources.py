import json
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import new_id, now
from job_radar.feed_catalog import RETIRED_CAREER_FEEDS
from job_radar.ingest import ObservedJob, ingest
from job_radar.seeds import seed
from job_radar.settings import Settings
from job_radar.web import create_app


def test_upgrade_retires_inaccessible_sources_without_losing_history(tmp_path: Path):
    app = create_app(Settings(tmp_path))
    db = app.state.db
    employer_id = db.one("SELECT id FROM employers WHERE name='Agoda'")["id"]
    source_ids = []
    for index, url in enumerate(RETIRED_CAREER_FEEDS):
        source_id = new_id()
        source_ids.append(source_id)
        db.execute(
            "INSERT INTO sources(id,kind,name,url,employer_id,config,created_at) "
            "VALUES(?,'career',?,?,?, ?,?)",
            (source_id, f"Legacy career feed {index}", url, employer_id,
             '{"adapter":"html_board","max_pages":3}', now()),
        )
        ingest(db, source_id, ObservedJob(
            url=f"https://example.com/jobs/retired-{index}", title=f"AI Engineer {index}",
            company="Agoda", description="Build production machine learning services with Python.",
        ))
        db.execute("INSERT INTO scan_runs(id,source_id,started_at,status) VALUES(?,?,?,'error')",
                   (new_id(), source_id, now()))

    alternative_id = new_id()
    db.execute(
        "INSERT INTO sources(id,kind,name,url,employer_id,created_at) VALUES(?,'career',?,?,?,?)",
        (alternative_id, "Alternative career source", "https://example.com/careers", employer_id, now()),
    )
    history = {table: db.all(f"SELECT * FROM {table} ORDER BY id")
               for table in ("observations", "vacancies", "scan_runs")}

    seed(db)
    seed(db)
    client = TestClient(app)
    listed = {source["id"] for source in client.get("/api/sources?kind=career").json()}
    assert not listed.intersection(source_ids)
    assert alternative_id in listed
    for source_id in source_ids:
        source = db.one("SELECT enabled,config,url FROM sources WHERE id=?", (source_id,))
        config = json.loads(source["config"])
        assert source["enabled"] == 0
        assert config["retired"] is True
        assert config["retirement_reason"] == RETIRED_CAREER_FEEDS[source["url"]]
        assert config["max_pages"] == 3
    assert history == {table: db.all(f"SELECT * FROM {table} ORDER BY id") for table in history}
    assert db.all("PRAGMA foreign_key_check") == []


def test_fresh_install_has_no_retired_feeds(tmp_path: Path):
    client = TestClient(create_app(Settings(tmp_path)))
    sources = client.get("/api/sources?kind=career").json()
    assert len(sources) == 123
    assert not {source["url"] for source in sources}.intersection(RETIRED_CAREER_FEEDS)
