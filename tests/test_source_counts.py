from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import new_id
from job_radar.ingest import ObservedJob, ingest
from job_radar.settings import Settings
from job_radar.web import create_app


def test_source_list_counts_distinct_jobs_and_new_jobs_in_latest_scan(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    sources = db.all("SELECT id FROM sources WHERE kind='linkedin' ORDER BY name LIMIT 3")
    first, second, untouched = [source["id"] for source in sources]
    shared = ObservedJob("https://www.linkedin.com/jobs/view/100/", "AI Engineer", "Acme",
                         "Build production machine learning services with Python.")
    other = ObservedJob("https://www.linkedin.com/jobs/view/200/", "Data Analyst", "Beta",
                        "Analyze customer data and build SQL dashboards.")
    ingest(db, first, shared)
    ingest(db, first, other)
    ingest(db, second, shared)
    db.execute("UPDATE observations SET first_seen_at='2026-10-04T08:00:00+00:00' WHERE source_id=? AND url=?",
               (first, shared.url.rstrip("/")))
    db.execute("UPDATE observations SET first_seen_at='2026-10-04T09:00:00+00:00' WHERE source_id=? AND url=?",
               (first, other.url.rstrip("/")))
    db.execute("UPDATE observations SET first_seen_at='2026-10-04T09:00:00+00:00' WHERE source_id=?",
               (second,))
    for source_id, started, finished, new_count in (
        (first, "2026-10-04T07:59:00+00:00", "2026-10-04T08:01:00+00:00", 1),
        (first, "2026-10-04T08:59:00+00:00", "2026-10-04T09:01:00+00:00", 1),
        (second, "2026-10-04T08:59:00+00:00", "2026-10-04T09:01:00+00:00", 0),
    ):
        db.execute("INSERT INTO scan_runs(id,source_id,started_at,finished_at,status,new_count) "
                   "VALUES(?,?,?,?,'success',?)", (new_id(), source_id, started, finished, new_count))
        db.execute("UPDATE sources SET last_success_at=? WHERE id=?", (finished, source_id))

    listed = {source["id"]: source for source in TestClient(app).get("/api/sources?kind=linkedin").json()}
    assert (listed[first]["job_count"], listed[first]["new_job_count"]) == (2, 1)
    assert (listed[second]["job_count"], listed[second]["new_job_count"]) == (0, 0)
    assert (listed[untouched]["job_count"], listed[untouched]["new_job_count"]) == (0, 0)

    db.execute("INSERT INTO scan_runs(id,source_id,started_at,finished_at,status,new_count) "
               "VALUES(?,?,?,?,'success',0)",
               (new_id(), first, "2026-10-04T10:00:00+00:00", "2026-10-04T10:01:00+00:00"))
    refreshed = {source["id"]: source for source in TestClient(app).get("/api/sources?kind=linkedin").json()}
    assert (refreshed[first]["job_count"], refreshed[first]["new_job_count"]) == (2, 0)
