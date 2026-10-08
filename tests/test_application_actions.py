import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from job_radar.db import new_id, now
from job_radar.drafting import prepare_draft
from job_radar.apply import inspect_form, send_application, send_readiness
from job_radar.ingest import ObservedJob, ingest
from job_radar.linkedin_application import find_linkedin_apply_control, linkedin_sign_in_dialog_visible
from job_radar.settings import Settings
from job_radar.web import create_app


def _source(db, kind: str) -> str:
    row = db.one("SELECT id FROM sources WHERE kind=? LIMIT 1", (kind,))
    if row:
        return row["id"]
    identifier = new_id()
    db.execute(
        "INSERT INTO sources(id,kind,name,url,config,created_at) VALUES(?,?,?,?,?,?)",
        (identifier, kind, f"Test {kind}", f"https://example.org/{kind}", "{}", now()),
    )
    return identifier


def _prepare(tmp_path: Path, kind: str, observed: ObservedJob) -> dict:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={
        "company": "Prior Co", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built Python systems."]
    })
    vacancy_id, _ = ingest(client.app.state.db, _source(client.app.state.db, kind), observed)
    draft = prepare_draft(client.app.state.db, client.app.state.settings, vacancy_id, "template")
    client.app.state.auto_apply_manager.register_review(draft)
    return draft


def test_career_form_is_resolved_with_provenance(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "career", ObservedJob(
        "https://careers.example.org/jobs/42", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
        apply_url="https://boards.greenhouse.io/example/jobs/42#app",
    ))
    action = draft["destination"]
    assert action["kind"] == "web"
    assert action["action_type"] == "web_form"
    assert action["url"].startswith("https://boards.greenhouse.io/")
    assert action["provenance"] == "career_apply_link"
    assert action["confidence"] == "high"


def test_shortlisting_queues_application_inspection_for_only_that_job(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    imported = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer",
        "description": "Build software", "location": "Hanoi", "apply_url": "https://example.org/careers/1"}).json()
    queued = []
    monkeypatch.setattr(app.state.auto_apply_manager, "queue_manual", lambda job_id, provider, prepare_anyway:
                        queued.append((job_id, provider, prepare_anyway)) or {"status": "queued"})
    app.state.db.set_setting("profile", {"drafting_provider": "codex"})
    response = client.post(f"/api/jobs/{imported['id']}/decision", json={"decision": "shortlisted"})
    assert response.status_code == 200
    assert queued == [(imported["id"], "codex", True)]


def test_linkedin_easy_apply_is_distinct_from_external_apply(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/101/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
        raw_text="AI Engineer\nEasy Apply\nBuild reliable AI systems in Python.",
    ))
    action = draft["destination"]
    assert action["kind"] == "manual"
    assert action["action_type"] == "linkedin_easy_apply"
    assert action["url"] == "https://www.linkedin.com/jobs/view/101/"
    assert action["provenance"] == "linkedin_easy_apply_control"
    assert draft["job_posting_url"] == "https://www.linkedin.com/jobs/view/101"
    assert draft["job_source_kind"] == "linkedin"


def test_linkedin_easy_apply_requires_complete_reviewed_fields_before_send(tmp_path: Path) -> None:
    from job_radar.drafting import get_draft, set_discovered_linkedin_destination

    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/107/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.", raw_text="Easy Apply",
    ))
    app = create_app(Settings(tmp_path))
    db = app.state.db
    set_discovered_linkedin_destination(db, draft["id"])
    fields = [
        {"index": 0, "step": 1, "position": 0, "type": "text", "label": "Years of experience", "required": True},
        {"index": 1, "step": 2, "position": -1, "type": "file", "label": "Resume", "required": True,
         "accept": ".pdf,application/pdf", "max_file_bytes": 2000000},
    ]
    form = {"kind": "linkedin_easy_apply", "fields": fields, "answers": {"0": ""},
            "attachments": {"1": {"kind": "resume"}}, "signature": "reviewed-form",
            "destination_url": "https://www.linkedin.com/jobs/view/107", "complete": False}
    db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft["id"]))
    blocked = send_readiness(db, app.state.settings, get_draft(db, draft["id"]))
    assert any("Inspect every LinkedIn application step" in item for item in blocked)
    assert any("Years of experience" in item for item in blocked)

    form["complete"] = True
    form["answers"]["0"] = "2"
    db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft["id"]))
    assert send_readiness(db, app.state.settings, get_draft(db, draft["id"])) == []


