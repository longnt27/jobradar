import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from job_radar.review_telegram import build_review_pdf, format_review_details, send_preparation_notice, send_review_packet
from job_radar.drafting import ModelDraft, get_draft, prepare_draft
from job_radar.web import create_app
from job_radar.settings import Settings
from job_radar.notifications import save_telegram


def _draft(pdf: Path) -> dict:
    return {"id": "a" * 32, "package_hash": "b" * 64, "job_title": "AI Engineer", "job_score": 80,
            "company": "Example", "resume_path": str(pdf),
            "job_description": "Build reliable search services. " + ("Full job description filler. " * 80),
            "job_apply_url": "https://example.org/jobs/ai-engineer", "job_location": "Hanoi",
            "job_work_mode": "Hybrid",
            "job_score_detail": {
                "facts": {"required_skills": ["Python", "Search"], "years_required": 2,
                          "location": "Hanoi", "work_mode": "Hybrid"},
                "criteria": {
                    "role": {"score": 9, "reason": "Direct role fit"},
                    "experience": {"score": 6, "reason": "One year below the stated preference"},
                },
            },
            "review_context": {
                "selected_evidence": [{"title": "Search Platform", "reason": "Matches Python, search"}],
                "risky_claims": [{"text": "Improved search latency by 30%"}],
                "relevant_alternatives": [],
            },
            "resume_data": {"name": "Alex Example", "email": "alex@example.org", "phone": "123",
                "summary": "Python engineer", "experience": [{"company": "Prior Co", "role": "ML Engineer",
                    "dates": "2023–2025", "bullets": ["Built search"]}],
                "projects": [{"title": "Vision", "bullets": ["Trained vision models"]}],
                "education": [{"school": "Example University", "degree": "BS"}],
                "skills": ["Python"], "achievements": ["Award"]},
            "message_data": {"subject": "Application for AI Engineer", "body": "Dear team, I built search."},
            "destination": {"kind": "email", "email": "jobs@example.org"},
            "form_data": {"fields": [{"index": 0, "label": "Why join?", "type": "text", "required": True}],
                          "answers": {"0": "To build useful products"}, "attachments": {}},
            "warnings": ["Review claims"]}


def test_review_details_are_human_first_and_omit_machine_only_detail(tmp_path: Path) -> None:
    draft = _draft(tmp_path / "resume.pdf")
    text = format_review_details(draft, ["Check the form"])
    for expected in (
        "AI Engineer", "80/100", "jobs@example.org", "https://example.org/jobs/ai-engineer",
        "Skills: Python, Search", "Experience: 2 years",
        "Main gap to check: One year below the stated preference",
        "Search Platform", "Improved search latency by 30%",
        "Application for AI Engineer", "Dear team", "Why join?", "To build useful products",
        "Review claims", "Check the form",
    ):
        assert expected in text
    assert "Version:" not in text
    assert draft["package_hash"][:12] not in text
    assert "Full job description filler." not in text


def _resume_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 760, "Alex Example - English CV")
    pdf.save()


def test_review_packet_sends_summary_before_pdf_with_actions(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "123"})
    pdf = tmp_path / "resume.pdf"
    _resume_pdf(pdf)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/getChat"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": "private"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(seen)}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await send_review_packet(settings, _draft(pdf), [], client=client)

    message_id = asyncio.run(run())
    paths = [request.url.path for request in seen]
    summary_index = next(index for index, path in enumerate(paths) if path.endswith("/sendMessage"))
    document_index = next(index for index, path in enumerate(paths) if path.endswith("/sendDocument"))
    assert summary_index < document_index
    assert message_id == summary_index + 1
    summary = seen[summary_index]
    payload = json.loads(summary.content)
    assert "AI Engineer at Example" in payload["text"]
    assert "Match: 80/100" in payload["text"]
    assert "Action: Email -> jobs@example.org" in payload["text"]
    assert "Ready to send" in payload["text"]
    assert "Version:" not in payload["text"]
    assert ("b" * 12) not in payload["text"]
    assert "Review focus:" in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"]
    assert [button["text"] for row in buttons for button in row] == ["Approve & send", "Edit", "Regenerate"]
    assert buttons[1][0]["callback_data"] == f"review:edit:{'a' * 32}:{'b' * 12}"
    assert all(len(button["callback_data"].encode()) <= 64 for row in buttons for button in row if "callback_data" in button)
    document = seen[document_index]
    assert b"Review packet" in document.content
    assert b"version" not in document.content.lower()
    assert b"reply_parameters" in document.content
    review = PdfReader(str(build_review_pdf(settings, _draft(pdf), [])))
    full_text = "\n".join(page.extract_text() for page in review.pages)
    for expected in ("QUICK REVIEW", "Python", "Search Platform", "Dear team", "Why join?", "Alex Example - English CV"):
        assert expected in full_text
    assert "Full job description filler." not in full_text
    assert ("b" * 12) not in full_text



