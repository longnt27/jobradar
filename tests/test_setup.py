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
