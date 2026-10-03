import asyncio
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from job_radar.auto_apply import _safe_attachments
from job_radar.drafting import ModelDraft, prepare_draft
from job_radar.ingest import ObservedJob, ingest
from job_radar.mail_config import save_smtp
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
        app.state.auto_apply_manager.configure(True, 85)
        assert _wait_for_status(app, old_id, "skipped")
        threshold_id = _scored_job(app, "Threshold Engineer", 85)
        new_id = _scored_job(app, "New Engineer", 86)
        draft_id = _wait_for_status(app, new_id, "awaiting_review")["draft_id"]
        assert not sent
        assert app.state.db.one("SELECT state FROM vacancies WHERE id=?", (new_id,))["state"] == "prepare"
        draft = client.get(f"/api/applications/{draft_id}").json()
        response = client.post(f"/api/applications/{draft_id}/approve", json={"package_hash": draft["package_hash"]})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "sent_confirmed"
        assert sent == [new_id]
        assert app.state.db.one("SELECT state FROM vacancies WHERE id=?", (new_id,))["state"] == "applied"
        assert app.state.db.one("SELECT vacancy_id FROM auto_application_attempts WHERE vacancy_id=?", (threshold_id,)) is None
        app.state.auto_apply_manager.wake()
        time.sleep(.1)
        assert sent == [new_id]


def test_auto_apply_missing_destination_needs_review(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
        "drafting_provider": "template"})
    with TestClient(app):
        app.state.auto_apply_manager.configure(True, 80)
        identifier = _scored_job(app, "Unlinked Engineer", 90, None)
        result = _wait_for_status(app, identifier, "needs_review")
        assert "Choose an email or web application destination" in result["detail"]
        assert result["draft_id"]
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
