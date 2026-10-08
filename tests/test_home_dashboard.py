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


STATIC = Path(__file__).parents[1] / "job_radar" / "static"


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


def test_home_ui_separates_required_optional_and_operational_work() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    css = (STATIC / "app.css").read_text()

    assert "Capabilities and optional setup" in html
    assert "Optional capabilities" in js
    assert "What deserves attention" in html
    assert "Daily loop" in html
    assert "View activity" in html

    assert "Strong matches" in js
    assert "Unseen jobs" in js
    assert "Applications to review" in js
    assert "Applied to track" in js

    assert "Job discovery" in js
    assert "Application preparation" in js
    assert "Discovery is useful on its own; application features stay optional." in js
    assert "Optional · fallback ranking still works" in js
    assert "Optional · not configured" in js

    assert "data-home-queue-kind" in js
    assert "await showApplication(button.dataset.homeQueueDraft)" in js
    assert "await openJobInJobs(id)" in js

    assert ".hero.is-compact" in css
    assert ".home-optional-row" in css
    assert ".home-action-row" in css



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


def _review_draft(app, job_id: str, status: str = "awaiting_review") -> str:
    draft_id = new_id()
    timestamp = now()
    app.state.db.execute(
        "INSERT INTO application_drafts(id,vacancy_id,provider,provider_mode,evidence_ids,resume_data,"
        "message_data,form_data,destination,warnings,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            draft_id, job_id, "template", "local template; no model inference", "[]", "{}",
            "{}", "{}", '{"kind":"manual","action_type":"manual"}', "[]", timestamp, timestamp,
        ),
    )
    app.state.db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,created_at,updated_at) "
        "VALUES(?,?,?,?,?)",
        (job_id, status, draft_id, timestamp, timestamp),
    )
    return draft_id


def test_home_priority_queue_ranks_user_work_above_technical_health(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    review_job = _seed_home_job(app, "Review Me", 72, seen=True)
    draft_id = _review_draft(app, review_job, "needs_review")
    strong_job = _seed_home_job(app, "Strong New Job", 94)
    failed_job = _seed_home_job(app, "Broken Analysis", 20, seen=True)
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='failed',analysis_error='model failed' WHERE id=?",
        (failed_job,),
    )

    dashboard = build_home_dashboard(
        app.state.db,
        {"level": "degraded", "label": "1 source threatens discovery"},
        80,
    )

    assert dashboard["priority_items"][0]["kind"] == "application_review"
    assert dashboard["priority_items"][0]["id"] == draft_id
    assert dashboard["priority_items"][1]["kind"] == "strong_unseen_job"
    assert dashboard["priority_items"][1]["id"] == strong_job
    assert all(item["kind"] != "analysis_failure" for item in dashboard["priority_items"])
    assert dashboard["health"]["degraded"] is True
    assert dashboard["state"] == "action_needed"


def test_home_does_not_claim_clear_when_discovery_or_analysis_is_incomplete(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    degraded = build_home_dashboard(
        app.state.db,
        {"level": "limited", "label": "Coverage uncertain"},
        80,
    )
    assert degraded["state"] == "radar_degraded"
    assert "caught up" not in degraded["headline"].lower()
    assert degraded["health"]["degraded"] is True

    pending = _seed_home_job(app, "Still Reviewing", 0, seen=True)
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='pending',score=NULL,score_detail=NULL WHERE id=?",
        (pending,),
    )
    processing = build_home_dashboard(
        app.state.db,
        {"level": "good", "label": "Discovery coverage looks normal"},
        80,
    )
    assert processing["state"] == "radar_degraded"
    assert "still being reviewed" in processing["health"]["message"].lower()


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
    assert dashboard["counts"]["strong_matches"] == 1
    assert dashboard["counts"]["ready_to_prepare"] == 1
    stages = {stage["key"]: stage for stage in dashboard["stages"]}
    assert stages["triage"]["route"] == {"tab": "jobs", "params": {"inbox": "unseen"}}
    assert stages["prepare"]["count"] == 1
    assert stages["prepare"]["route"]["params"]["decision"] == "shortlisted"
    assert stages["review"]["route"]["tab"] == "applications"
    assert stages["track"]["route"]["params"]["application"] == "applied"
    assert dashboard["routes"]["strong_matches"]["params"]["score"] == "80"


def test_home_static_contract_uses_backend_priorities_and_routed_counts() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    css = (STATIC / "app.css").read_text()

    assert 'id="home-loop"' in html
    assert 'id="home-health-status"' in html
    assert 'id="home-show-all-priorities"' in html
    assert "data-home-priority" in js
    assert "data-home-metric" in js
    assert "openHomeRoute" in js
    assert "data.attention?.drafts" not in js
    assert "].slice(0, 8)" not in js
    assert "home.state === 'radar_degraded'" in js
    assert ".home-loop-step" in css
    assert ".home-empty-warning" in css



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
                assert "inbox=unseen" in page.url
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
