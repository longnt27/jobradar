import hashlib
import json
import socket
import smtplib
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.db import new_id, now
from job_radar.drafting import get_draft, prepare_draft
from job_radar.mail_config import save_smtp
from job_radar.settings import Settings
from job_radar.web import create_app


def _prepare_email_draft(app, title: str = "AI Engineer") -> dict:
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    db.set_setting("profile", {
        "name": "Alex Example",
        "email": "alex@example.org",
        "experience": [{"company": "Prior Co", "role": "Engineer", "dates": "2024-2026",
                        "bullets": ["Built Python systems."]}],
        "drafting_provider": "template",
    })
    job_id = new_id()
    timestamp = now()
    db.execute(
        "INSERT INTO vacancies(id,company,title,description,apply_url,first_seen_at,last_seen_at,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (job_id, "Example", title, "Build reliable Python systems.", "mailto:jobs@example.org",
         timestamp, timestamp, timestamp, timestamp),
    )
    return prepare_draft(db, app.state.settings, job_id, "template")


def test_uncertain_submission_is_human_readable_and_cannot_be_retried(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    draft = _prepare_email_draft(app)
    save_smtp(app.state.settings, {
        "host": "smtp.example.org", "port": 587, "user": "", "password": "",
        "from": "alex@example.org",
    })
    attachment_bytes = b"%PDF-1.4\nexact reviewed portfolio\n%%EOF"
    attachment_path = tmp_path / "portfolio.pdf"
    attachment_path.write_bytes(attachment_bytes)
    app.state.db.execute(
        "UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
        (json.dumps({
            "fields": [{"index": 0, "name": "portfolio", "type": "file", "label": "Portfolio"}],
            "answers": {},
            "attachments": {"0": {
                "kind": "uploaded", "path": str(attachment_path),
                "sha256": hashlib.sha256(attachment_bytes).hexdigest(), "name": "portfolio.pdf",
            }},
        }), now(), draft["id"]),
    )
    draft = get_draft(app.state.db, draft["id"])
    attempts = []

    def ambiguous_send(item, _settings):
        attempts.append(item["id"])
        raise RuntimeError("Connection dropped after SMTP DATA")

    monkeypatch.setattr("job_radar.apply._send_email", ambiguous_send)

    with TestClient(app) as client:
        first = client.post(
            f"/api/applications/{draft['id']}/send",
            json={"package_hash": draft["package_hash"]},
        )
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["status"] == "submitted_unconfirmed"
        assert result["outcome"]["key"] == "submission_uncertain"
        assert result["outcome"]["label"] == "Submission status uncertain"
        assert result["outcome"]["retry_blocked"] is True

        stored = app.state.db.one("SELECT status FROM application_drafts WHERE id=?", (draft["id"],))
        assert stored["status"] == "submission_uncertain"

        detail = client.get(f"/api/applications/{draft['id']}").json()
        assert detail["latest_submission"]["outcome"]["label"] == "Submission status uncertain"
        assert any("Verify on the employer site" in blocker for blocker in detail["send_blockers"])

        second = client.post(
            f"/api/applications/{draft['id']}/send",
            json={"package_hash": draft["package_hash"]},
        )
        assert second.status_code == 422
        assert "Submission status uncertain" in second.json()["detail"]
        assert len(attempts) == 1

        proof = client.get(f"/api/submissions/{result['id']}/resume")
        assert proof.status_code == 200
        assert hashlib.sha256(proof.content).hexdigest() == draft["resume_hash"]

        attachment = client.get(f"/api/submissions/{result['id']}/attachments/0")
        assert attachment.status_code == 200
        assert attachment.content == attachment_bytes
        attachment_path.write_bytes(b"%PDF-1.4\nmutated later\n%%EOF")
        assert client.get(f"/api/submissions/{result['id']}/attachments/0").content == attachment_bytes


def test_definite_smtp_rejection_is_retryable_and_not_marked_uncertain(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    draft = _prepare_email_draft(app, "Retryable Email")
    save_smtp(app.state.settings, {
        "host": "smtp.example.org", "port": 587, "user": "", "password": "",
        "from": "alex@example.org",
    })
    calls = []

    def send(item, _settings):
        calls.append(item["id"])
        if len(calls) == 1:
            raise smtplib.SMTPAuthenticationError(535, b"Authentication rejected")
        return "accepted"

    monkeypatch.setattr("job_radar.apply._send_email", send)

    with TestClient(app) as client:
        first = client.post(
            f"/api/applications/{draft['id']}/send",
            json={"package_hash": draft["package_hash"]},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "failed"
        assert first.json()["outcome"]["key"] == "send_failed"
        assert first.json()["outcome"]["retry_blocked"] is False
        assert app.state.db.one("SELECT status FROM application_drafts WHERE id=?", (draft["id"],))["status"] == "draft"

        second = client.post(
            f"/api/applications/{draft['id']}/send",
            json={"package_hash": draft["package_hash"]},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "sent_confirmed"
        assert second.json()["outcome"]["label"] == "Email sent"
        assert len(calls) == 2


def test_interrupted_send_claim_blocks_raw_api_retry_without_submission_row(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    draft = _prepare_email_draft(app, "Interrupted Claim")
    save_smtp(app.state.settings, {
        "host": "smtp.example.org", "port": 587, "user": "", "password": "",
        "from": "alex@example.org",
    })
    app.state.db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,detail,created_at,updated_at) "
        "VALUES(?,'submission_uncertain',?,'Restarted during send',?,?)",
        (draft["vacancy_id"], draft["id"], now(), now()),
    )
    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings: sent.append(item["id"]) or "accepted")

    with TestClient(app) as client:
        detail = client.get(f"/api/applications/{draft['id']}").json()
        assert detail["send_ready"] is False
        assert any("Submission status uncertain" in blocker for blocker in detail["send_blockers"])

        retry = client.post(
            f"/api/applications/{draft['id']}/send",
            json={"package_hash": draft["package_hash"]},
        )
        assert retry.status_code == 422
        assert "Submission status uncertain" in retry.json()["detail"]
        assert sent == []

        edit = client.patch(
            f"/api/applications/{draft['id']}",
            json={"message_data": {"body": "Changed after uncertain send"}},
        )
        assert edit.status_code == 409

    import asyncio
    try:
        asyncio.run(app.state.auto_apply_manager.regenerate(draft["id"], "Try another version"))
    except ValueError as error:
        assert "cannot be regenerated" in str(error)
    else:
        raise AssertionError("Uncertain application review was allowed to regenerate")


def test_application_page_reaches_history_beyond_first_hundred_and_filters_server_side(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    timestamp = now()
    ids = []
    for index in range(105):
        job_id = new_id()
        draft_id = new_id()
        ids.append(draft_id)
        company = "Needle Company" if index == 0 else f"Company {index:03d}"
        title = "Needle Role" if index == 0 else f"Engineer {index:03d}"
        db.execute(
            "INSERT INTO vacancies(id,company,title,description,first_seen_at,last_seen_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (job_id, company, title, "Build systems.", timestamp, timestamp, timestamp, timestamp),
        )
        db.execute(
            "INSERT INTO application_drafts(id,vacancy_id,status,provider,provider_mode,evidence_ids,resume_data,"
            "message_data,form_data,destination,resume_path,resume_hash,warnings,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (draft_id, job_id, "draft", "template", "local template; no model inference", "[]",
             json.dumps({"name": "Alex"}), json.dumps({"body": "Hello"}), "{}",
             json.dumps({"kind": "manual"}), "", "", "[]", timestamp, timestamp),
        )

    with TestClient(app) as client:
        last_page = client.get("/api/applications/page", params={"page": 5, "page_size": 25}).json()
        assert last_page["total"] == 105
        assert last_page["pages"] == 5
        assert len(last_page["items"]) == 5

        search = client.get("/api/applications/page", params={"q": "Needle Role", "page_size": 25}).json()
        assert search["total"] == 1
        assert search["items"][0]["company"] == "Needle Company"
        assert "Needle Company" in search["companies"]


def test_legacy_uncertain_submission_is_shown_as_durable_proof_after_reload(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    draft = _prepare_email_draft(app, "Legacy Web Outcome")
    db = app.state.db
    submission_id = new_id()
    package = {key: draft[key] for key in (
        "vacancy_id", "provider", "provider_mode", "evidence_ids", "resume_data",
        "message_data", "form_data", "destination", "resume_path", "resume_hash",
    )}
    db.execute(
        "INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,package_data,destination,status,receipt,error,sent_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (submission_id, draft["id"], draft["vacancy_id"], draft["package_hash"],
         json.dumps(package), json.dumps(draft["destination"]), "submitted_unconfirmed",
         None, "Browser closed before confirmation was detected", now(), now()),
    )
    db.execute(
        "INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,detail,created_at,updated_at) "
        "VALUES(?,'needs_review',?,'Legacy state',?,?)",
        (draft["vacancy_id"], draft["id"], now(), now()),
    )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(10000)
                page.goto(f"http://127.0.0.1:{port}/#applications/{draft['id']}")
                page.locator("#application-detail").get_by_text("Submission status uncertain", exact=True).first.wait_for()
                assert page.get_by_role("button", name="Approve & send").is_disabled()
                assert page.get_by_text(
                    "Verify on the employer site before taking another send action.", exact=False
                ).first.is_visible()
                page.get_by_text("Exact reviewed package", exact=True).click()
                assert page.get_by_role("link", name="Open exact submitted resume").is_visible()

                page.reload()
                page.locator("#application-detail").get_by_text("Submission status uncertain", exact=True).first.wait_for()
                assert page.get_by_role("button", name="Approve & send").is_disabled()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
