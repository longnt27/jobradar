import asyncio
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from job_radar.auto_apply import AutoApplyManager, _safe_attachments
from job_radar.drafting import ModelDraft, prepare_draft
from job_radar.ingest import ObservedJob, ingest
from job_radar.mail_config import save_smtp
from job_radar.notifications import save_telegram
from job_radar.settings import Settings
from job_radar.web import create_app


def _scored_job(app, title: str, score: int, apply_url: str | None = "mailto:jobs@example.org") -> str:
    source_id = app.state.db.one("SELECT id FROM sources LIMIT 1")["id"]
    identifier, _ = ingest(app.state.db, source_id, ObservedJob(
        f"https://example.org/jobs/{title.replace(' ', '-')}", title, "Example", "Build Python systems.", apply_url=apply_url))
    app.state.db.execute("UPDATE vacancies SET score=?,analysis_status='done',analysis_model='test:small' WHERE id=?", (score, identifier))
    app.state.auto_apply_manager.wake()
    return identifier


def _wait_for_status(app, identifier: str, expected: str) -> dict:
    for _ in range(150):
        row = app.state.db.one("SELECT * FROM auto_application_attempts WHERE vacancy_id=?", (identifier,))
        if row and row["status"] == expected:
            return row
        time.sleep(.05)
    raise AssertionError(f"Automatic application did not reach {expected}: {row}")


def _wait_for_telegram_status(app, identifier: str, expected: str) -> dict:
    for _ in range(150):
        row = app.state.db.one("SELECT * FROM auto_application_attempts WHERE vacancy_id=?", (identifier,))
        if row and row["telegram_status"] == expected:
            return row
        time.sleep(.05)
    raise AssertionError(f"Telegram review did not reach {expected}: {row}")


def test_failed_preparation_without_draft_sends_one_notice(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    job_id = _scored_job(app, "Unprepared Engineer", 88)
    save_telegram(app.state.settings, {"token": "test-token", "chat_id": "123"})
    app.state.db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,telegram_status,detail,requested_by,created_at,updated_at) "
        "VALUES(?,'needs_review','pending','Drafting failed','manual','2026-10-06','2026-10-06')",
        (job_id,),
    )
    notices = []

    async def send_notice(_settings, title, company):
        notices.append((title, company))
        return 42

    monkeypatch.setattr("job_radar.auto_apply.send_preparation_notice", send_notice)
    asyncio.run(app.state.auto_apply_manager.notify_preparation_issue(job_id))
    asyncio.run(app.state.auto_apply_manager.notify_preparation_issue(job_id))
    attempt = app.state.db.one("SELECT telegram_status,telegram_message_id FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))
    assert attempt == {"telegram_status": "sent", "telegram_message_id": 42}
    assert notices == [("Unprepared Engineer", "Example")]


def test_retry_all_ai_preparations_excludes_forms_needing_review(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    quota_job = _scored_job(app, "Quota Engineer", 88)
    form_job = _scored_job(app, "Form Engineer", 87)
    for job_id, detail in ((quota_job, "AI drafting failed: codex quota reached"),
                           (form_job, "Inspect every LinkedIn application step before sending")):
        app.state.db.execute(
            "INSERT INTO auto_application_attempts(vacancy_id,status,requested_by,detail,created_at,updated_at) "
            "VALUES(?,'needs_review','automation',?,'2026-10-07','2026-10-07')",
            (job_id, detail),
        )
    manager = app.state.auto_apply_manager
    assert [item["vacancy_id"] for item in manager.failed_preparations()] == [quota_job]
    assert manager.retry_failed_preparations() == 1
    assert app.state.db.one("SELECT status,requested_by FROM auto_application_attempts WHERE vacancy_id=?", (quota_job,)) == {
        "status": "queued", "requested_by": "manual"}
    assert app.state.db.one("SELECT status FROM auto_application_attempts WHERE vacancy_id=?", (form_job,))["status"] == "needs_review"
    with TestClient(app) as client:
        failures = client.get("/api/ai/failures")
    assert failures.status_code == 200
    assert failures.json()["preparations"] == 0


def test_retry_all_failed_ai_work_retries_saved_resume_updates(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
                                         "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024-2026",
                                                         "bullets": ["Built Python systems."]}]})
    job_id = _scored_job(app, "Resume Retry Engineer", 88)
    draft = prepare_draft(app.state.db, app.state.settings, job_id, "template")
    app.state.db.execute("UPDATE application_drafts SET project_refresh_error='provider quota reached' WHERE id=?",
                         (draft["id"],))
    retried = []

    def retry(db, _settings, draft_id):
        retried.append(draft_id)
        db.execute("UPDATE application_drafts SET project_refresh_error=NULL WHERE id=?", (draft_id,))

    monkeypatch.setattr("job_radar.web.refresh_draft_projects", retry)
    with TestClient(app) as client:
        response = client.post("/api/ai/retry-failed")
        assert response.status_code == 202
        assert response.json()["queued"] == 1
        for _ in range(50):
            if client.get("/api/ai/failures").json()["draft_projects"] == 0:
                break
            time.sleep(.05)
        assert client.get("/api/ai/failures").json()["draft_projects"] == 0
    assert retried == [draft["id"]]