def test_saving_linkedin_answers_clears_stale_missing_answer_warnings(tmp_path: Path) -> None:
    from job_radar.drafting import set_discovered_linkedin_destination

    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/109/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.", raw_text="Easy Apply",
    ))
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    set_discovered_linkedin_destination(db, draft["id"])
    form = {"kind": "linkedin_easy_apply", "fields": [
        {"index": 0, "step": 2, "type": "number", "label": "Machine learning years", "required": True},
        {"index": 1, "step": 2, "type": "number", "label": "Engineering years", "required": True},
    ], "answers": {"0": "", "1": ""}, "attachments": {}, "signature": "partial-form",
        "destination_url": "https://www.linkedin.com/jobs/view/109", "complete": False,
        "inspection_blockers": ["Answer required: Machine learning years", "Answer required: Engineering years"],
        "steps_total": 4}
    db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft["id"]))

    saved = client.patch(f"/api/applications/{draft['id']}", json={
        "form_data": {**form, "answers": {"0": "2", "1": "1"}}
    })
    assert saved.status_code == 200
    updated = saved.json()
    assert updated["form_data"]["answers"] == {"0": "2", "1": "1"}
    assert updated["form_data"]["inspection_blockers"] == []
    assert not updated["form_data"]["complete"]
    readiness = send_readiness(db, app.state.settings, updated)
    assert readiness == ["Inspect every LinkedIn application step before sending"]

    missing_again = client.patch(f"/api/applications/{draft['id']}", json={
        "form_data": {**updated["form_data"], "answers": {"0": "", "1": "1"}}
    })
    assert missing_again.status_code == 200
    assert missing_again.json()["form_data"]["inspection_blockers"] == ["Answer required: Machine learning years"]


def test_inspecting_linkedin_easy_apply_saves_fields_for_application_review(tmp_path: Path, monkeypatch) -> None:
    from job_radar.drafting import set_discovered_linkedin_destination

    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/108/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.", raw_text="Easy Apply",
    ))
    app = create_app(Settings(tmp_path))
    db = app.state.db
    set_discovered_linkedin_destination(db, draft["id"])

    async def inspected(_settings, current):
        return {"kind": "linkedin_easy_apply", "fields": [{"index": 0, "step": 1, "position": 0,
                "type": "text", "label": "Years of experience", "required": True}],
                "answers": {"0": ""}, "attachments": {}, "signature": "saved-form", "complete": False,
                "inspection_blockers": ["Answer required: Years of experience"],
                "destination_url": current["destination"]["url"], "steps_total": 2}

    monkeypatch.setattr("job_radar.apply.inspect_linkedin_application", inspected, raising=False)
    result = asyncio.run(inspect_form(db, app.state.settings, draft["id"]))
    assert result["form_data"]["fields"][0]["label"] == "Years of experience"
    assert result["form_data"]["inspection_blockers"] == ["Answer required: Years of experience"]
    assert not result["form_data"]["complete"]


def test_approved_linkedin_easy_apply_uses_reviewed_form_and_records_submission(tmp_path: Path, monkeypatch) -> None:
    from job_radar.drafting import get_draft, set_discovered_linkedin_destination

    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/109/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.", raw_text="Easy Apply",
    ))
    app = create_app(Settings(tmp_path))
    db = app.state.db
    set_discovered_linkedin_destination(db, draft["id"])
    current = get_draft(db, draft["id"])
    form = {"kind": "linkedin_easy_apply", "fields": [{"index": 0, "step": 1, "position": 0,
            "type": "text", "label": "Years of experience", "required": True}],
            "answers": {"0": "2"}, "attachments": {}, "signature": "reviewed-form", "complete": True,
            "destination_url": current["destination"]["url"]}
    db.execute("UPDATE application_drafts SET form_data=? WHERE id=?", (json.dumps(form), draft["id"]))
    current = get_draft(db, draft["id"])
    seen = []

    async def send_linkedin(_settings, reviewed):
        seen.append(reviewed["form_data"]["answers"]["0"])
        return "submitted_confirmed", "LinkedIn confirmed application"

    monkeypatch.setattr("job_radar.apply._send_linkedin_easy_apply", send_linkedin, raising=False)
    result = asyncio.run(send_application(db, app.state.settings, draft["id"], current["package_hash"]))
    assert result["status"] == "submitted_confirmed"
    assert seen == ["2"]
    assert get_draft(db, draft["id"])["status"] == "sent"


