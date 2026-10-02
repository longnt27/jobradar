import asyncio
import json
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import Database, now
from job_radar.scanner import ScanManager
from job_radar.seeds import seed
from job_radar.settings import Settings
from job_radar.web import create_app


def test_setup_saves_secrets_without_returning_them(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    initial = client.get("/api/setup").json()
    assert initial["profile_complete"] is False
    assert initial["linkedin_searches"] == 27
    smtp = client.post("/api/setup/smtp", json={"host": "mail.example.org", "port": 587,
        "user": "alex", "password": "private-password", "from_address": "alex@example.org"})
    assert smtp.status_code == 200
    assert "private-password" not in smtp.text
    assert stat.S_IMODE((tmp_path / "smtp.json").stat().st_mode) == 0o600
    client.post("/api/setup/smtp", json={"host": "mail.example.org", "port": 587,
        "user": "alex", "password": "", "from_address": "new@example.org"})
    assert json.loads((tmp_path / "smtp.json").read_text())["password"] == "private-password"
    alert = client.post("/api/setup/telegram", json={"token": "1234567890:secret", "chat_id": "42"})
    assert alert.status_code == 200
    status = client.get("/api/setup").json()
    assert status["smtp_configured"] and status["telegram_configured"]
    assert "private-password" not in json.dumps(status)
    assert "1234567890:secret" not in json.dumps(status)


def test_social_scans_wait_for_browser_setup(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    seed(db)

    async def run():
        manager = ScanManager(db, settings)
        assert manager.queue_due() == 1  # VinAI careers only
        await manager.stop()
        db.set_setting("browser_login_completed_at", now())
        assert manager.queue_due() == 28  # Career page plus 27 LinkedIn searches
        await manager.stop()

    asyncio.run(run())


def test_browser_setup_flow_can_finish_from_ui(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    manager = app.state.login_manager

    async def fake_browser():
        manager.state = "open"
        await manager.finished.wait()
        manager.db.set_setting("browser_login_completed_at", now())
        manager.state = "saved"

    monkeypatch.setattr(manager, "_run", fake_browser)
    with TestClient(app) as client:
        assert client.post("/api/setup/browser/start").status_code == 200
        for _ in range(5):
            if client.get("/api/setup").json()["browser"]["state"] == "open":
                break
        assert client.post("/api/setup/browser/finish").json()["state"] == "saved"
        assert client.get("/api/setup").json()["browser"]["last_saved_at"]
