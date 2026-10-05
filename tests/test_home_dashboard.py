from pathlib import Path

from fastapi.testclient import TestClient

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
    app.state.db.execute(
        "UPDATE vacancies SET analysis_status='done',score=91,state='new' WHERE id=?",
        (job["id"],),
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

    assert "Setup checklist" in html
    assert "Optional integrations" in js
    assert "Needs your attention" in html
    assert "View activity" in html

    assert "Strong matches" in js
    assert "New in 24h" in js
    assert "Drafts to review" in js
    assert "Analysis failures" in js

    assert "Add personal details" in js
    assert "Add work history" in js
    assert "Select projects" in js
    assert "Review live jobs" not in js
    assert "Optional · not configured" in js

    assert "data-home-queue-kind" in js
    assert "await showApplication(button.dataset.homeQueueDraft)" in js
    assert "await showJob(id)" in js

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
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=85,state='new' WHERE id=?", (job["id"],))
    status = client.get("/api/status").json()
    assert status["strong_match_threshold"] == 90
    assert status["counts"]["high_fit_new"] == 0
    app.state.db.execute("UPDATE vacancies SET score=92 WHERE id=?", (job["id"],))
    status = client.get("/api/status").json()
    assert status["counts"]["high_fit_new"] == 1
