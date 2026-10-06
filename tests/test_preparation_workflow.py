import time
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.drafting import prepare_draft as real_prepare_draft
from job_radar.ingest import ObservedJob, ingest
from job_radar.mail_config import save_smtp
from job_radar.settings import Settings
from job_radar.web import create_app


def _ready_profile(app) -> None:
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


def _wait_attempt(app, job_id: str, statuses: set[str], timeout: float = 6.0) -> dict:
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
    raise AssertionError(f"Preparation did not reach {statuses}: {last}")


def test_manual_prepare_preflights_then_returns_before_background_drafting_finishes(
    tmp_path: Path, monkeypatch
) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    _ready_profile(app)
    save_smtp(app.state.settings, {
        "host": "smtp.example.org",
        "port": 587,
        "user": "",
        "password": "",
        "from": "alex@example.org",
    })

    drafting_started = []

    def slow_prepare(*args, **kwargs):
        drafting_started.append(True)
        time.sleep(.8)
        return real_prepare_draft(*args, **kwargs)

    monkeypatch.setattr("job_radar.auto_apply.prepare_draft", slow_prepare)

    with TestClient(app) as client:
        assert client.get("/api/auto-apply").json()["enabled"] is False
        source_id = app.state.db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")["id"]
        job_id, _ = ingest(
            app.state.db,
            source_id,
            ObservedJob(
                "https://example.org/jobs/platform-engineer",
                "Platform Engineer",
                "Example",
                "Build reliable Python systems.",
                apply_url="mailto:jobs@example.org",
            ),
        )
        job = {"id": job_id}

        preflight = client.get(f"/api/jobs/{job['id']}/prepare/preflight")
        assert preflight.status_code == 200
        assert preflight.json()["state"] == "ready"
        assert preflight.json()["action"]["action_type"] == "email"
        assert preflight.json()["processing"]["remote"] is False

        started = time.monotonic()
        queued = client.post(
            f"/api/jobs/{job['id']}/prepare",
            json={"provider": "template"},
        )
        elapsed = time.monotonic() - started

        assert queued.status_code == 202, queued.text
        assert queued.json()["status"] in {"queued", "preparing"}
        assert queued.json()["processing"]["remote"] is False
        assert elapsed < .5
        attempt = app.state.db.one(
            "SELECT requested_by,requested_provider,status FROM auto_application_attempts WHERE vacancy_id=?",
            (job["id"],),
        )
        assert attempt["requested_by"] == "manual"
        assert attempt["requested_provider"] == "template"
        assert attempt["status"] in {"queued", "preparing"}
        progress = client.get(f"/api/jobs/{job['id']}").json()
        assert progress["application_progress"] == "preparing"
        assert progress["preparation_requested_by"] == "manual"

        finished = _wait_attempt(app, job["id"], {"awaiting_review", "needs_review"})
        assert drafting_started
        assert finished["draft_id"]
        draft = client.get(f"/api/applications/{finished['draft_id']}")
        assert draft.status_code == 200
        assert draft.json()["destination"]["email"] == "jobs@example.org"


def test_ambiguous_destination_requires_prepare_anyway_before_any_drafting(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    _ready_profile(app)

    with TestClient(app) as client:
        job = client.post("/api/jobs/import", json={
            "company": "Example",
            "title": "Research Engineer",
            "description": "Research and deploy machine learning systems.",
        }).json()

        preflight = client.get(f"/api/jobs/{job['id']}/prepare/preflight").json()
        assert preflight["state"] == "confirmation_required"
        assert preflight["requires_confirmation"] is True
        assert preflight["action"]["kind"] == "manual"

        blocked = client.post(
            f"/api/jobs/{job['id']}/prepare",
            json={"provider": "template"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "prepare_confirmation_required"
        assert blocked.json()["detail"]["processing"]["remote"] is False
        assert not app.state.db.one(
            "SELECT vacancy_id FROM auto_application_attempts WHERE vacancy_id=?",
            (job["id"],),
        )
        assert not app.state.db.one(
            "SELECT id FROM application_drafts WHERE vacancy_id=?",
            (job["id"],),
        )

        queued = client.post(
            f"/api/jobs/{job['id']}/prepare",
            json={"provider": "template", "prepare_anyway": True},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["status"] in {"queued", "preparing"}
        attempt = _wait_attempt(app, job["id"], {"needs_review"})
        assert attempt["requested_by"] == "manual"
        assert attempt["prepare_anyway"] == 1
        assert attempt["draft_id"]
        draft = client.get(f"/api/applications/{attempt['draft_id']}").json()
        assert draft["destination"]["kind"] == "manual"
        assert any("destination" in warning.lower() for warning in draft["warnings"])


def test_automatic_policy_rejects_unknown_destination_before_preparation(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    _ready_profile(app)
    app.state.db.set_setting("matching_model", "test:small")

    with TestClient(app) as client:
        app.state.auto_apply_manager.configure(True, 80)
        job = client.post("/api/jobs/import", json={
            "company": "Example",
            "title": "Unknown Destination Engineer",
            "description": "Build reliable Python systems.",
        }).json()
        app.state.db.execute(
            "UPDATE vacancies SET score=90,analysis_status='done',analysis_model='test:small' WHERE id=?",
            (job["id"],),
        )
        app.state.auto_apply_manager.wake()
        time.sleep(.2)
        assert app.state.db.one(
            "SELECT vacancy_id FROM auto_application_attempts WHERE vacancy_id=?",
            (job["id"],),
        ) is None
        assert not app.state.db.one(
            "SELECT id FROM application_drafts WHERE vacancy_id=?",
            (job["id"],),
        )