def test_automation_status_counts_existing_jobs_without_per_job_database_reads(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    for index in range(40):
        _scored_job(app, f"Existing Engineer {index}", 90)

    db = app.state.db
    reads = 0
    original_one, original_all = db.one, db.all

    def one(*args, **kwargs):
        nonlocal reads
        reads += 1
        return original_one(*args, **kwargs)

    def all(*args, **kwargs):
        nonlocal reads
        reads += 1
        return original_all(*args, **kwargs)

    monkeypatch.setattr(db, "one", one)
    monkeypatch.setattr(db, "all", all)

    status = app.state.auto_apply_manager.status()
    assert status["eligible_existing"] == 40
    assert status["highest_existing_score"] == 90
    assert reads < 30


def test_automation_summary_loads_without_candidate_preview(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    _scored_job(app, "Existing Engineer", 90)
    def unexpected_preview(*_args, **_kwargs):
        raise AssertionError("candidate preview should be deferred")
    monkeypatch.setattr("job_radar.auto_apply.automation_eligibility", unexpected_preview)
    with TestClient(app) as client:
        summary = client.get("/api/auto-apply", params={"summary_only": True})
    assert summary.status_code == 200
    assert summary.json()["eligible_existing"] is None
    assert "recent" in summary.json()


def test_auto_apply_prepares_new_jobs_but_waits_for_approval(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
        "drafting_provider": "template"})
    save_smtp(app.state.settings, {"host": "smtp.example.org", "port": 587, "user": "", "password": "", "from": "alex@example.org"})
    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda draft, _settings: sent.append(draft["vacancy_id"]) or "accepted")
    old_id = _scored_job(app, "Old Engineer", 99)
    with TestClient(app) as client:
        app.state.auto_apply_manager.configure(True, 80)
        assert _wait_for_status(app, old_id, "skipped")
        threshold_id = _scored_job(app, "Below Threshold Engineer", 79)
        new_id = _scored_job(app, "Threshold Engineer", 80)
        draft_id = _wait_for_status(app, new_id, "awaiting_review")["draft_id"]
        assert not sent
        prepared_job = client.get(f"/api/jobs/{new_id}").json()
        assert prepared_job["decision_state"] == "undecided"
        assert prepared_job["application_progress"] == "draft_ready"
        draft = client.get(f"/api/applications/{draft_id}").json()
        response = client.post(f"/api/applications/{draft_id}/approve", json={"package_hash": draft["package_hash"]})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "sent_confirmed"
        assert sent == [new_id]
        applied_job = client.get(f"/api/jobs/{new_id}").json()
        assert applied_job["decision_state"] == "undecided"
        assert applied_job["application_progress"] == "applied"
        assert app.state.db.one("SELECT vacancy_id FROM auto_application_attempts WHERE vacancy_id=?", (threshold_id,)) is None
        app.state.auto_apply_manager.wake()
        time.sleep(.1)
        assert sent == [new_id]


