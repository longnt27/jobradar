from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.web import create_app


def _prepared(client: TestClient, url: str) -> dict:
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "Engineer", "dates": "2024 – 2026", "bullets": ["Built Python search systems."]})
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer", "description": "Build Python search systems.", "apply_url": url}).json()
    response = client.post(f"/api/jobs/{job['id']}/prepare", json={"provider": "template"})
    assert response.status_code == 200, response.text
    return response.json()


def test_email_send_is_explicit_and_duplicate_protected(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _prepared(client, "https://example.org/apply")
    draft = client.patch(f"/api/applications/{draft['id']}", json={"destination": {"kind": "email", "email": "jobs@example.org"}}).json()
    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings: sent.append(item["id"]) or "message-123")
    assert client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": "0" * 64}).status_code == 422
    assert not sent
    result = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": draft["package_hash"]})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "sent_confirmed"
    assert sent == [draft["id"]]
    assert client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": draft["package_hash"]}).status_code == 422
    assert len(client.get("/api/submissions").json()) == 1
    snapshot = json.loads(client.get("/api/submissions").json()[0]["package_data"])
    assert snapshot["message_data"]["body"] == draft["message_data"]["body"]


def test_web_form_inspection_and_one_click_submit(tmp_path: Path) -> None:
    posted = []
    action = [""]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f'<html><body><form method="post" action="{action[0]}"><label>Name <input name="name" required></label><label>Email <input type="email" name="email" required></label><label>Cover letter <textarea name="cover_letter" required></textarea></label><input type="file" name="resume" accept="application/pdf"><button>Apply</button></form></body></html>'.encode()
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
        client = TestClient(create_app(Settings(tmp_path)))
        draft = _prepared(client, f"http://127.0.0.1:{server.server_port}/apply")
        inspected = client.post(f"/api/applications/{draft['id']}/inspect")
        assert inspected.status_code == 200, inspected.text
        answers = inspected.json()["form_data"]["answers"]
        assert "Alex Example" in answers.values()
        assert any("I am applying" in value for value in answers.values())
        action[0] = "/changed"
        changed = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": inspected.json()["package_hash"]})
        assert changed.json()["status"] == "needs_user_attention"
        assert not posted
        action[0] = ""
        result = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": inspected.json()["package_hash"]})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "submitted_confirmed"
        assert len(posted) == 1
    finally:
        server.shutdown()
        server.server_close()