def test_linkedin_listing_cannot_be_used_as_web_application_form(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/103/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
    ))
    client = TestClient(create_app(Settings(tmp_path)))
    listing = "https://www.linkedin.com/jobs/view/103/"
    edited = client.patch(f"/api/applications/{draft['id']}", json={
        "destination": {"kind": "web", "url": listing},
    })
    assert edited.status_code == 200
    assert any("not an application form" in warning for warning in edited.json()["warnings"])
    detail = client.get(f"/api/applications/{draft['id']}").json()
    assert not detail["send_ready"]
    assert any("not an application form" in blocker for blocker in detail["send_blockers"])
    inspected = client.post(f"/api/applications/{draft['id']}/inspect")
    assert inspected.status_code == 422
    assert "Open its Apply button" in inspected.json()["detail"]


def test_linkedin_apply_control_distinguishes_external_and_easy_apply() -> None:
    async def check() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<main><a href="https://jobs.example.org/apply/42">Apply</a><h2>About the job</h2></main>')
                external = await find_linkedin_apply_control(page)
                assert external["kind"] == "apply"
                assert external["url"] == "https://jobs.example.org/apply/42"
                await page.set_content('<main><button type="button">Easy Apply</button><h2>About the job</h2></main>')
                easy = await find_linkedin_apply_control(page)
                assert easy["kind"] == "linkedin_easy_apply"
                await page.set_content('<main><button type="button">Apply</button></main><div class="modal__overlay--visible">Sign in with Email</div>')
                assert await linkedin_sign_in_dialog_visible(page)
            finally:
                await browser.close()
    asyncio.run(check())


def test_linkedin_apply_discovery_saves_and_inspects_external_form(tmp_path: Path, monkeypatch) -> None:
    class FormHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<form action="/submit" method="post"><label>Full name<input name="name" required></label><button type="submit">Apply</button></form>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FormHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/apply"
        draft = _prepare(tmp_path, "linkedin", ObservedJob(
            "https://www.linkedin.com/jobs/view/104/", "AI Engineer", "Example",
            "Build reliable AI systems in Python.",
        ))

        async def fake_discovery(_settings, _posting_url):
            return {"kind": "web", "url": url, "detail": "External Apply destination opened from LinkedIn."}

        monkeypatch.setattr("job_radar.web.discover_linkedin_apply", fake_discovery)
        client = TestClient(create_app(Settings(tmp_path)))
        result = client.post(f"/api/applications/{draft['id']}/discover-apply")
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["inspection_error"] is None
        assert payload["draft"]["destination"]["url"] == url
        assert payload["draft"]["destination"]["provenance"] == "linkedin_apply_button"
        assert any(field["name"] == "name" for field in payload["draft"]["form_data"]["fields"])
        assert not client.get("/api/submissions").json()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_linkedin_apply_discovery_explains_expired_sign_in(tmp_path: Path, monkeypatch) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/105/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
    ))

    async def unexpected_discovery(_settings, _posting_url):
        raise AssertionError("Browser should not open while LinkedIn sign-in is expired")

    monkeypatch.setattr("job_radar.web.discover_linkedin_apply", unexpected_discovery)
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("social_reauth_required_linkedin", "1")
    result = TestClient(app).post(f"/api/applications/{draft['id']}/discover-apply")
    assert result.status_code == 200
    assert result.json()["action"]["kind"] == "sign_in_required"
    assert "Sign in again" in result.json()["action"]["detail"]


