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