def test_updated_review_uses_human_indicator_but_keeps_hash_only_in_callbacks(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "123"})
    pdf = tmp_path / "resume.pdf"
    _resume_pdf(pdf)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/getChat"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": "private"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(seen)}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await send_review_packet(settings, _draft(pdf), [], client=client, updated=True)

    asyncio.run(run())
    summary = next(request for request in seen if request.url.path.endswith("/sendMessage"))
    payload = json.loads(summary.content)
    assert payload["text"].startswith("Updated review · AI Engineer at Example")
    assert ("b" * 12) not in payload["text"]
    callback_data = [
        button["callback_data"]
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
        if "callback_data" in button
    ]
    assert any(("b" * 12) in value for value in callback_data)


def test_not_ready_application_still_sends_review_without_approve_button(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "123"})
    pdf = tmp_path / "resume.pdf"
    _resume_pdf(pdf)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/getChat"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": "private"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(seen)}})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await send_review_packet(settings, _draft(pdf), ["Choose an application destination"], client=client)

    asyncio.run(run())
    summary = next(request for request in seen if request.url.path.endswith("/sendMessage"))
    payload = json.loads(summary.content)
    assert "Needs changes before sending" in payload["text"]
    assert [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row] == ["Edit", "Regenerate"]
    assert any(request.url.path.endswith("/sendDocument") for request in seen)


def test_preparation_failure_notice_has_no_send_action_or_resume(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "123"})
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/getChat"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 123, "type": "private"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async def run() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await send_preparation_notice(settings, "AI Engineer", "Example", client)

    assert asyncio.run(run()) == 42
    assert [request.url.path.rsplit("/", 1)[-1] for request in seen] == ["getChat", "sendMessage"]
    message = json.loads(seen[-1].content)
    assert "AI Engineer at Example" in message["text"]
    assert "No draft or resume was created" in message["text"]
    assert "reply_markup" not in message


def test_review_packet_refuses_group_chat_before_sending_resume(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "-100123"})
    pdf = tmp_path / "resume.pdf"
    _resume_pdf(pdf)
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"id": -100123, "type": "supergroup"}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await send_review_packet(settings, _draft(pdf), [], client=client)
    try:
        asyncio.run(run())
    except ValueError as error:
        assert "private" in str(error)
    else:
        assert False, "A group must not receive private application details"
    assert not any(path.endswith("/sendDocument") or path.endswith("/sendMessage") for path in seen)