def test_user_requested_linkedin_easy_apply_inspection_works_while_scheduled_checks_are_paused(tmp_path: Path, monkeypatch) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/106/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
    ))

    async def discovered(_settings, _posting_url):
        return {"kind": "linkedin_easy_apply", "detail": "Easy Apply form found"}

    async def inspected(db, _settings, draft_id):
        from job_radar.drafting import get_draft
        current = get_draft(db, draft_id)
        assert current["destination"]["kind"] == "linkedin_easy_apply"
        return current

    monkeypatch.setattr("job_radar.web.discover_linkedin_apply", discovered)
    monkeypatch.setattr("job_radar.web.inspect_form", inspected)
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("linkedin_automation_paused", True)
    client = TestClient(app)
    assert client.get(f"/api/applications/{draft['id']}").json()["linkedin_automation_paused"]
    result = client.post(f"/api/applications/{draft['id']}/discover-apply")
    assert result.status_code == 200
    assert result.json()["action"]["kind"] == "linkedin_easy_apply"
    assert result.json()["draft"]["destination"]["kind"] == "linkedin_easy_apply"
    assert not app.state.db.get_setting("linkedin_automation_paused", False)


def test_closed_linkedin_posting_clears_obsolete_apply_path(tmp_path: Path, monkeypatch) -> None:
    from job_radar.drafting import set_discovered_linkedin_destination

    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/111/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.", raw_text="Easy Apply",
    ))
    app = create_app(Settings(tmp_path))
    set_discovered_linkedin_destination(app.state.db, draft["id"])

    async def discovered(_settings, _posting_url):
        return {"kind": "closed", "detail": "This posting is no longer accepting applications."}

    monkeypatch.setattr("job_radar.web.discover_linkedin_apply", discovered)
    result = TestClient(app).post(f"/api/applications/{draft['id']}/discover-apply")
    assert result.status_code == 200
    current = result.json()["draft"]
    assert current["destination"]["kind"] == "manual"
    assert current["form_data"].get("fields") == []
    assert "no longer accepting" in " ".join(current["warnings"])


def test_linkedin_external_apply_uses_external_form(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/102/", "Research Engineer", "Example",
        "Research and deploy computer vision systems.",
        apply_url="https://jobs.lever.co/example/abc123",
        raw_text="Research Engineer\nApply\nResearch and deploy computer vision systems.",
    ))
    action = draft["destination"]
    assert action["action_type"] == "web_form"
    assert action["url"] == "https://jobs.lever.co/example/abc123"
    assert action["provenance"] == "linkedin_external_apply"


def test_facebook_explicit_email_overrides_generic_outbound_link(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "facebook", ObservedJob(
        "https://www.facebook.com/groups/example/posts/1/", "AI Engineer", "Example",
        "We are hiring an AI Engineer. Send your CV to hiring@example.org.",
        apply_url="https://example.org/about-us",
        raw_text="We are hiring an AI Engineer. Send your CV to hiring@example.org.",
    ))
    action = draft["destination"]
    assert action["action_type"] == "email"
    assert action["email"] == "hiring@example.org"
    assert action["provenance"] == "facebook_posting_instruction"


def test_facebook_explicit_web_form_is_preserved(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "facebook", ObservedJob(
        "https://www.facebook.com/groups/example/posts/2/", "Data Engineer", "Example",
        "Apply using the form below for our Data Engineer role.",
        apply_url="https://forms.gle/ExampleForm123",
        raw_text="Apply using the form below for our Data Engineer role.",
    ))
    action = draft["destination"]
    assert action["action_type"] == "web_form"
    assert action["url"] == "https://forms.gle/ExampleForm123"
    assert action["provenance"] == "facebook_form_link"


def test_conflicting_explicit_destinations_require_manual_review(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "facebook", ObservedJob(
        "https://www.facebook.com/groups/example/posts/3/", "ML Engineer", "Example",
        "Apply at the form below or send your CV to jobs@example.org.",
        apply_url="https://forms.gle/AnotherForm123",
        raw_text="Apply at the form below or send your CV to jobs@example.org.",
    ))
    action = draft["destination"]
    assert action["kind"] == "manual"
    assert action["action_type"] == "manual"
    assert action["provenance"] == "conflicting_explicit_evidence"
    assert any("No verified application method" in warning for warning in draft["warnings"])
