import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

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


def test_auto_apply_only_new_jobs_above_threshold_and_no_duplicate(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    app.state.db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]}],
        "drafting_provider": "template"})
    save_smtp(app.state.settings, {"host": "smtp.example.org", "port": 587, "user": "", "password": "", "from": "alex@example.org"})
    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda draft, _settings: sent.append(draft["vacancy_id"]) or "accepted")
    old_id = _scored_job(app, "Old Engineer", 99)
    with TestClient(app):
        app.state.auto_apply_manager.configure(True, 85)
        assert _wait_for_status(app, old_id, "skipped")
        threshold_id = _scored_job(app, "Threshold Engineer", 85)
        new_id = _scored_job(app, "New Engineer", 86)
        assert _wait_for_status(app, new_id, "sent")["draft_id"]
        assert sent == [new_id]
        assert app.state.db.one("SELECT state FROM vacancies WHERE id=?", (new_id,))["state"] == "applied"
        assert app.state.db.one("SELECT vacancy_id FROM auto_application_attempts WHERE vacancy_id=?", (threshold_id,)) is None
        app.state.auto_apply_manager.wake()
        time.sleep(.1)
        assert sent == [new_id]


def test_auto_apply_missing_destination_needs_review(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    with TestClient(app):
        app.state.auto_apply_manager.configure(True, 80)
        identifier = _scored_job(app, "Unlinked Engineer", 90, None)
        result = _wait_for_status(app, identifier, "needs_review")
        assert "No verified application destination" in result["detail"]
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
        with TestClient(app):
            app.state.auto_apply_manager.configure(True, 85)
            identifier = _scored_job(app, "Web Engineer", 90, f"http://127.0.0.1:{server.server_port}/apply")
            result = _wait_for_status(app, identifier, "sent")
            assert result["draft_id"]
            assert posted
            assert b'Content-Type: application/pdf' in posted[0]
            assert b'%PDF-' in posted[0]
            assert app.state.db.one("SELECT status FROM submissions WHERE vacancy_id=?", (identifier,))["status"] == "submitted_confirmed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