def test_existing_scored_jobs_can_be_queued_for_draft_review(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
        "drafting_provider": "template"})
    save_smtp(app.state.settings, {"host": "smtp.example.org", "port": 587, "user": "", "password": "", "from": "alex@example.org"})
    old_id = _scored_job(app, "Existing Engineer", 92)
    _scored_job(app, "Below Threshold Engineer", 79)
    with TestClient(app) as client:
        assert client.post("/api/auto-apply/queue-existing").status_code == 409
        app.state.auto_apply_manager.configure(True, 80)
        assert _wait_for_status(app, old_id, "skipped")
        assert client.get("/api/auto-apply").json()["eligible_existing"] == 1
        assert client.get("/api/auto-apply").json()["highest_existing_score"] == 92
        queued = client.post("/api/auto-apply/queue-existing")
        assert queued.status_code == 200, queued.text
        assert queued.json() == {"queued": 1}
        assert client.post("/api/auto-apply/queue-existing").json() == {"queued": 0}
        attempt = _wait_for_status(app, old_id, "awaiting_review")
        assert attempt["draft_id"]
        status = client.get("/api/auto-apply").json()
        assert status["eligible_existing"] == 0
        assert status["highest_existing_score"] == 79
        assert not app.state.db.one("SELECT id FROM submissions WHERE vacancy_id=?", (old_id,))


def test_existing_job_waits_for_score_before_telegram_review(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026",
                        "bullets": ["Built Python systems."]}], "drafting_provider": "template"})
    save_smtp(app.state.settings, {"host": "smtp.example.org", "port": 587, "user": "",
                                   "password": "", "from": "alex@example.org"})
    save_telegram(app.state.settings, {"token": "test-token", "chat_id": "123"})
    packets = []

    async def fake_packet(_settings, draft, _blockers):
        packets.append(draft)
        return 42

    async def quiet_telegram_loop(_self):
        await asyncio.Event().wait()

    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", fake_packet)
    monkeypatch.setattr(AutoApplyManager, "_telegram_loop", quiet_telegram_loop)
    job_id = _scored_job(app, "Waiting Engineer", 68)
    db.execute("UPDATE vacancies SET analysis_status='pending' WHERE id=?", (job_id,))
    with TestClient(app) as client:
        app.state.auto_apply_manager.configure(True, 80)
        assert _wait_for_status(app, job_id, "skipped")
        assert client.post("/api/auto-apply/queue-existing").json() == {"queued": 1}
        waiting = db.one("SELECT status,detail FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))
        assert waiting["status"] == "queued"
        assert "Waiting for local match review" in waiting["detail"]
        assert client.get("/api/auto-apply").json()["recent"][0]["analysis_status"] == "pending"
        assert packets == []
        db.execute("UPDATE vacancies SET score=80,analysis_status='done' WHERE id=?", (job_id,))
        app.state.auto_apply_manager.wake()
        _wait_for_status(app, job_id, "awaiting_review")
        attempt = _wait_for_telegram_status(app, job_id, "sent")
        assert len(packets) == 1
        assert packets[0]["id"] == attempt["draft_id"]
        assert packets[0]["job_score"] == 80
        assert Path(packets[0]["resume_path"]).is_file()


def test_auto_apply_requires_verified_destination_before_provider_work(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
        "drafting_provider": "template"})
    with TestClient(app):
        app.state.auto_apply_manager.configure(True, 80)
        identifier = _scored_job(app, "Unlinked Engineer", 90, None)
        time.sleep(.2)
        assert app.state.db.one(
            "SELECT vacancy_id FROM auto_application_attempts WHERE vacancy_id=?", (identifier,)
        ) is None
        assert not app.state.db.one("SELECT id FROM application_drafts WHERE vacancy_id=?", (identifier,))
        assert not app.state.db.one("SELECT id FROM submissions WHERE vacancy_id=?", (identifier,))


