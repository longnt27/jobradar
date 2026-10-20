import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from playwright.sync_api import sync_playwright

from fastapi.testclient import TestClient

from job_radar.db import new_id, now
from job_radar.home_dashboard import build_home_dashboard
from job_radar.settings import Settings
from job_radar.web import create_app


def test_status_exposes_decision_centric_home_counts(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    job = client.post("/api/jobs/import", json={
        "company": "Example", "title": "AI Engineer",
        "description": "Build Python AI systems in Hanoi."
    }).json()
    detail = {
        "facts": {"required_skills": ["Python"], "years_required": 1, "location": "Hanoi",
                  "work_mode": "Hybrid", "education": ["Bachelor"]},
        "criteria": {
            "role": {"score": 9, "reason": "Direct role fit"},
            "required_skills": {"score": 9, "reason": "Python is documented"},
            "experience": {"score": 8, "reason": "Experience fits"},
            "location": {"score": 10, "reason": "Location fits"},
            "work_mode": {"score": 8, "reason": "Hybrid fits"},
            "education": {"score": 8, "reason": "Education fits"},
        },
    }
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='done',score=91,score_detail=?,state='new' WHERE id=?",
        (json.dumps(detail), job["id"]),
    )
    failed = client.post("/api/jobs/import", json={
        "company": "Broken Co", "title": "ML Engineer",
        "description": "Build ML systems."
    }).json()
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='failed',analysis_error='model error' WHERE id=?",
        (failed["id"],),
    )
    status = client.get("/api/status").json()
    assert status["counts"]["high_fit_new"] == 1
    assert status["counts"]["recent_jobs"] >= 2
    assert status["counts"]["analysis_failures"] == 1
    assert status["attention"]["jobs"][0]["id"] == job["id"]
    assert status["attention"]["jobs"][0]["fit_class"] == "strong"
    assert status["strong_match_threshold"] == 80
    assert status["attention"]["failures"][0]["id"] == failed["id"]


def test_home_uses_configured_strong_match_threshold(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    assert client.put("/api/search-intent", json={
        "strong_match_threshold": 90,
        "role_families": [],
        "seniority_levels": [],
        "preferred_locations": [],
        "work_modes": [],
        "preferred_employers": [],
        "excluded_employers": [],
        "negative_keywords": [],
        "hard_constraints": {},
        "minimum_salary": None,
        "salary_currency": "VND",
        "salary_unknown_ok": True,
    }).status_code == 200
    job = client.post("/api/jobs/import", json={
        "company": "Example", "title": "AI Engineer", "description": "Build Python AI systems."
    }).json()
    detail = {
        "facts": {"required_skills": ["Python"], "years_required": 1, "location": "Hanoi",
                  "work_mode": "Hybrid", "education": ["Bachelor"]},
        "criteria": {"role": {"score": 9, "reason": "Direct role fit"},
                     "required_skills": {"score": 9, "reason": "Python fits"},
                     "experience": {"score": 8, "reason": "Experience fits"},
                     "location": {"score": 9, "reason": "Location fits"},
                     "work_mode": {"score": 8, "reason": "Mode fits"},
                     "education": {"score": 8, "reason": "Education fits"}},
    }
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=85,score_detail=?,state='new' WHERE id=?",
                         (json.dumps(detail), job["id"]))
    status = client.get("/api/status").json()
    assert status["strong_match_threshold"] == 90
    assert status["counts"]["high_fit_new"] == 0
    app.state.db.execute("UPDATE vacancies SET score=92 WHERE id=?", (job["id"],))
    status = client.get("/api/status").json()
    assert status["counts"]["high_fit_new"] == 1



def _seed_home_job(app, title: str, score: int = 90, *, seen: bool = False) -> str:
    identifier = new_id()
    timestamp = now()
    detail = {
        "facts": {"required_skills": ["Python"], "location": "Hanoi", "work_mode": "Hybrid"},
        "criteria": {
            "role": {"score": 9, "reason": "Role matches"},
            "required_skills": {"score": 9, "reason": "Python matches"},
            "experience": {"score": 8, "reason": "Experience matches"},
            "location": {"score": 9, "reason": "Location matches"},
            "work_mode": {"score": 8, "reason": "Mode matches"},
            "education": {"score": 8, "reason": "Education matches"},
        },
    }
    app.state.db.execute(
        "INSERT INTO vacancies(id,company,title,description,first_seen_at,last_seen_at,score,score_detail,"
        "analysis_status,decision_state,seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identifier, "Example", title, "Build Python AI systems.", timestamp, timestamp, score,
            json.dumps(detail), "done", "undecided", timestamp if seen else None, timestamp, timestamp,
        ),
    )
    return identifier


def test_home_health_reflects_discovery_and_analysis_backlog(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    degraded = build_home_dashboard(app.state.db, {"level": "limited"}, 80)
    assert degraded["health"]["degraded"] is True

    pending = _seed_home_job(app, "Still Reviewing", 0, seen=True)
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='pending',score=NULL,score_detail=NULL WHERE id=?",
        (pending,),
    )
    processing = build_home_dashboard(app.state.db, {"level": "good"}, 80)
    assert processing["health"]["degraded"] is True
    assert processing["health"]["analysis"]["pending"] == 1

def test_home_counts_and_daily_loop_route_to_canonical_filtered_workflows(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    _seed_home_job(app, "Unseen Strong", 92)
    shortlisted = _seed_home_job(app, "Shortlisted Ready", 88, seen=True)
    app.state.db.execute(
        "UPDATE vacancies SET decision_state='shortlisted' WHERE id=?",
        (shortlisted,),
    )
    dashboard = build_home_dashboard(
        app.state.db,
        {"level": "good", "label": "Discovery coverage looks normal"},
        80,
    )

    assert dashboard["counts"]["unseen_jobs"] == 1
    assert dashboard["counts"]["strong_matches"] == 2
    assert dashboard["counts"]["ready_to_prepare"] == 1
    stages = {stage["key"]: stage for stage in dashboard["stages"]}
    assert stages["triage"]["route"] == {"tab": "jobs", "params": {"inbox": "unseen", "fit": "all"}}
    assert stages["prepare"]["count"] == 1
    assert stages["prepare"]["route"]["params"]["decision"] == "shortlisted"
    assert stages["review"]["route"]["tab"] == "applications"
    assert stages["track"]["route"]["params"]["application"] == "applied"
    assert dashboard["routes"]["strong_matches"]["params"] == {"score": "80", "inbox": "all", "fit": "all"}


def test_home_metric_opens_the_matching_filtered_workflow(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    job_id = _seed_home_job(app, "Inbox Route Engineer", 91)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.set_default_timeout(8000)
                page.goto(f"http://127.0.0.1:{port}/#home")
                page.locator('[data-home-metric="0"]').wait_for()
                page.locator('[data-home-metric="0"]').click()
                page.locator("#jobs.active").wait_for()
                page.locator(f'[data-job="{job_id}"]').wait_for()
                assert "inbox=all" in page.url and "fit=all" in page.url
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
