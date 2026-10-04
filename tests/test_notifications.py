import asyncio
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from job_radar.notifications import notify_new_jobs
from job_radar.settings import Settings
from job_radar.web import create_app


def test_new_high_scoring_jobs_only_are_alerted(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    identifier = client.post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
        "description": "Private full job text should stay local.", "apply_url": "https://example.org/apply"}).json()["id"]
    app.state.db.execute("UPDATE vacancies SET score=80 WHERE id=?", (identifier,))
    (tmp_path / "telegram.json").write_text(json.dumps({"token": "test", "chat_id": "42"}))
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, json):
            calls.append((url, json))
            return type("Response", (), {"raise_for_status": lambda self: None})()

    monkeypatch.setattr("job_radar.notifications.httpx.AsyncClient", lambda **_kwargs: Client())
    assert asyncio.run(notify_new_jobs(app.state.db, settings, [identifier, identifier])) == 1
    assert len(calls) == 1
    assert "Private full job text" not in calls[0][1]["text"]


def test_failed_telegram_alert_is_persisted_and_retried(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    identifier = client.post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
        "description": "Build Python systems.", "apply_url": "https://example.org/apply"}).json()["id"]
    app.state.db.execute("UPDATE vacancies SET score=80 WHERE id=?", (identifier,))
    (tmp_path / "telegram.json").write_text(json.dumps({"token": "test", "chat_id": "42"}))
    failures = [True, False]
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, json):
            calls.append(url)
            if failures.pop(0):
                raise httpx.ConnectError("temporary", request=httpx.Request("POST", url))
            return type("Response", (), {"raise_for_status": lambda self: None})()

    monkeypatch.setattr("job_radar.notifications.httpx.AsyncClient", lambda **_kwargs: Client())
    assert asyncio.run(notify_new_jobs(app.state.db, settings, [identifier])) == 0
    assert app.state.db.one("SELECT status,attempts FROM notification_attempts WHERE vacancy_id=?", (identifier,)) == {"status": "pending", "attempts": 1}
    assert asyncio.run(notify_new_jobs(app.state.db, settings, [])) == 1
    assert app.state.db.one("SELECT status,attempts FROM notification_attempts WHERE vacancy_id=?", (identifier,)) == {"status": "sent", "attempts": 2}
    assert asyncio.run(notify_new_jobs(app.state.db, settings, [identifier])) == 0
    assert len(calls) == 2


def test_pending_alert_is_skipped_if_score_drops_below_current_minimum(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(tmp_path)
    app = create_app(settings)
    db = app.state.db
    identifier = TestClient(app).post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
        "description": "Build Python systems."}).json()["id"]
    db.execute("UPDATE vacancies SET score=90 WHERE id=?", (identifier,))
    profile = db.get_setting("profile", {})
    profile["alert_min_score"] = 86
    db.set_setting("profile", profile)
    (tmp_path / "telegram.json").write_text(json.dumps({"token": "test", "chat_id": "42"}))
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, json):
            calls.append(url)
            raise httpx.ConnectError("temporary", request=httpx.Request("POST", url))

    monkeypatch.setattr("job_radar.notifications.httpx.AsyncClient", lambda **_kwargs: Client())
    assert asyncio.run(notify_new_jobs(db, settings, [identifier])) == 0
    db.execute("UPDATE vacancies SET score=68 WHERE id=?", (identifier,))
    assert asyncio.run(notify_new_jobs(db, settings, [])) == 0
    assert db.one("SELECT status FROM notification_attempts WHERE vacancy_id=?", (identifier,))["status"] == "skipped"
    assert len(calls) == 1
