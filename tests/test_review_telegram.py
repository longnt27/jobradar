import asyncio
import json
from pathlib import Path

import httpx

from job_radar.review_telegram import format_review_details, send_review_packet
from job_radar.drafting import ModelDraft, get_draft, prepare_draft
from job_radar.web import create_app
from job_radar.settings import Settings
from job_radar.notifications import save_telegram


def _draft(pdf: Path) -> dict:
    return {"id": "a" * 32, "package_hash": "b" * 64, "job_title": "AI Engineer",
            "company": "Example", "resume_path": str(pdf), "job_description": "Build reliable search services.",
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


def test_review_details_include_every_application_section(tmp_path: Path) -> None:
    draft = _draft(tmp_path / "resume.pdf")
    text = format_review_details(draft, ["Check the form"])
    for expected in ("AI Engineer", "jobs@example.org", "Build reliable search services.", "Alex Example", "Prior Co", "Built search",
                     "Vision", "Trained vision models", "Example University", "Python", "Award",
                     "Application for AI Engineer", "Dear team", "Why join?", "To build useful products",
                     "Review claims", "Check the form"):
        assert expected in text


def test_review_packet_sends_full_text_pdf_and_actions(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "123"})
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\nexample")
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
    assert message_id == len(seen)
    assert any(request.url.path.endswith("/sendDocument") and b"%PDF-1.4" in request.content for request in seen)
    messages = [json.loads(request.content) for request in seen if request.url.path.endswith("/sendMessage")]
    assert "Trained vision models" in "\n".join(message["text"] for message in messages)
    buttons = messages[-1]["reply_markup"]["inline_keyboard"]
    assert [button["text"] for row in buttons for button in row] == ["Approve & send", "Edit", "Regenerate"]
    assert buttons[1][0]["url"] == f"http://127.0.0.1:8787/#applications/{'a' * 32}"
    assert all(len(button["callback_data"].encode()) <= 64 for row in buttons for button in row if "callback_data" in button)


def test_review_packet_refuses_group_chat_before_sending_resume(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    save_telegram(settings, {"token": "test-token", "chat_id": "-100123"})
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\nexample")
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
    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", lambda *_args: asyncio.sleep(0, result=42))
    asyncio.run(app.state.auto_apply_manager.notify_review(draft["id"]))
    attempt = db.one("SELECT review_hash,telegram_status,telegram_message_id FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))
    assert attempt == {"review_hash": draft["package_hash"], "telegram_status": "sent", "telegram_message_id": 42}


def test_telegram_retry_reply_regenerates_draft_with_custom_prompt(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr("job_radar.auto_apply.send_review_packet", lambda *_args: asyncio.sleep(0, result=88))
    draft = prepare_draft(db, app.state.settings, job_id, "codex")
    db.execute("INSERT INTO auto_application_attempts(vacancy_id,status,draft_id,review_hash,created_at,updated_at) VALUES(?,'awaiting_review',?,?,?,?)",
               (job_id, draft["id"], draft["package_hash"], now(), now()))
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await app.state.auto_apply_manager.handle_telegram_update({"callback_query": {
                "id": "retry-1", "from": {"id": 123}, "message": {"chat": {"id": 123, "type": "private"}},
                "data": f"review:retry:{draft['id']}:{draft['package_hash'][:12]}"}}, client)
            await app.state.auto_apply_manager.handle_telegram_update({"message": {
                "message_id": 43, "chat": {"id": 123, "type": "private"}, "from": {"id": 123},
                "reply_to_message": {"message_id": 42}, "text": "Focus on production search"}}, client)
    asyncio.run(run())
    assert prompts[-1] == "Focus on production search"
    assert get_draft(db, draft["id"])["package_hash"] != draft["package_hash"]
    assert db.one("SELECT status FROM auto_application_attempts WHERE vacancy_id=?", (job_id,))["status"] == "awaiting_review"
