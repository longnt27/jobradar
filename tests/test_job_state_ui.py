import socket
import sqlite3
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from job_radar.db import Database
from job_radar.drafting import prepare_draft
from job_radar.settings import Settings
from job_radar.web import create_app


def test_legacy_job_state_migrates_into_independent_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE vacancies (
          id TEXT PRIMARY KEY,
          employer_id TEXT,
          company TEXT NOT NULL,
          title TEXT NOT NULL,
          location TEXT,
          work_mode TEXT,
          description TEXT NOT NULL,
          apply_url TEXT,
          published_at TEXT,
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          score INTEGER,
          score_detail TEXT,
          analysis_status TEXT NOT NULL DEFAULT 'not_configured',
          analysis_stage TEXT,
          analysis_model TEXT,
          analysis_error TEXT,
          analyzed_at TEXT,
          state TEXT NOT NULL DEFAULT 'new',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
    """)
    timestamp = "2026-10-01T00:00:00+00:00"
    rows = [
        ("short", "interesting"),
        ("applied", "applied"),
        ("interview", "interview"),
        ("ignored", "ignored"),
    ]
    for identifier, state in rows:
        conn.execute(
            "INSERT INTO vacancies(id,company,title,description,first_seen_at,last_seen_at,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (identifier, "Example", identifier.title(), "Build Python systems.", timestamp, timestamp, state, timestamp, timestamp),
        )
    conn.commit()
    conn.close()

    db = Database(path)
    shortlisted = db.one("SELECT * FROM vacancies WHERE id='short'")
    assert shortlisted["decision_state"] == "shortlisted"
    assert shortlisted["recruiting_outcome"] == "none"
    assert shortlisted["manual_applied_at"] is None

    applied = db.one("SELECT * FROM vacancies WHERE id='applied'")
    assert applied["decision_state"] == "shortlisted"
    assert applied["manual_applied_at"] == timestamp
    assert applied["manual_applied_source"] == "legacy_state"

    interview = db.one("SELECT * FROM vacancies WHERE id='interview'")
    assert interview["decision_state"] == "shortlisted"
    assert interview["recruiting_outcome"] == "interview"
    assert interview["manual_applied_source"] == "legacy_state"

    ignored = db.one("SELECT * FROM vacancies WHERE id='ignored'")
    assert ignored["decision_state"] == "ignored"
    assert db.get_setting("job_state_v2_migrated") is True


def _profile(client: TestClient) -> None:
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={
        "company": "Prior Co",
        "role": "Engineer",
        "dates": "2024-2026",
        "bullets": ["Built Python systems."],
    }).status_code == 201


def test_job_read_decision_application_and_outcome_are_independent(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    _profile(client)

    job = client.post("/api/jobs/import", json={
        "company": "Example",
        "title": "AI Engineer",
        "description": "Build reliable Python systems.",
        "apply_url": "https://example.org/apply",
    }).json()

    initial = client.get(f"/api/jobs/{job['id']}").json()
    assert initial["read_state"] == "unseen"
    assert initial["decision_state"] == "undecided"
    assert initial["application_progress"] == "not_started"
    assert initial["recruiting_outcome"] == "none"

    seen = client.post(f"/api/jobs/{job['id']}/seen")
    assert seen.status_code == 200
    after_seen = client.get(f"/api/jobs/{job['id']}").json()
    assert after_seen["read_state"] == "seen"
    assert after_seen["decision_state"] == "undecided"

    shortlisted = client.post(f"/api/jobs/{job['id']}/decision", json={"decision": "shortlisted"})
    assert shortlisted.status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").json()["application_progress"] == "preparing"

    assert client.post(f"/api/jobs/{job['id']}/state", json={"state": "ready"}).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/state", json={"state": "applied"}).status_code == 409

    draft = prepare_draft(app.state.db, app.state.settings, job["id"], "template")
    app.state.auto_apply_manager._set_status(job["id"], "needs_review", "Draft ready for review", draft["id"])
    prepared = client.get(f"/api/jobs/{job['id']}").json()
    assert prepared["decision_state"] == "shortlisted"
    assert prepared["application_progress"] == "draft_ready"
    assert prepared["latest_draft_id"] == draft["id"]

    outcome = client.post(f"/api/jobs/{job['id']}/outcome", json={"outcome": "interview"})
    assert outcome.status_code == 200
    with_outcome = client.get(f"/api/jobs/{job['id']}").json()
    assert with_outcome["decision_state"] == "shortlisted"
    assert with_outcome["application_progress"] == "draft_ready"
    assert with_outcome["recruiting_outcome"] == "interview"

    external = client.post("/api/jobs/import", json={
        "company": "External Co", "title": "Research Engineer", "description": "Research reliable ML systems."
    }).json()
    assert client.post(f"/api/jobs/{external['id']}/manual-applied", json={"applied": True}).status_code == 200
    externally_applied = client.get(f"/api/jobs/{external['id']}").json()
    assert externally_applied["application_progress"] == "applied_external"
    assert externally_applied["manual_applied_source"] == "user_external"
    assert externally_applied["decision_state"] == "undecided"


def test_since_last_visit_snooze_ignore_reason_and_saved_views(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)

    old = client.post("/api/jobs/import", json={
        "company": "Old Co", "title": "Old Engineer", "description": "Build older Python systems."
    }).json()
    visit = client.post("/api/jobs/visit").json()
    assert visit["previous"] is None

    newer = client.post("/api/jobs/import", json={
        "company": "New Co", "title": "New Engineer", "description": "Build newer Python systems."
    }).json()
    app.state.db.execute(
        "UPDATE vacancies SET first_seen_at=datetime(?, '+2 minutes') WHERE id=?",
        (visit["current"], newer["id"]),
    )
    result = client.get("/api/jobs/page", params={
        "inbox": "since_last_visit",
        "since": visit["current"],
        "sort": "found",
    }).json()
    assert [row["id"] for row in result["items"]] == [newer["id"]]
    assert old["id"] not in {row["id"] for row in result["items"]}

    ignored = client.post(f"/api/jobs/{newer['id']}/decision", json={
        "decision": "ignored", "reason": "Salary"
    })
    assert ignored.status_code == 200
    feedback = app.state.db.one(
        "SELECT state,reason FROM feedback WHERE vacancy_id=? ORDER BY created_at DESC LIMIT 1",
        (newer["id"],),
    )
    assert feedback == {"state": "ignored", "reason": "Salary"}

    future = "2099-01-01T00:00:00+00:00"
    assert client.post(f"/api/jobs/{old['id']}/decision", json={
        "decision": "later", "snoozed_until": future
    }).status_code == 200
    app.state.db.execute(
        "UPDATE vacancies SET snoozed_until='2000-01-01T00:00:00+00:00' WHERE id=?",
        (old["id"],),
    )
    client.get("/api/jobs/page", params={"inbox": "all"})
    released = client.get(f"/api/jobs/{old['id']}").json()
    assert released["decision_state"] == "undecided"
    assert released["snoozed_until"] is None

    saved = client.post("/api/jobs/views", json={
        "name": "Strong remote jobs",
        "filters": {"inbox": "unseen", "score": "80", "mode": "remote", "garbage": "drop-me"},
        "set_default": True,
    })
    assert saved.status_code == 201
    view = saved.json()
    assert view["filters"] == {"inbox": "unseen", "score": "80", "mode": "remote"}
    assert view["default"] is True
    assert client.get("/api/jobs/views").json()[0]["id"] == view["id"]
    assert client.delete(f"/api/jobs/views/{view['id']}").json() == {"deleted": True}


def test_browser_job_inbox_triage_marks_seen_without_deciding(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    client = TestClient(app)
    first = client.post("/api/jobs/import", json={
        "company": "Example Robotics",
        "title": "AI Engineer",
        "description": "Build Python perception systems.",
        "location": "Hanoi",
        "apply_url": "https://example.org/apply",
    }).json()["id"]
    second = client.post("/api/jobs/import", json={
        "company": "Other Co",
        "title": "ML Engineer",
        "description": "Build ML systems.",
    }).json()["id"]
    app.state.db.execute(
        "UPDATE vacancies SET score=92,analysis_status='done',score_detail=? WHERE id=?",
        ('{"facts":{"salary_range":"$2,000-$3,000","location":"Hanoi","work_mode":"Hybrid"},'
         '"criteria":{"role":{"score":9,"reason":"Strong role fit"},"location":{"score":4,"reason":"Hybrid commute"}},'
         '"weights":{"role":30,"location":10},"explanation":"Good role fit."}', first),
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
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.set_default_timeout(8000)
                page.goto(f"http://127.0.0.1:{port}/#jobs")
                card = page.locator(f'[data-job-card="{first}"]')
                card.wait_for()
                assert "$2,000-$3,000" in card.inner_text()
                unread = card.get_by_role("button", name="Open AI Engineer at Example Robotics").locator(".job-unread-dot")
                assert unread.get_attribute("aria-label") == "Unseen job"

                card.get_by_role("button", name="Shortlist", exact=True).click()
                page.wait_for_function(
                    "(id) => { const node = document.querySelector('[data-job-card=\"' + id + '\"]'); "
                    "return node === null || node.textContent.includes('Shortlisted'); }",
                    arg=first,
                )
                assert client.get(f"/api/jobs/{first}").json()["decision_state"] == "shortlisted"
                page.get_by_role("button", name="Undo", exact=True).click()
                page.wait_for_function(
                    "(id) => document.querySelector('[data-job-card=\"' + id + '\"]')?.textContent.includes('Shortlist')",
                    arg=first,
                )
                assert client.get(f"/api/jobs/{first}").json()["decision_state"] == "undecided"

                page.locator(f'[data-job="{first}"]').click()
                page.get_by_role("heading", name="Why it fits").wait_for()
                state = client.get(f"/api/jobs/{first}").json()
                assert state["read_state"] == "seen"
                assert state["decision_state"] == "undecided"
                detail = page.locator("#job-detail")
                assert detail.get_by_text("No decision yet", exact=False).is_visible()
                assert detail.get_by_text("$2,000-$3,000", exact=True).is_visible()
                assert detail.get_by_role("heading", name="Application preparation").is_visible()
                assert detail.locator("details").filter(has_text="Detailed match breakdown").count() == 1

                second_card = page.locator(f'[data-job-card="{second}"]')
                second_card.get_by_role("button", name="Ignore", exact=True).click()
                dialog = page.locator("#job-ignore-reason-dialog")
                dialog.wait_for(state="visible")
                dialog.get_by_role("button", name="Salary", exact=True).click()
                page.get_by_role("status").filter(has_text="Ignore reason saved: Salary.").wait_for()
                reason = app.state.db.one(
                    "SELECT reason FROM feedback WHERE vacancy_id=? AND reason IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                    (second,),
                )
                assert reason["reason"] == "Salary"
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
