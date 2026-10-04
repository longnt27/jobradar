import asyncio
import json
import stat
import time
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import Database, now
from job_radar.collectors import AuthRequired
from job_radar.feed_catalog import CAREER_FEEDS
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
    alert = client.post("/api/setup/telegram", json={"token": "1234567890:secret", "chat_id": "42", "min_score": 72})
    assert alert.status_code == 200
    status = client.get("/api/setup").json()
    assert status["smtp_configured"] and status["telegram_configured"]
    assert status["telegram_min_score"] == 72
    assert "private-password" not in json.dumps(status)
    assert "1234567890:secret" not in json.dumps(status)


def test_gmail_smtp_requires_its_own_app_password(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    generic = {"host": "mail.example.org", "port": 587, "user": "alex", "password": "old-secret", "from_address": "alex@example.org"}
    assert client.post("/api/setup/smtp", json=generic).status_code == 200
    gmail = {"host": "smtp.gmail.com", "port": 465, "user": "alex@gmail.com", "password": "", "from_address": "alex@gmail.com"}
    rejected = client.post("/api/setup/smtp", json=gmail)
    assert rejected.status_code == 422
    assert "app password" in rejected.json()["detail"].lower()
    assert json.loads((tmp_path / "smtp.json").read_text())["host"] == "mail.example.org"
    gmail["password"] = "gmail-app-password"
    assert client.post("/api/setup/smtp", json=gmail).status_code == 200
    gmail["password"] = ""
    assert client.post("/api/setup/smtp", json=gmail).status_code == 200
    assert client.get("/api/setup").json()["smtp_configured"] is True


def test_telegram_first_time_blank_token_returns_validation_error(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    response = client.post("/api/setup/telegram", json={"token": "", "chat_id": "42"})
    assert response.status_code == 422
    assert "token" in response.json()["detail"].lower()
    assert not (tmp_path / "telegram.json").exists()


def test_changing_telegram_bot_resets_review_polling(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    assert client.post("/api/setup/telegram", json={"token": "old-token", "chat_id": "42"}).status_code == 200
    app.state.db.set_setting("telegram_review_offset", 900)
    assert client.post("/api/setup/telegram", json={"token": "new-token", "chat_id": "43"}).status_code == 200
    assert app.state.db.get_setting("telegram_review_offset") == 0


def test_telegram_chat_lookup_uses_token_without_exposing_it(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    seen = []
    async def lookup(token):
        seen.append(token)
        return [{"id": "42", "name": "My chat"}]
    monkeypatch.setattr("job_radar.web.discover_telegram_chats", lookup)
    response = client.post("/api/setup/telegram/chats", json={"token": "secret"})
    assert response.status_code == 200
    assert response.json() == {"chats": [{"id": "42", "name": "My chat"}]}
    assert seen == ["secret"]
    assert "secret" not in response.text


def test_social_scans_wait_for_browser_setup(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    seed(db)

    async def run():
        manager = ScanManager(db, settings)
        assert manager.queue_due() == len(CAREER_FEEDS)
        await manager.stop()
        db.set_setting("social_login_completed_at_linkedin", now())
        assert manager.queue_due() == len(CAREER_FEEDS) + 27
        await manager.stop()

    asyncio.run(run())


def test_browser_setup_flow_detects_login_without_finish_click(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    class FakeProcess:
        pid = 12345
        closed = False
        def poll(self):
            return 0 if self.closed else None
        def terminate(self):
            self.closed = True
        def wait(self, timeout=None):
            return 0

    process = FakeProcess()
    monkeypatch.setattr("job_radar.browser_login.chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr("job_radar.browser_login.subprocess.Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr("job_radar.browser_login.chrome_login_complete", lambda _profile, site, _started: site == "linkedin")
    monkeypatch.setattr("job_radar.browser_login.frontmost_app_bundle", lambda: "com.openai.codex")
    monkeypatch.setattr("job_radar.browser_login.return_to_job_radar", lambda *_args: None)
    with TestClient(app) as client:
        assert client.post("/api/setup/browser/start", json={"site": "linkedin"}).status_code == 200
        for _ in range(100):
            if client.get("/api/setup").json()["browser"]["state"] == "saved":
                break
            time.sleep(.01)
        assert process.closed
        assert client.get("/api/setup").json()["browser"]["connected_sites"] == ["linkedin"]


def test_expired_social_session_is_persisted_and_only_that_site_pauses(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("social_login_completed_at_linkedin", now())
    db.set_setting("social_login_completed_at_facebook", now())
    linkedin = db.one("SELECT id FROM sources WHERE kind='linkedin' LIMIT 1")["id"]
    added = TestClient(app).post("/api/sources", json={"kind": "facebook", "name": "Test AI group",
        "url": "https://www.facebook.com/groups/12345"})
    assert added.status_code == 201
    alerts = []
    monkeypatch.setattr("job_radar.scanner.notify_social_sign_in_required", lambda site: alerts.append(site), raising=False)

    async def expired(_settings, _source):
        raise AuthRequired("Login or verification is required in the browser profile")

    monkeypatch.setattr("job_radar.scanner.collect_source", expired)

    async def run():
        result = await app.state.scan_manager.run_source(linkedin)
        assert result["status"] == "auth_required"
        assert app.state.scan_manager.queue_due() == len(CAREER_FEEDS) + 1
        await app.state.scan_manager.stop()

    asyncio.run(run())
    browser = TestClient(app).get("/api/setup").json()["browser"]
    assert browser["state"] == "reauth_required"
    assert browser["sites"] == ["linkedin"]
    assert browser["last_saved_at"]
    assert alerts == ["linkedin"]
    blocked = asyncio.run(app.state.scan_manager.run_source(linkedin))
    assert blocked["status"] == "auth_required"
    assert alerts == ["linkedin"]


def test_signing_in_again_uses_regular_chrome_for_one_site_and_clears_expiry(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("social_login_completed_at_linkedin", now())
    db.set_setting("social_reauth_required_linkedin", {"detected_at": now()})
    launches = []

    class FakeProcess:
        pid = 12345
        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("job_radar.browser_login.chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr("job_radar.browser_login.subprocess.Popen", lambda args, **kwargs: launches.append(args) or FakeProcess())
    monkeypatch.setattr("job_radar.browser_login.chrome_login_complete", lambda _profile, _site, _started: True)
    monkeypatch.setattr("job_radar.browser_login.frontmost_app_bundle", lambda: "com.openai.codex")
    monkeypatch.setattr("job_radar.browser_login.return_to_job_radar", lambda *_args: None)
    manager = app.state.login_manager

    async def run():
        assert manager.status()["state"] == "reauth_required"
        manager.start("linkedin")
        await manager.task
        return manager.status()

    saved = asyncio.run(run())
    assert saved["state"] == "saved"
    assert saved["sites"] == []
    assert saved["connected_sites"] == ["linkedin"]
    assert len(launches) == 1
    assert f"--user-data-dir={Settings(tmp_path).browser_profile}" in launches[0]
    assert "https://www.linkedin.com/feed/" in launches[0]
    assert not any("remote-debugging" in arg for arg in launches[0])
    assert TestClient(create_app(Settings(tmp_path))).get("/api/setup").json()["browser"]["connected_sites"] == ["linkedin"]

    async def connect_facebook():
        manager.start("facebook")
        await manager.task
        return manager.status()

    both = asyncio.run(connect_facebook())
    assert both["connected_sites"] == ["linkedin", "facebook"]
    assert "https://www.facebook.com/settings" in launches[1]


def test_auto_detect_closes_chrome_and_returns_to_app(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    launches = []
    activated = []

    class FakeProcess:
        pid = 67890
        closed = False

        def poll(self):
            return 0 if self.closed else None

        def terminate(self):
            self.closed = True

        def wait(self, timeout=None):
            return 0

    process = FakeProcess()
    monkeypatch.setattr("job_radar.browser_login.chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr("job_radar.browser_login.subprocess.Popen", lambda args, **kwargs: launches.append(args) or process)
    monkeypatch.setattr("job_radar.browser_login.chrome_login_complete", lambda _profile, site, _started: site == "linkedin")
    monkeypatch.setattr("job_radar.browser_login.frontmost_app_bundle", lambda: "com.openai.codex")
    monkeypatch.setattr("job_radar.browser_login.return_to_job_radar", lambda bundle, port: activated.append((bundle, port)))

    with TestClient(app) as client:
        started = client.post("/api/setup/browser/start", json={"site": "linkedin"})
        assert started.status_code == 200
        for _ in range(100):
            if client.get("/api/setup").json()["browser"]["state"] == "saved":
                break
            time.sleep(.01)
        assert process.closed
        assert not any("/signin/finish/" in arg for arg in launches[0])
        assert client.get("/api/setup").json()["browser"]["connected_sites"] == ["linkedin"]
        assert activated == [("com.openai.codex", 8787)]
