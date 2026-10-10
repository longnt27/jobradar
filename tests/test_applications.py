import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from job_radar.drafting import ModelDraft, prepare_draft, regenerate_draft
from job_radar.settings import Settings
from job_radar.apply import _default_answer
from job_radar.notifications import save_telegram
from job_radar.web import create_app


def _prepared(client: TestClient, url: str) -> dict:
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "Engineer", "dates": "2024 – 2026", "bullets": ["Built Python search systems."]})
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer", "description": "Build Python search systems.", "apply_url": url}).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    client.app.state.auto_apply_manager.register_review(draft)
    asyncio.run(client.app.state.auto_apply_manager.notify_review(draft["id"]))
    return draft


def test_email_send_is_explicit_and_duplicate_protected(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _prepared(client, "https://example.org/apply")
    draft = client.patch(f"/api/applications/{draft['id']}", json={"destination": {"kind": "email", "email": "jobs@example.org"}}).json()
    assert client.post("/api/setup/smtp", json={"host": "smtp.example.org", "port": 587,
        "user": "alex", "password": "secret", "from_address": "alex@example.org"}).status_code == 200
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


def test_web_message_save_reuses_resume_and_does_not_notify_telegram(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _prepared(client, "https://example.org/apply")
    before = client.get(f"/api/applications/{draft['id']}").json()
    save_telegram(client.app.state.settings, {"token": "test-token", "chat_id": "123"})

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Message-only web save must not render a PDF or deliver Telegram")

    monkeypatch.setattr("job_radar.drafting.render_resume", unexpected)
    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", unexpected)
    response = client.patch(f"/api/applications/{draft['id']}", json={
        "message_data": {**before["message_data"], "body": "Updated application message."}})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["resume_path"] == before["resume_path"]
    assert saved["resume_hash"] == before["resume_hash"]
    assert saved["message_data"]["body"] == "Updated application message."
    assert client.get(f"/api/applications/{draft['id']}").json()["telegram_status"] == "web_only"


def test_manual_application_is_registered_for_review_and_edit_refreshes_version(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    draft = _prepared(client, "https://example.org/apply")
    status = client.get(f"/api/applications/{draft['id']}").json()
    assert status["review_status"] == "needs_review"
    assert status["telegram_status"] == "not_configured"
    previous_hash = status["package_hash"]
    updated = client.patch(f"/api/applications/{draft['id']}", json={
        "message_data": {**status["message_data"], "body": "Revised application message"}})
    assert updated.status_code == 200, updated.text
    revised = client.get(f"/api/applications/{draft['id']}").json()
    assert revised["package_hash"] != previous_hash
    assert revised["review_hash"] == revised["package_hash"]


def test_application_list_is_compact_and_detail_explains_preparation(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _prepared(client, "https://example.org/apply")
    page = client.get("/api/applications/page").json()
    card = page["items"][0]
    assert card["id"] == draft["id"]
    assert card["job_title"] == "Engineer"
    assert "resume_data" not in card
    assert "review_context" not in card
    detail = client.get(f"/api/applications/{draft['id']}").json()
    assert detail["preparation_requested_by"] == "automation"
    assert detail["vacancy_id"] == draft["vacancy_id"]


def test_failed_ai_preparation_is_visible_without_exposing_provider_payload(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    strong = client.post("/api/jobs/import", json={
        "company": "SETA", "title": "AI Engineer", "description": "Build AI systems.",
    }).json()
    weak = client.post("/api/jobs/import", json={
        "company": "Other", "title": "Other Engineer", "description": "Build systems.",
    }).json()
    for job, score in ((strong, 91), (weak, 60)):
        db.execute("UPDATE vacancies SET analysis_status='done',score=? WHERE id=?", (score, job["id"]))
        db.execute(
            "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,detail,requested_by,created_at,updated_at) "
            "VALUES(?,'needs_review',NULL,?,'automation','2026-10-08T00:00:00Z','2026-10-08T00:00:00Z')",
            (job["id"], "Automatic preparation stopped: codex drafting failed: "
             "candidate private payload. ERROR: Your workspace is out of credits."),
        )

    response = client.get("/api/application-preparations")
    assert response.status_code == 200
    issues = response.json()
    assert len(issues) == 1
    assert issues[0]["vacancy_id"] == strong["id"]
    assert issues[0]["label"] == "Out of credits"
    assert "Retry" in issues[0]["reason"]
    assert "private payload" not in json.dumps(issues)
    assert client.get("/api/applications/page").json()["total"] == 0


def test_missing_smtp_settings_do_not_lock_future_send(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _prepared(client, "https://example.org/apply")
    draft = client.patch(f"/api/applications/{draft['id']}", json={"destination": {"kind": "email", "email": "jobs@example.org"}}).json()
    first = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": draft["package_hash"]})
    assert first.status_code == 422
    assert not client.get("/api/submissions").json()

    configured = client.post("/api/setup/smtp", json={"host": "smtp.example.org", "port": 587,
        "user": "alex", "password": "secret", "from_address": "alex@example.org"})
    assert configured.status_code == 200
    monkeypatch.setattr("job_radar.apply._send_email", lambda *_args: "accepted")
    second = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": draft["package_hash"]})
    assert second.json()["status"] == "sent_confirmed"


def test_destination_warning_and_send_readiness_follow_current_draft(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]})
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer", "description": "Build Python systems."}).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    client.app.state.auto_apply_manager.register_review(draft)
    assert any("No verified application method" in warning for warning in draft["warnings"])
    client.app.state.db.execute(
        "UPDATE application_drafts SET warnings=? WHERE id=?",
        (json.dumps(["No application destination is known. Add an email address or application URL before sending."]), draft["id"]),
    )
    refreshed = client.get(f"/api/applications/{draft['id']}").json()
    assert refreshed["warnings"] == [
        "No verified application method was found. Check the original posting's Apply instructions before sending."
    ]
    assert client.get(f"/api/applications/{draft['id']}").json()["send_ready"] is False
    updated = client.patch(f"/api/applications/{draft['id']}", json={"destination": {"kind": "email", "email": "jobs@example.org"}}).json()
    assert not any("destination" in warning for warning in updated["warnings"])
    assert client.get(f"/api/applications/{draft['id']}").json()["send_ready"] is False
    client.post("/api/setup/smtp", json={"host": "smtp.example.org", "port": 587, "user": "alex", "password": "secret", "from_address": "alex@example.org"})
    assert client.get(f"/api/applications/{draft['id']}").json()["send_ready"] is True


def test_name_parts_require_explicit_candidate_preference() -> None:
    first = {"name": "first_name", "id": "", "label": "First name", "type": "text"}
    last = {"name": "last_name", "id": "", "label": "Last name", "type": "text"}
    profile = {"name": "Nguyen Trung Long"}
    assert _default_answer(first, profile, {}) == ""
    assert _default_answer(last, profile, {}) == ""
    profile.update({"given_name": "Long", "family_name": "Nguyen Trung"})
    assert _default_answer(first, profile, {}) == "Long"
    assert _default_answer(last, profile, {}) == "Nguyen Trung"


def test_web_form_inspection_and_one_click_submit(tmp_path: Path) -> None:
    posted = []
    action = [""]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f'<html><body><form method="post" enctype="multipart/form-data" action="{action[0]}"><label>Name <input name="name" required></label><label>Email <input type="email" name="email" required></label><label>Cover letter <textarea name="cover_letter" required></textarea></label><input type="file" name="resume" accept="application/pdf"><button>Apply</button></form></body></html>'.encode()
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
        assert any("I saw the posting" in value and "attached resume" in value for value in answers.values())
        form_data = inspected.json()["form_data"]
        resume_field = next(field for field in form_data["fields"] if field["type"] == "file")
        form_data["attachments"] = {str(resume_field["index"]): {"kind": "resume"}}
        inspected_draft = client.patch(f"/api/applications/{draft['id']}", json={"form_data": form_data}).json()
        action[0] = "/changed"
        changed = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": inspected_draft["package_hash"]})
        assert changed.json()["status"] == "needs_user_attention"
        assert not posted
        action[0] = ""
        result = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": inspected_draft["package_hash"]})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "submitted_confirmed"
        assert len(posted) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_static_thank_you_text_does_not_confirm_blocked_form(tmp_path: Path) -> None:
    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<html><body><p>Thank you for visiting our careers page.</p><form method="post"><label>Email <input type="email" name="email" required></label><button>Apply</button></form></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posted.append(True)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = TestClient(create_app(Settings(tmp_path)))
        draft = _prepared(client, f"http://127.0.0.1:{server.server_port}/apply")
        inspected = client.post(f"/api/applications/{draft['id']}/inspect").json()
        form_data = inspected["form_data"]
        email_field = next(field for field in form_data["fields"] if field["type"] == "email")
        form_data["answers"][str(email_field["index"])] = "invalid-email"
        reviewed = client.patch(f"/api/applications/{draft['id']}", json={"form_data": form_data}).json()
        outcome = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": reviewed["package_hash"]})
        assert outcome.json()["status"] == "needs_user_attention"
        assert not posted
        assert client.get(f"/api/applications/{draft['id']}").json()["status"] == "draft"
    finally:
        server.shutdown()
        server.server_close()


def test_required_radio_group_accepts_one_reviewed_choice(tmp_path: Path) -> None:
    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<html><body><form method="post"><label>Yes <input type="radio" name="authorized" value="yes" required></label><label>No <input type="radio" name="authorized" value="no" required></label><button>Apply</button></form></body></html>'
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
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = TestClient(create_app(Settings(tmp_path)))
        draft = _prepared(client, f"http://127.0.0.1:{server.server_port}/apply")
        inspected = client.post(f"/api/applications/{draft['id']}/inspect").json()
        form_data = inspected["form_data"]
        radios = [field for field in form_data["fields"] if field["type"] == "radio"]
        form_data["answers"][str(radios[0]["index"])] = "yes"
        reviewed = client.patch(f"/api/applications/{draft['id']}", json={"form_data": form_data}).json()
        result = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": reviewed["package_hash"]})
        assert result.json()["status"] == "submitted_confirmed"
        assert len(posted) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_each_file_field_uses_its_reviewed_attachment(tmp_path: Path) -> None:
    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<html><body><form method="post" enctype="multipart/form-data"><label>Resume <input type="file" name="resume" accept="application/pdf" required></label><label>Cover letter <input type="file" name="cover_letter" accept="application/pdf" required></label><button>Apply</button></form></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posted.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Thank you. Application received.</body></html>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = TestClient(create_app(Settings(tmp_path)))
        draft = _prepared(client, f"http://127.0.0.1:{server.server_port}/apply")
        inspected = client.post(f"/api/applications/{draft['id']}/inspect").json()
        fields = {field["name"]: field for field in inspected["form_data"]["fields"]}
        form_data = inspected["form_data"]
        form_data["attachments"] = {str(fields["resume"]["index"]): {"kind": "resume"}}
        partial = client.patch(f"/api/applications/{draft['id']}", json={"form_data": form_data}).json()
        assert client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": partial["package_hash"]}).status_code == 422
        assert not posted
        upload = client.post(f"/api/applications/{draft['id']}/attachments", files={"file": ("cover.pdf", b"%PDF-1.4\nCOVER LETTER UNIQUE\n%%EOF", "application/pdf")})
        assert upload.status_code == 200
        form_data["attachments"][str(fields["cover_letter"]["index"])] = upload.json()
        reviewed = client.patch(f"/api/applications/{draft['id']}", json={"form_data": form_data}).json()
        outcome = client.post(f"/api/applications/{draft['id']}/send", json={"package_hash": reviewed["package_hash"]})
        assert outcome.json()["status"] == "submitted_confirmed"
        assert b"COVER LETTER UNIQUE" in posted[0]
        assert b'name="resume"; filename="' in posted[0]
        assert b'name="cover_letter"; filename="' in posted[0]
        assert b"COVER LETTER UNIQUE" not in posted[0].split(b'name="resume"; filename="')[1].split(b'name="cover_letter"; filename="')[0]
    finally:
        server.shutdown()
        server.server_close()



def test_review_context_explains_selected_evidence_and_risky_claims(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    selected = client.post("/api/evidence", json={
        "kind": "project", "title": "Search Platform",
        "claim": "Built a Python search platform used by 120 users",
        "support": ["Python", "search"], "approved": True,
    }).json()["id"]
    client.post("/api/evidence", json={
        "kind": "project", "title": "Vision Demo",
        "claim": "Built a computer vision demo",
        "support": ["vision"], "approved": True,
    })
    job = client.post("/api/jobs/import", json={
        "company": "Example", "title": "Search Engineer",
        "description": "Build Python search systems.",
    }).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    detail = client.get(f"/api/applications/{draft['id']}").json()
    chosen = {item["id"]: item for item in detail["review_context"]["selected_evidence"]}
    assert selected in chosen
    assert "search" in chosen[selected]["matched_terms"]
    assert detail["review_context"]["risky_claims"]


def test_targeted_message_regeneration_preserves_resume_and_reports_diff(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026",
                        "bullets": ["Built Python systems."]}],
    })
    client.put("/api/profile", json=profile)
    job = client.post("/api/jobs/import", json={
        "company": "Example", "title": "Engineer",
        "description": "Build Python systems.",
    }).json()
    calls = {"count": 0}
    def fake_run(*_args, **_kwargs):
        calls["count"] += 1
        return ModelDraft(
            selected_evidence_ids=[], project_bullets=[],
            summary=f"Summary {calls['count']}",
            fit_text=f"Body {calls['count']}",
        )
    monkeypatch.setattr("job_radar.drafting._run_provider", fake_run)
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "codex")
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda _provider, _prompt, response_type:
                        response_type.model_validate({"fit": "I built Python systems at Prior and have relevant personal projects."}))
    before_resume = draft["resume_data"]
    revised = regenerate_draft(client.app.state.db, client.app.state.settings, draft["id"],
                               "Shorter email", "message")
    assert revised["resume_data"] == before_resume
    assert "I built Python systems at Prior" in revised["message_data"]["body"]
    assert revised["message_data"]["body"].startswith("Dear Example hiring team,")
    assert [change["section"] for change in revised["changes"]] == ["message"]


