import asyncio
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.automation_policy import (
    automation_eligibility,
    daily_auto_drafts_used,
    normalize_automation_policy,
)
from job_radar.db import new_id, now
from job_radar.feedback_learning import feedback_suggestions
from job_radar.ingest import ObservedJob, ingest
from job_radar.notifications import save_telegram, telegram_config, telegram_quiet_now
from job_radar.settings import Settings
from job_radar.web import create_app


def _scored_job(app, title: str, *, company: str = "Example", location: str = "Hanoi",
                apply_url: str | None = "mailto:jobs@example.org", score: int = 90) -> str:
    source = app.state.db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")["id"]
    identifier, _ = ingest(
        app.state.db,
        source,
        ObservedJob(
            f"https://example.org/jobs/{title.lower().replace(' ', '-')}",
            title,
            company,
            "Build reliable Python and machine learning systems.",
            location=location,
            apply_url=apply_url,
        ),
    )
    app.state.db.execute(
        "UPDATE vacancies SET score=?,analysis_status='done',analysis_model='test:small' WHERE id=?",
        (score, identifier),
    )
    return identifier


def test_shortlist_is_bookmark_unless_automation_explicitly_includes_it(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    job_id = _scored_job(app, "Shortlisted Engineer")
    app.state.db.execute(
        "UPDATE vacancies SET decision_state='shortlisted',state='interesting' WHERE id=?",
        (job_id,),
    )
    base = normalize_automation_policy({"enabled": True}, threshold=80)

    blocked = automation_eligibility(app.state.db, job_id, policy=base)
    assert blocked["eligible"] is False
    assert any("bookmarks" in reason for reason in blocked["reasons"])

    allowed = automation_eligibility(
        app.state.db,
        job_id,
        policy={**base, "include_shortlisted": True},
    )
    assert allowed["eligible"] is True


def test_automation_policy_combines_freshness_destination_and_location(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("search_intent", {
        "preferred_locations": ["Hanoi"],
        "strong_match_threshold": 80,
    })
    wrong_location = _scored_job(app, "Remote Engineer", location="Da Nang")
    old_job = _scored_job(app, "Old Engineer")
    missing_destination = _scored_job(app, "No Destination Engineer", apply_url=None)
    db.execute(
        "UPDATE vacancies SET first_seen_at='2026-09-01T00:00:00+00:00' WHERE id=?",
        (old_job,),
    )
    policy = normalize_automation_policy({
        "enabled": True,
        "require_preferred_location": True,
        "require_verified_destination": True,
        "max_job_age_days": 3,
    }, threshold=80)

    location = automation_eligibility(db, wrong_location, policy=policy)
    assert any("preferred locations" in reason for reason in location["reasons"])
    old = automation_eligibility(db, old_job, policy=policy)
    assert any("older than" in reason for reason in old["reasons"])
    destination = automation_eligibility(db, missing_destination, policy=policy)
    assert any("verified application destination" in reason for reason in destination["reasons"])


def test_daily_draft_limit_counts_only_automatic_work(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    automatic = _scored_job(app, "Automatic Engineer")
    manual = _scored_job(app, "Manual Engineer")
    for job_id, requested_by in ((automatic, "automation"), (manual, "manual")):
        draft_id = new_id()
        timestamp = now()
        db.execute(
            "INSERT INTO application_drafts(id,vacancy_id,provider,provider_mode,evidence_ids,resume_data,"
            "message_data,form_data,destination,warnings,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                draft_id, job_id, "template", "local template; no model inference", "[]", "{}",
                "{}", "{}", '{"kind":"email","email":"jobs@example.org"}', "[]", timestamp, timestamp,
            ),
        )
        db.execute(
            "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,requested_by,created_at,updated_at) "
            "VALUES(?,'awaiting_review',?,?,?,?)",
            (job_id, draft_id, requested_by, timestamp, timestamp),
        )
    assert daily_auto_drafts_used(db) == 1


def test_telegram_modes_and_quiet_hours_are_independent(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {
        "token": "secret",
        "chat_id": "42",
        "modes": {
            "application_reviews": False,
            "strong_job_alerts": True,
            "daily_digest": True,
        },
        "digest_time": "17:30",
        "quiet_start": "22:00",
        "quiet_end": "07:00",
    })
    config = telegram_config(settings)
    assert config["modes"] == {
        "application_reviews": False,
        "strong_job_alerts": True,
        "daily_digest": True,
    }
    assert config["digest_time"] == "17:30"
    assert telegram_quiet_now(config, datetime.fromisoformat("2026-10-06T23:00:00+07:00")) is True
    assert telegram_quiet_now(config, datetime.fromisoformat("2026-10-06T12:00:00+07:00")) is False


def test_setup_api_persists_notification_modes_without_exposing_token(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    response = client.post("/api/setup/telegram", json={
        "token": "1234567890:secret",
        "chat_id": "42",
        "application_reviews": False,
        "strong_job_alerts": True,
        "daily_digest": True,
        "digest_time": "19:15",
        "quiet_start": "23:00",
        "quiet_end": "06:00",
    })
    assert response.status_code == 200, response.text
    setup = client.get("/api/setup").json()
    assert setup["telegram_notifications"]["modes"] == {
        "application_reviews": False,
        "strong_job_alerts": True,
        "daily_digest": True,
    }
    assert setup["telegram_notifications"]["digest_time"] == "19:15"
    assert "secret" not in response.text
    assert "secret" not in str(setup)


def test_feedback_patterns_suggest_but_never_silently_change_preferences(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    ids = [
        _scored_job(app, f"Engineer {index}", company="Noise Corp")
        for index in range(3)
    ]
    for job_id in ids:
        response = client.post(
            f"/api/jobs/{job_id}/decision",
            json={"decision": "ignored", "reason": "Company"},
        )
        assert response.status_code == 200

    before = client.get("/api/search-intent").json()
    result = client.get("/api/preferences/suggestions").json()
    assert len(result["items"]) == 1
    suggestion = result["items"][0]
    assert suggestion["kind"] == "exclude_employer"
    assert suggestion["value"] == "Noise Corp"
    assert client.get("/api/search-intent").json() == before

    applied = client.post(f"/api/preferences/suggestions/{suggestion['id']}/apply")
    assert applied.status_code == 200
    assert "Noise Corp" in applied.json()["preferences"]["excluded_employers"]
    assert client.get("/api/preferences/suggestions").json()["items"] == []


def test_dismissing_feedback_suggestion_does_not_change_preferences(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    for index in range(3):
        job_id = _scored_job(app, f"Research {index}", company="Maybe Corp")
        client.post(
            f"/api/jobs/{job_id}/decision",
            json={"decision": "ignored", "reason": "Company"},
        )
    before = client.get("/api/search-intent").json()
    suggestion = client.get("/api/preferences/suggestions").json()["items"][0]
    dismissed = client.post(f"/api/preferences/suggestions/{suggestion['id']}/dismiss")
    assert dismissed.status_code == 200
    assert client.get("/api/search-intent").json() == before
    assert client.get("/api/preferences/suggestions").json()["items"] == []