def test_auto_apply_setting_requires_local_matching_and_profile(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    with TestClient(app) as client:
        assert client.get("/api/auto-apply").json()["enabled"] is False
        response = client.put("/api/auto-apply", json={"enabled": True, "threshold": 85})
        assert response.status_code == 409
        assert "local matching model" in response.json()["detail"]
        assert client.put("/api/auto-apply", json={"enabled": False, "threshold": 101}).status_code == 422


def test_auto_apply_attaches_resume_only_to_unambiguous_resume_field() -> None:
    fields = [{"index": 0, "type": "file", "name": "resume", "label": "Resume", "required": True},
              {"index": 1, "type": "file", "name": "portfolio", "label": "Portfolio", "required": False}]
    assert _safe_attachments(fields) == {"0": {"kind": "resume"}, "1": {"kind": "none"}}
    fields.append({"index": 2, "type": "file", "name": "cv_other", "label": "Additional CV", "required": True})
    assert "0" not in _safe_attachments(fields)
    assert "2" not in _safe_attachments(fields)


def test_regeneration_uses_custom_prompt_and_invalidates_old_review(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python"]}],
        "drafting_provider": "codex"})
    from job_radar.db import new_id, now
    job_id = new_id()
    db.execute("INSERT INTO vacancies(id,company,title,description,apply_url,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (job_id, "Example", "AI Engineer", "Build Python systems.", "mailto:jobs@example.org", now(), now(), now(), now()))
    seen = []
    def fake_provider(_provider, _job, _profile, _cards, custom_prompt=""):
        seen.append(custom_prompt)
        return ModelDraft(summary="Python engineer", email_subject="AI Engineer application",
                          email_body=f"Dear team. {custom_prompt or 'Initial version'}")
    monkeypatch.setattr("job_radar.drafting._run_provider", fake_provider)
    original = prepare_draft(db, app.state.settings, job_id, "codex")
    db.execute("INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,review_hash,created_at,updated_at) VALUES(?,'awaiting_review',?,?,?,?)",
               (job_id, original["id"], original["package_hash"], now(), now()))
    with TestClient(app) as client:
        result = client.post(f"/api/applications/{original['id']}/regenerate",
                             json={"prompt": "Emphasize production search work"})
        assert result.status_code == 200, result.text
        revised = result.json()
        assert revised["id"] == original["id"]
        assert revised["package_hash"] != original["package_hash"]
        assert "Emphasize production search work" in revised["message_data"]["body"]
        assert seen[-1] == "Emphasize production search work"
        review = db.one("SELECT review_hash,telegram_status FROM auto_application_attempts WHERE draft_id=?", (original["id"],))
        assert review == {"review_hash": revised["package_hash"], "telegram_status": "web_only"}
        stale = client.post(f"/api/applications/{original['id']}/approve", json={"package_hash": original["package_hash"]})
        assert stale.status_code == 422
        db.execute("UPDATE auto_application_attempts SET status='regenerating' WHERE draft_id=?", (original["id"],))
        asyncio.run(app.state.auto_apply_manager.notify_review(original["id"]))
        assert db.one("SELECT status FROM auto_application_attempts WHERE draft_id=?", (original["id"],))["status"] == "regenerating"
        assert client.patch(f"/api/applications/{original['id']}", json={"message_data": {"body": "Race"}}).status_code == 409
        assert client.post(f"/api/applications/{original['id']}/regenerate", json={"prompt": "Another version"}).status_code == 422


def test_auto_apply_submits_complete_web_form(tmp_path: Path) -> None:
    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ("<html><body><form method='post' action='/apply' enctype='multipart/form-data'>"
                    "<label>Name <input name='name' required></label>"
                    "<label>Email <input type='email' name='email' required></label>"
                    "<label>Cover letter <textarea name='cover_letter' required></textarea></label>"
                    "<label>Resume <input type='file' name='resume' accept='application/pdf' required></label>"
                    "<button type='submit'>Apply</button></form></body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posted.append(self.rfile.read(int(self.headers["Content-Length"])))
            body = b"<html><body>Thank you. Application received.</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        app = create_app(Settings(tmp_path))
        app.state.db.execute("UPDATE sources SET enabled=0")
        app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
            "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
            "drafting_provider": "template"})
        with TestClient(app) as client:
            app.state.auto_apply_manager.configure(True, 85)
            identifier = _scored_job(app, "Web Engineer", 90, f"http://127.0.0.1:{server.server_port}/apply")
            result = _wait_for_status(app, identifier, "awaiting_review")
            assert result["draft_id"]
            assert not posted
            draft = client.get(f"/api/applications/{result['draft_id']}").json()
            outcome = client.post(f"/api/applications/{result['draft_id']}/approve", json={"package_hash": draft["package_hash"]})
            assert outcome.status_code == 200, outcome.text
            assert outcome.json()["status"] == "submitted_confirmed"
            assert posted
            assert b'Content-Type: application/pdf' in posted[0]
            assert b'%PDF-' in posted[0]
            assert app.state.db.one("SELECT status FROM submissions WHERE vacancy_id=?", (identifier,))["status"] == "submitted_confirmed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