def test_telegram_approval_accepts_current_private_chat_review_only(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    save_telegram(app.state.settings, {"token": "test-token", "chat_id": "123"})
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
                            "experience": [{"role": "Engineer", "company": "Prior Co", "dates": "2024–2026", "bullets": ["Built Python"]}]})
    job = app.state.db.one("SELECT id FROM vacancies LIMIT 1")
    if not job:
        from job_radar.db import new_id, now
        job_id = new_id()
        db.execute("INSERT INTO vacancies(id,company,title,description,apply_url,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (job_id, "Example", "AI Engineer", "Build Python systems.", "mailto:jobs@example.org", now(), now(), now(), now()))
    else:
        job_id = job["id"]
    draft = prepare_draft(db, app.state.settings, job_id, "template")
    db.execute("INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,review_hash,created_at,updated_at) VALUES(?,'awaiting_review',?,?,?,?)",
               (job_id, draft["id"], draft["package_hash"], "2026-01-01", "2026-01-01"))
    save_calls = []
    monkeypatch.setattr("job_radar.apply._send_email", lambda item, _settings: save_calls.append(item["id"]) or "accepted")
    from job_radar.mail_config import save_smtp
    save_smtp(app.state.settings, {"host": "smtp.example.org", "port": 587, "from": "alex@example.org"})
    requests = []
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    callback = lambda chat, version: {"callback_query": {"id": "callback-1", "from": {"id": chat},
        "message": {"chat": {"id": chat, "type": "private"}},
        "data": f"review:approve:{draft['id']}:{version}"}}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await app.state.auto_apply_manager.handle_telegram_update(callback(999, draft["package_hash"][:12]), client)
            await app.state.auto_apply_manager.handle_telegram_update(callback(123, "0" * 12), client)
            assert not save_calls
            await app.state.auto_apply_manager.handle_telegram_update(callback(123, draft["package_hash"][:12]), client)
    asyncio.run(run())
    assert len(save_calls) == 1
    assert db.one("SELECT status FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))["status"] == "sent"
    assert any(request.url.path.endswith("/answerCallbackQuery") for request in requests)


def test_review_notification_tracks_delivered_version(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    save_telegram(app.state.settings, {"token": "test-token", "chat_id": "123"})
    from job_radar.db import new_id, now
    job_id = new_id()
    db.execute("INSERT INTO vacancies(id,company,title,description,apply_url,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (job_id, "Example", "AI Engineer", "Build Python systems.", "mailto:jobs@example.org", now(), now(), now(), now()))
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
                            "experience": [{"role": "Engineer", "company": "Prior Co", "dates": "2024–2026", "bullets": ["Built Python"]}]})
    draft = prepare_draft(db, app.state.settings, job_id, "template")
    db.execute("INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,created_at,updated_at) VALUES(?,'awaiting_review',?,?,?)",
               (job_id, draft["id"], now(), now()))
    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", lambda *_args, **_kwargs: asyncio.sleep(0, result=42))
    asyncio.run(app.state.auto_apply_manager.notify_review(draft["id"]))
    attempt = db.one("SELECT review_hash,telegram_status,telegram_message_id FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))
    assert attempt == {"review_hash": draft["package_hash"], "telegram_status": "sent", "telegram_message_id": 42}


@pytest.mark.parametrize("action,instructions", [
    ("retry", "Focus on production search"),
    ("edit", "Correct my phone number to 555-1234"),
])
def test_telegram_reply_edits_or_regenerates_draft(tmp_path: Path, monkeypatch, action: str, instructions: str) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    save_telegram(app.state.settings, {"token": "test-token", "chat_id": "123"})
    from job_radar.mail_config import save_smtp
    save_smtp(app.state.settings, {"host": "smtp.example.org", "port": 587, "from": "alex@example.org"})
    from job_radar.db import new_id, now
    job_id = new_id()
    db.execute("INSERT INTO vacancies(id,company,title,description,apply_url,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (job_id, "Example", "AI Engineer", "Build Python systems.", "mailto:jobs@example.org", now(), now(), now(), now()))
    db.set_setting("profile", {"name": "Alex Example", "email": "alex@example.org",
                            "experience": [{"role": "Engineer", "company": "Prior Co", "dates": "2024–2026", "bullets": ["Built Python"]}]})
    prompts = []
    def fake_provider(_provider, _job, _profile, _cards, custom_prompt=""):
        prompts.append(custom_prompt)
        return ModelDraft(summary="Python engineer", email_subject="AI Engineer application",
                          email_body=f"Dear team. {custom_prompt or 'Initial'}")
    monkeypatch.setattr("job_radar.drafting._run_provider", fake_provider)
    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", lambda *_args, **_kwargs: asyncio.sleep(0, result=88))
    draft = prepare_draft(db, app.state.settings, job_id, "codex")
    db.execute("INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,review_hash,created_at,updated_at) VALUES(?,'awaiting_review',?,?,?,?)",
               (job_id, draft["id"], draft["package_hash"], now(), now()))
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await app.state.auto_apply_manager.handle_telegram_update({"callback_query": {
                "id": "review-1", "from": {"id": 123}, "message": {"chat": {"id": 123, "type": "private"}},
                "data": f"review:{action}:{draft['id']}:{draft['package_hash'][:12]}"}}, client)
            await app.state.auto_apply_manager.handle_telegram_update({"message": {
                "message_id": 43, "chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                "reply_to_message": {"message_id": 42}, "text": instructions}}, client)
    asyncio.run(run())
    if action == "edit":
        assert prompts[-1].startswith("Make only these requested corrections")
        assert prompts[-1].endswith(instructions)
    else:
        assert prompts[-1] == instructions
    assert get_draft(db, draft["id"])["package_hash"] != draft["package_hash"]
    assert db.one("SELECT status FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))["status"] == "awaiting_review"
