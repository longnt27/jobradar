import json
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import now
from job_radar.home_dashboard import build_home_dashboard
from job_radar.job_inbox import auto_shortlist_strong_matches, set_decision
from job_radar.settings import Settings
from job_radar.web import create_app


def _job(client: TestClient, title: str, score: int) -> str:
    job_id = client.post("/api/jobs/import", json={
        "company": "Example", "title": title, "description": "Build software in Hanoi.",
    }).json()["id"]
    client.app.state.db.execute(
        "UPDATE vacancies SET analysis_status='done',score=?,score_detail='{}' WHERE id=?",
        (score, job_id),
    )
    return job_id


def test_dashboard_totals_and_routes_use_the_same_workflows(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    high = _job(client, "High score", 90)
    edge = _job(client, "At threshold", 80)
    _job(client, "Below threshold", 79)
    app.state.db.execute(
        "UPDATE vacancies SET decision_state='shortlisted',score_detail=? WHERE id=?",
        (json.dumps({"hard_exclusions": ["Outside search area"]}), edge),
    )
    draft_id = "draft-for-home"
    app.state.db.execute(
        "INSERT INTO application_drafts(id,vacancy_id,provider,provider_mode,evidence_ids,resume_data,"
        "message_data,form_data,destination,warnings,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (draft_id, high, "template", "local", "[]", "{}", "{}", "{}", "{}", "[]", now(), now()),
    )
    app.state.db.execute("UPDATE vacancies SET manual_applied_at=? WHERE id=?", (now(), high))
    app.state.db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,created_at,updated_at) "
        "VALUES(?,?,?,?,?)",
        (high, "awaiting_review", draft_id, now(), now()),
    )

    home = build_home_dashboard(app.state.db, {"level": "good", "label": "Good"}, 80)
    assert home["counts"]["total_jobs"] == 3
    assert home["counts"]["strong_matches"] == 2
    assert home["counts"]["applications_drafted"] == 1
    assert home["counts"]["applications_sent"] == 1
    assert home["routes"]["strong_matches"]["params"] == {"score": "80", "inbox": "all", "fit": "all"}
    assert home["routes"]["applications_sent"]["params"] == {"application": "applied", "inbox": "all", "fit": "all"}
    assert client.get("/api/jobs/page?min_score=80&inbox=all&fit=all").json()["total"] == 2
    assert client.get("/api/jobs/page?application=applied&inbox=all&fit=all").json()["total"] == 1
    review = next(stage for stage in home["stages"] if stage["key"] == "review")
    assert review["route"]["params"]["review"] == "actionable"
    assert review["count"] == client.get("/api/applications/page?review=actionable").json()["total"] == 1
    prepare = next(stage for stage in home["stages"] if stage["key"] == "prepare")
    assert prepare["count"] == client.get(
        "/api/jobs/page?decision=shortlisted&application=not_started&inbox=all&fit=all"
    ).json()["total"] == 1


def test_auto_shortlist_respects_threshold_and_explicit_decisions(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    high = _job(client, "High", 92)
    edge = _job(client, "Edge", 80)
    low = _job(client, "Low", 79)
    declined = _job(client, "Declined", 95)
    set_decision(app.state.db, declined, "undecided")

    assert auto_shortlist_strong_matches(app.state.db, 80) == 2
    assert auto_shortlist_strong_matches(app.state.db, 80) == 0
    states = {row["id"]: row["decision_state"] for row in app.state.db.all("SELECT id,decision_state FROM vacancies")}
    assert states[high] == states[edge] == "shortlisted"
    assert states[low] == states[declined] == "undecided"
    assert app.state.db.one("SELECT COUNT(*) AS count FROM feedback WHERE vacancy_id=?", (high,))["count"] == 0
    app.state.db.execute("UPDATE vacancies SET score=70 WHERE id=?", (high,))
    auto_shortlist_strong_matches(app.state.db, 80)
    assert app.state.db.one("SELECT decision_state FROM vacancies WHERE id=?", (high,))["decision_state"] == "undecided"
    set_decision(app.state.db, edge, "shortlisted")
    app.state.db.execute("UPDATE vacancies SET score=70 WHERE id=?", (edge,))
    auto_shortlist_strong_matches(app.state.db, 80)
    assert app.state.db.one("SELECT decision_state FROM vacancies WHERE id=?", (edge,))["decision_state"] == "shortlisted"


def test_existing_strong_matches_are_shortlisted_when_service_starts(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    high = _job(client, "Existing strong match", 85)
    low = _job(client, "Existing low match", 70)

    with TestClient(app):
        assert app.state.db.one("SELECT decision_state FROM vacancies WHERE id=?", (high,))["decision_state"] == "shortlisted"
        assert app.state.db.one("SELECT decision_state FROM vacancies WHERE id=?", (low,))["decision_state"] == "undecided"


def test_home_only_shows_dashboard_loop_and_operational_panels() -> None:
    static = Path(__file__).parents[1] / "job_radar" / "static"
    html = (static / "index.html").read_text()
    js = (static / "app.js").read_text()
    for label in ("Total jobs found", "Applications drafted", "Applications sent", "Strong matches"):
        assert label in js
    for label in ("What deserves attention", "Capabilities and optional setup", "Open top priority"):
        assert label not in html + js
    assert html.index("home-activity-panel") < html.index("home-health-panel")
    assert "Update application status" in (Path(__file__).parents[1] / "job_radar" / "home_dashboard.py").read_text()