def test_delete_application_draft(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built Python systems."]}],
    })
    client.put("/api/profile", json=profile)
    job = client.post("/api/jobs/import", json={
        "company": "Discard Co", "title": "Software Engineer",
        "description": "Python developer role.",
    }).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    draft_id = draft["id"]
    assert client.get(f"/api/applications/{draft_id}").status_code == 200

    # Discard with ignore_job=True
    resp = client.delete(f"/api/applications/{draft_id}?ignore_job=true")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert resp.json()["job_ignored"] is True

    # Draft should no longer exist
    assert client.get(f"/api/applications/{draft_id}").status_code == 404

    # Job should be ignored
    job_detail = client.get(f"/api/jobs/{job['id']}").json()
    assert job_detail["decision_state"] == "ignored"


def test_delete_application_draft_sent_forbidden(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built Python systems."]}],
    })
    client.put("/api/profile", json=profile)
    job = client.post("/api/jobs/import", json={
        "company": "Sent Co", "title": "Lead Engineer",
        "description": "Leadership role.",
    }).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    draft_id = draft["id"]
    # Simulate sent status
    client.app.state.db.execute("UPDATE application_drafts SET status='sent' WHERE id=?", (draft_id,))
    resp = client.delete(f"/api/applications/{draft_id}")
    assert resp.status_code == 422
    assert "Sent applications cannot be deleted" in resp.text


