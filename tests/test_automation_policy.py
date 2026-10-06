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
from job_radar.mail_config import save_smtp
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


def test_search_excluded_employer_is_never_automation_eligible(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("search_intent", {
        "excluded_employers": ["Blocked Corp"],
        "hard_constraints": {"employer": False},
        "strong_match_threshold": 80,
    })
    job_id = _scored_job(app, "Blocked Employer Engineer", company="Blocked Corp AI")
    policy = normalize_automation_policy({"enabled": True}, threshold=80)

    result = automation_eligibility(app.state.db, job_id, policy=policy)
    assert result["eligible"] is False
    assert any("excluded list" in reason for reason in result["reasons"])


def test_applied_and_recruiting_outcome_jobs_are_never_automation_eligible(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    base = normalize_automation_policy({"enabled": True}, threshold=80)

    applied = _scored_job(app, "Already Applied")
    app.state.db.execute(
        "UPDATE vacancies SET manual_applied_at=?,manual_applied_source='user_external' WHERE id=?",
        (now(), applied),
    )
    applied_result = automation_eligibility(app.state.db, applied, policy=base)
    assert applied_result["eligible"] is False
    assert any("applied elsewhere" in reason for reason in applied_result["reasons"])

    interviewing = _scored_job(app, "Already Interviewing")
    app.state.db.execute(
        "UPDATE vacancies SET recruiting_outcome='interview' WHERE id=?",
        (interviewing,),
    )
    outcome_result = automation_eligibility(app.state.db, interviewing, policy=base)
    assert outcome_result["eligible"] is False
    assert any("recruiting outcome" in reason for reason in outcome_result["reasons"])


def test_unverified_destination_can_be_explicitly_opted_into_for_draft_only(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    _ready_automation(app)

    with TestClient(app):
        app.state.auto_apply_manager.configure(
            True,
            80,
            {"require_verified_destination": False},
        )
        job_id = _scored_job(app, "Unverified But Draftable", apply_url=None)
        app.state.auto_apply_manager.wake()
        attempt = _wait_for_attempt(app, job_id, {"needs_review"})
        assert attempt["requested_by"] == "automation"
        assert attempt["draft_id"]
        draft = app.state.db.one(
            "SELECT destination FROM application_drafts WHERE id=?",
            (attempt["draft_id"],),
        )
        assert '"kind": "manual"' in draft["destination"]


def test_latest_feedback_reason_wins_when_updates_share_the_same_second(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    for index in range(3):
        job_id = _scored_job(app, f"Fast Ignore {index}", company="Rapid Corp")
        assert client.post(
            f"/api/jobs/{job_id}/decision",
            json={"decision": "ignored"},
        ).status_code == 200
        assert client.post(
            f"/api/jobs/{job_id}/decision",
            json={"decision": "ignored", "reason": "Company"},
        ).status_code == 200

    items = client.get("/api/preferences/suggestions").json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "exclude_employer"
    assert items[0]["value"] == "Rapid Corp"


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



def _ready_automation(app) -> None:
    app.state.db.set_setting("profile", {
        "name": "Alex Example",
        "email": "alex@example.org",
        "experience": [{
            "company": "Prior Co",
            "role": "Engineer",
            "dates": "2024-2026",
            "bullets": ["Built Python systems."],
        }],
        "drafting_provider": "template",
    })
    app.state.db.set_setting("matching_model", "test:small")
    save_smtp(app.state.settings, {
        "host": "smtp.example.org",
        "port": 587,
        "user": "",
        "password": "",
        "from": "alex@example.org",
    })


def _wait_for_attempt(app, job_id: str, statuses: set[str], timeout: float = 6.0) -> dict:
    import time
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = app.state.db.one(
            "SELECT * FROM auto_application_attempts WHERE vacancy_id=?",
            (job_id,),
        )
        if last and last["status"] in statuses:
            return last
        time.sleep(.05)
    raise AssertionError(f"Attempt did not reach {statuses}: {last}")


def test_daily_automatic_draft_cap_is_enforced_but_manual_prepare_bypasses_it(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    _ready_automation(app)

    with TestClient(app) as client:
        app.state.auto_apply_manager.configure(
            True,
            80,
            {"max_auto_drafts_per_day": 1},
        )
        first = _scored_job(app, "First Automatic", score=95)
        second = _scored_job(app, "Second Automatic", score=90)
        app.state.auto_apply_manager.wake()

        first_attempt = _wait_for_attempt(app, first, {"awaiting_review", "needs_review"})
        assert first_attempt["draft_id"]
        import time
        time.sleep(.25)
        assert app.state.db.one(
            "SELECT draft_id FROM auto_application_attempts WHERE vacancy_id=?",
            (second,),
        ) is None
        status = client.get("/api/auto-apply").json()
        assert status["daily_auto_drafts_used"] == 1
        assert status["daily_auto_drafts_remaining"] == 0

        manual = client.post(
            f"/api/jobs/{second}/prepare",
            json={"provider": "template"},
        )
        assert manual.status_code == 202, manual.text
        manual_attempt = _wait_for_attempt(app, second, {"awaiting_review", "needs_review"})
        assert manual_attempt["requested_by"] == "manual"
        assert manual_attempt["draft_id"]


def test_automatic_review_packet_cap_defers_only_automatic_packets(tmp_path: Path, monkeypatch) -> None:
    from job_radar.drafting import prepare_draft

    app = create_app(Settings(tmp_path))
    db = app.state.db
    _ready_automation(app)
    save_telegram(app.state.settings, {"token": "test-token", "chat_id": "123"})
    app.state.auto_apply_manager.configure(
        True,
        80,
        {"max_review_notifications_per_day": 1},
    )

    sent_job = _scored_job(app, "Already Notified")
    blocked_job = _scored_job(app, "Deferred Automatic")
    manual_job = _scored_job(app, "Manual Review")

    timestamp = now()
    db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,requested_by,created_at,updated_at) "
        "VALUES(?,'awaiting_review','automation',?,?)",
        (sent_job, timestamp, timestamp),
    )
    db.execute(
        "INSERT INTO notification_attempts(vacancy_id,channel,status,attempts,last_attempt_at,sent_at) "
        "VALUES(?,'telegram_application_review','sent',1,?,?)",
        (sent_job, timestamp, timestamp),
    )
    db.execute(
        "INSERT INTO notification_events(id,vacancy_id,channel,status,created_at) "
        "VALUES(? ,?,'telegram_application_review','sent',?)",
        (new_id(), sent_job, timestamp),
    )

    packets = []
    async def fake_packet(_settings, draft, _blockers):
        packets.append(draft["vacancy_id"])
        return 77
    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", fake_packet)

    for job_id, requested_by in ((blocked_job, "automation"), (manual_job, "manual")):
        draft = prepare_draft(db, app.state.settings, job_id, "template")
        db.execute(
            "INSERT INTO auto_application_attempts("
            "vacancy_id,status,draft_id,requested_by,created_at,updated_at"
            ") VALUES(?,'awaiting_review',?,?,?,?) "
            "ON CONFLICT(vacancy_id) DO UPDATE SET status='awaiting_review',draft_id=excluded.draft_id,"
            "requested_by=excluded.requested_by,updated_at=excluded.updated_at",
            (job_id, draft["id"], requested_by, timestamp, timestamp),
        )
        asyncio.run(app.state.auto_apply_manager.notify_review(draft["id"]))

    blocked = db.one(
        "SELECT telegram_status,telegram_error FROM auto_application_attempts WHERE vacancy_id=?",
        (blocked_job,),
    )
    manual = db.one(
        "SELECT telegram_status FROM auto_application_attempts WHERE vacancy_id=?",
        (manual_job,),
    )
    assert blocked["telegram_status"] == "deferred_limit"
    assert "limit" in blocked["telegram_error"].lower()
    assert manual["telegram_status"] == "sent"
    assert packets == [manual_job]


def test_application_review_mode_can_be_disabled_without_disabling_other_telegram_modes(
    tmp_path: Path, monkeypatch
) -> None:
    from job_radar.drafting import prepare_draft

    app = create_app(Settings(tmp_path))
    db = app.state.db
    _ready_automation(app)
    save_telegram(app.state.settings, {
        "token": "test-token",
        "chat_id": "123",
        "modes": {
            "application_reviews": False,
            "strong_job_alerts": True,
            "daily_digest": False,
        },
    })
    job_id = _scored_job(app, "Review Mode Off")
    draft = prepare_draft(db, app.state.settings, job_id, "template")
    timestamp = now()
    db.execute(
        "INSERT INTO auto_application_attempts("
        "vacancy_id,status,draft_id,requested_by,created_at,updated_at"
        ") VALUES(?,'awaiting_review',?,'manual',?,?)",
        (job_id, draft["id"], timestamp, timestamp),
    )
    packets = []
    monkeypatch.setattr(
        "job_radar.auto_apply.send_review_packet",
        lambda *_args: packets.append(True),
    )

    asyncio.run(app.state.auto_apply_manager.notify_review(draft["id"]))
    attempt = db.one(
        "SELECT status,review_hash,telegram_status FROM auto_application_attempts WHERE vacancy_id=?",
        (job_id,),
    )
    assert attempt["telegram_status"] == "disabled"
    assert attempt["status"] in {"awaiting_review", "needs_review"}
    assert attempt["review_hash"] == draft["package_hash"]
    assert packets == []


def test_automation_policy_ui_contract_exposes_guardrails_and_explicit_feedback() -> None:
    static = Path(__file__).parents[1] / "job_radar" / "static"
    html = (static / "index.html").read_text()
    js = (static / "app.js").read_text()

    for name in (
        "include_shortlisted",
        "max_job_age_days",
        "require_verified_destination",
        "require_preferred_location",
        "max_auto_drafts_per_day",
        "max_review_notifications_per_day",
    ):
        assert f'name="{name}"' in html
    for name in (
        "application_reviews",
        "strong_job_alerts",
        "daily_digest",
        "digest_time",
        "quiet_start",
        "quiet_end",
    ):
        assert f'name="{name}"' in html
    assert 'id="preference-suggestions"' in html
    assert "data-preference-action=\"apply\"" in js
    assert "data-preference-action=\"dismiss\"" in js



def test_partial_auto_apply_update_preserves_saved_policy(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("auto_apply", {
        "enabled": True,
        "include_shortlisted": True,
        "max_job_age_days": 9,
        "require_verified_destination": False,
        "require_preferred_location": True,
        "max_auto_drafts_per_day": 7,
        "max_review_notifications_per_day": 4,
    })
    client = TestClient(app)

    response = client.put("/api/auto-apply", json={"enabled": False})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["enabled"] is False
    assert saved["include_shortlisted"] is True
    assert saved["max_job_age_days"] == 9
    assert saved["require_verified_destination"] is False
    assert saved["require_preferred_location"] is True
    assert saved["max_auto_drafts_per_day"] == 7
    assert saved["max_review_notifications_per_day"] == 4


def test_strong_job_alerts_send_without_application_review_mode(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    manager = app.state.auto_apply_manager
    config = {
        "token": "token",
        "chat_id": "42",
        "modes": {
            "application_reviews": False,
            "strong_job_alerts": True,
            "daily_digest": False,
        },
        "digest_time": "18:00",
        "quiet_start": "",
        "quiet_end": "",
    }
    job_id = _scored_job(app, "Alert Engineer")
    monkeypatch.setattr(manager, "_strong_jobs_for_notifications", lambda limit=10: [{
        "id": job_id,
        "title": "Alert Engineer",
        "company": "Example",
        "location": "Hanoi",
        "score": 93,
    }])
    messages = []

    async def fake_post(_client, _token, method, *, json):
        messages.append((method, json))
        return {"message_id": 1}

    monkeypatch.setattr("job_radar.auto_apply._post", fake_post)
    asyncio.run(manager._send_strong_job_alerts(config))
    asyncio.run(manager._send_strong_job_alerts(config))

    assert len(messages) == 1
    assert messages[0][0] == "sendMessage"
    assert "Strong match" in messages[0][1]["text"]
    event = app.state.db.one(
        "SELECT status FROM notification_events WHERE vacancy_id=? AND channel='telegram_strong_job'",
        (job_id,),
    )
    assert event["status"] == "sent"


def test_daily_digest_is_independent_and_sends_once_per_local_day(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    manager = app.state.auto_apply_manager
    config = {
        "token": "token",
        "chat_id": "42",
        "modes": {
            "application_reviews": False,
            "strong_job_alerts": False,
            "daily_digest": True,
        },
        "digest_time": "00:00",
        "quiet_start": "",
        "quiet_end": "",
    }
    monkeypatch.setattr(manager, "_strong_jobs_for_notifications", lambda limit=5: [{
        "id": "job-1",
        "title": "Digest Engineer",
        "company": "Example",
        "location": "Hanoi",
        "score": 91,
    }])
    monkeypatch.setattr("job_radar.auto_apply.telegram_config", lambda _settings: config)
    messages = []

    async def fake_post(_client, _token, method, *, json):
        messages.append((method, json))
        return {"message_id": 1}

    monkeypatch.setattr("job_radar.auto_apply._post", fake_post)
    asyncio.run(manager._send_daily_digest(config))
    asyncio.run(manager._send_daily_digest(config))

    assert len(messages) == 1
    assert "daily digest" in messages[0][1]["text"].lower()
    assert "Digest Engineer" in messages[0][1]["text"]
