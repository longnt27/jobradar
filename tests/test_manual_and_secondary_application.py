import json
import socket
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.db import new_id, now
from job_radar.drafting import prepare_draft
from job_radar.application_action import posting_application_emails
from job_radar.settings import Settings
from job_radar.web import create_app


def _draft(client: TestClient, description: str, apply_url: str | None = None) -> dict:
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={
        "company": "Example Labs", "role": "Engineer", "dates": "2024-2026",
        "bullets": ["Built useful Python systems."],
    }).status_code == 201
    job = client.post("/api/jobs/import", json={
        "company": "F.Learning Studio", "title": "AI Engineer",
        "description": description, "apply_url": apply_url,
    }).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    client.app.state.auto_apply_manager.register_review(draft)
    return draft


def test_manual_send_records_external_delivery_and_removes_draft_from_unsent(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _draft(client, "Contact us through the Facebook post to apply.")
    response = client.post(f"/api/applications/{draft['id']}/record-manual-send", json={
        "channel": "Facebook Messenger", "target": "Facebook post", "note": "Sent my CV and introduction",
    })
    assert response.status_code == 200, response.text
    assert response.json()["outcome"]["label"] == "Sent manually"
    detail = client.get(f"/api/applications/{draft['id']}").json()
    assert detail["review_status"] == "sent"
    assert detail["latest_submission"]["destination"]["channel"] == "Facebook Messenger"
    assert detail["latest_submission"]["receipt"] == "Sent my CV and introduction"
    assert client.get("/api/applications/page", params={"delivery": "unsent"}).json()["total"] == 0
    assert client.get(f"/api/jobs/{draft['vacancy_id']}").json()["application_progress"] == "applied"
    assert client.post(f"/api/applications/{draft['id']}/record-manual-send", json={
        "channel": "Facebook Messenger", "target": "Facebook post",
    }).status_code == 422


def test_questions_and_general_contact_emails_are_not_application_destinations() -> None:
    assert posting_application_emails("Apply online or email questions to hiring@example.org") == []
    assert posting_application_emails("Please apply online or contact our team at hello@example.org") == []
    assert posting_application_emails("Apply online or contact support at help@example.org") == []
    assert posting_application_emails("Apply online or reach us at hello@example.org") == []
    assert posting_application_emails("Please apply via LinkedIn or contact HR at hr@example.org") == []
    assert posting_application_emails("Apply online. For enquiries email info@example.org") == []
    assert posting_application_emails("Please send us your CV via jobs@example.org") == ["jobs@example.org"]
    assert posting_application_emails("Interested applicants should send their application to jobs@example.org") == ["jobs@example.org"]
    assert posting_application_emails("Ứng tuyển: Gửi CV về careers@example.org") == ["careers@example.org"]


def test_manual_email_record_blocks_same_address_as_an_additional_send(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _draft(client, "Please send your CV via jobs@example.org")
    assert client.post(f"/api/applications/{draft['id']}/record-manual-send", json={
        "channel": "Email", "target": "HR inbox",
    }).status_code == 422
    assert client.post(f"/api/applications/{draft['id']}/record-manual-send", json={
        "channel": "Email", "target": "mailto:jobs@example.org",
    }).status_code == 200
    detail = client.get(f"/api/applications/{draft['id']}").json()
    assert detail["latest_submission"]["destination"]["target"] == "jobs@example.org"
    assert detail["additional_email_options"] == []
    assert client.post(f"/api/applications/{draft['id']}/send-additional-email", json={
        "email": "jobs@example.org", "subject": "Application", "body": "Please see my CV.",
        "package_hash": draft["package_hash"],
    }).status_code == 422


def test_confirmed_easy_apply_can_be_followed_by_one_verified_posting_email(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _draft(client,
        "Build AI tools. Please send us your CV via f.creativeteam@gmail.com\n"
        "Subject: [AI Engineer]_Your Name",
        "https://www.linkedin.com/jobs/view/4478414934",
    )
    db = client.app.state.db
    timestamp = now()
    db.execute("UPDATE application_drafts SET status='sent' WHERE id=?", (draft["id"],))
    db.execute(
        "INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,package_data,destination,status,sent_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (new_id(), draft["id"], draft["vacancy_id"], draft["package_hash"], "{}",
         json.dumps({"kind": "linkedin_easy_apply", "url": "https://www.linkedin.com/jobs/view/4478414934"}),
         "submitted_confirmed", timestamp, timestamp),
    )
    detail = client.get(f"/api/applications/{draft['id']}").json()
    option = detail["additional_email_options"][0]
    assert option["email"] == "f.creativeteam@gmail.com"
    assert option["suggested_subject"] == "[AI Engineer]_Alex Example"
    assert client.post("/api/setup/smtp", json={
        "host": "smtp.example.org", "port": 587, "user": "alex", "password": "secret",
        "from_address": "alex@example.org",
    }).status_code == 200

    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings: sent.append(item) or "smtp-message-id")
    assert client.post(f"/api/applications/{draft['id']}/send-additional-email", json={
        "email": "random@example.org", "subject": option["suggested_subject"],
        "body": "Please find my CV attached.", "package_hash": draft["package_hash"],
    }).status_code == 422
    response = client.post(f"/api/applications/{draft['id']}/send-additional-email", json={
        "email": option["email"], "subject": option["suggested_subject"],
        "body": "Please find my CV attached.", "package_hash": draft["package_hash"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent_confirmed"
    assert sent[0]["destination"]["email"] == option["email"]
    assert sent[0]["message_data"]["subject"] == option["suggested_subject"]
    assert len(client.get("/api/submissions").json()) == 2
    assert client.post(f"/api/applications/{draft['id']}/send-additional-email", json={
        "email": option["email"], "subject": option["suggested_subject"],
        "body": "Please find my CV attached.", "package_hash": draft["package_hash"],
    }).status_code == 422


def test_failed_second_channel_does_not_erase_confirmed_job_application(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    draft = _draft(client, "Please send your CV via jobs@example.org")
    assert client.post(f"/api/applications/{draft['id']}/record-manual-send", json={
        "channel": "Facebook Messenger", "target": "Facebook post",
    }).status_code == 200
    timestamp = "2099-01-01T00:00:00+00:00"
    client.app.state.db.execute(
        "INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,package_data,destination,status,sent_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (new_id(), draft["id"], draft["vacancy_id"], "failed-email", "{}",
         json.dumps({"kind": "email", "email": "jobs@example.org"}), "failed", timestamp, timestamp),
    )
    job = client.get(f"/api/jobs/{draft['vacancy_id']}").json()
    assert job["application_progress"] == "applied"
    client.app.state.db.execute(
        "UPDATE submissions SET status='submitted_unconfirmed' WHERE vacancy_id=? AND package_hash='failed-email'",
        (draft["vacancy_id"],),
    )
    job = client.get(f"/api/jobs/{draft['vacancy_id']}").json()
    assert job["application_progress"] == "attention"
    assert job["has_confirmed_submission"] is True


def test_manual_send_is_available_in_application_review(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    draft = _draft(client, "Message the team on Facebook to apply.")
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
                page.goto(f"http://127.0.0.1:{port}/#applications/{draft['id']}")
                page.locator("#manual-send-disclosure summary").click()
                page.locator("#record-manual-send").wait_for()
                page.locator("#manual-send-channel").select_option("Facebook Messenger")
                page.locator("#manual-send-target").fill("https://facebook.com/groups/example/posts/1")
                page.locator("#manual-send-note").fill("Sent my CV")
                with page.expect_response(lambda response: response.url.endswith("/record-manual-send")) as sent:
                    page.locator("#record-manual-send").click()
                assert sent.value.status == 200
                page.get_by_role("heading", name="Change log").wait_for()
                assert page.get_by_text("Facebook Messenger").count() >= 1
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_reviewed_secondary_email_is_visible_after_easy_apply(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    draft = _draft(client,
        "Please send us your CV via f.creativeteam@gmail.com\nSubject: [AI Engineer]_Your Name",
        "https://www.linkedin.com/jobs/view/4478414934",
    )
    timestamp = now()
    app.state.db.execute("UPDATE application_drafts SET status='sent' WHERE id=?", (draft["id"],))
    app.state.db.execute(
        "INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,package_data,destination,status,sent_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (new_id(), draft["id"], draft["vacancy_id"], draft["package_hash"], "{}",
         json.dumps({"kind": "linkedin_easy_apply", "url": "https://www.linkedin.com/jobs/view/4478414934"}),
         "submitted_confirmed", timestamp, timestamp),
    )
    assert client.post("/api/setup/smtp", json={
        "host": "smtp.example.org", "port": 587, "user": "alex", "password": "secret",
        "from_address": "alex@example.org",
    }).status_code == 200
    sent = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings: sent.append(item) or "smtp-message-id")
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
                page.on("dialog", lambda dialog: dialog.accept())
                page.goto(f"http://127.0.0.1:{port}/#applications/{draft['id']}")
                page.locator("#send-additional-email").wait_for()
                assert page.locator("#additional-email-address").input_value() == "f.creativeteam@gmail.com"
                assert page.locator("#additional-email-subject").input_value() == "[AI Engineer]_Alex Example"
                with page.expect_response(lambda response: response.url.endswith("/send-additional-email")) as email_response:
                    page.locator("#send-additional-email").click()
                assert email_response.value.status == 200
                page.get_by_role("heading", name="Email sent").wait_for()
                assert len(sent) == 1
                assert page.locator("#send-additional-email").count() == 0
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_application_deep_link_opens_a_draft_on_later_page(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    draft = _draft(client, "Build AI tools.")
    original = app.state.db.one("SELECT * FROM application_drafts WHERE id=?", (draft["id"],))
    columns = list(original)
    for index in range(25):
        copy = {**original, "id": new_id(), "updated_at": f"2099-01-01T00:00:{index:02d}+00:00"}
        app.state.db.execute(
            f"INSERT INTO application_drafts({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            tuple(copy[column] for column in columns),
        )
    assert draft["id"] not in {item["id"] for item in client.get("/api/applications/page").json()["items"]}
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
                page.goto(f"http://127.0.0.1:{port}/#applications/{draft['id']}")
                page.locator(f'[data-application="{draft["id"]}"]').wait_for(timeout=5000)
                assert page.locator("#applications-page-summary").inner_text() == "Page 2 of 2"
                assert page.evaluate("location.hash") == f"#applications/{draft['id']}"
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