def test_application_page_sorting_by_status_and_recency(tmp_path: Path) -> None:
    from job_radar.db import new_id

    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db

    profile = client.get("/api/profile").json()
    profile.update({
        "name": "Alex Example", "email": "alex@example.org",
        "experience": [{"company": "Prior", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built Python systems."]}],
    })
    client.put("/api/profile", json=profile)

    def add_item(suffix: str, draft_status: str, attempt_status: str | None = None, attempt_detail: str | None = None,
                 submission_status: str | None = None, updated_at: str = "2026-10-01T00:00:00Z") -> str:
        job = client.post("/api/jobs/import", json={
            "company": f"Company {suffix}", "title": f"Role {suffix}", "description": "Build systems and software.",
        }).json()
        draft = prepare_draft(db, client.app.state.settings, job["id"], "template")
        draft_id = draft["id"]
        db.execute("UPDATE application_drafts SET status=?, updated_at=?, created_at=? WHERE id=?",
                   (draft_status, updated_at, updated_at, draft_id))
        if attempt_status:
            db.execute(
                "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,detail,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (job["id"], attempt_status, draft_id, attempt_detail or "", updated_at, updated_at),
            )
        if submission_status:
            sub_id = new_id()
            db.execute(
                "INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,package_data,destination,status,sent_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (sub_id, draft_id, job["id"], draft["package_hash"], "{}", "{}", submission_status, updated_at, updated_at),
            )
        return draft_id

    # 1. Generation failed (newest and older)
    fail_old = add_item("fail_old", "failed", updated_at="2026-10-01T10:00:00Z")
    fail_new = add_item("fail_new", "draft", attempt_status="needs_review", attempt_detail="drafting failed with error", updated_at="2026-10-02T10:00:00Z")

    # 2. Need changes (newest and older)
    change_old = add_item("change_old", "draft", attempt_status="needs_review", attempt_detail="manual check needed", updated_at="2026-10-03T10:00:00Z")
    change_new = add_item("change_new", "draft", attempt_status="needs_confirmation", updated_at="2026-10-04T10:00:00Z")

    # 3. Ready to review (newest and older)
    ready_old = add_item("ready_old", "draft", attempt_status="awaiting_review", updated_at="2026-10-05T10:00:00Z")
    ready_new = add_item("ready_new", "draft", attempt_status="awaiting_review", updated_at="2026-10-06T10:00:00Z")

    # 4. Sent (newest and older)
    sent_old = add_item("sent_old", "sent", submission_status="sent_confirmed", updated_at="2026-10-07T10:00:00Z")
    sent_new = add_item("sent_new", "sent", submission_status="sent_confirmed", updated_at="2026-10-08T10:00:00Z")

    page = client.get("/api/applications/page", params={"page_size": 20}).json()
    item_ids = [item["id"] for item in page["items"]]

    expected = [
        fail_new, fail_old,      # Group 1: Generation failed, newest first
        change_new, change_old,  # Group 2: Need changes, newest first
        ready_new, ready_old,    # Group 3: Ready to review, newest first
        sent_new, sent_old,      # Group 4: Sent, newest first
    ]
    assert item_ids == expected


