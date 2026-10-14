import asyncio
import subprocess
import threading

from fastapi.testclient import TestClient

from job_radar.browser_login import BrowserLoginManager
from job_radar.chatgpt_handoff import ChatGPTInputManager
from job_radar.db import Database, now
from job_radar.settings import Settings
from job_radar import social_browser
from job_radar.web import create_app


class RunningProcess:
    pid = 12345

    def __init__(self):
        self.finished = threading.Event()

    def poll(self):
        return 0 if self.finished.is_set() else None

    def wait(self, timeout=None):
        if not self.finished.wait(timeout):
            raise subprocess.TimeoutExpired("test Chrome", timeout)
        return 0

    def terminate(self):
        self.finished.set()

    def kill(self):
        self.finished.set()


def test_chatgpt_login_monitor_can_close_its_browser_without_cancelling_itself(tmp_path, monkeypatch):
    process = RunningProcess()
    monkeypatch.setattr("job_radar.chatgpt_handoff.chatgpt_logged_in", lambda _: True)
    monkeypatch.setattr("job_radar.chatgpt_handoff.frontmost_app_bundle", lambda: None)
    monkeypatch.setattr("job_radar.chatgpt_handoff.return_to_job_radar", lambda *args: None)

    async def run():
        lock = asyncio.Lock()
        await lock.acquire()
        manager = ChatGPTInputManager(Settings(tmp_path), lock)
        manager.owns_browser_lock = True
        manager.process = process
        manager.login_task = asyncio.create_task(manager._watch_login())
        result = await asyncio.gather(manager.login_task, return_exceptions=True)
        try:
            assert result == [None]
            assert manager.login_task is None or manager.login_task.cancelling() == 0
            assert manager.state == "saved"
            assert process.poll() == 0
            assert manager.process is None
            assert not lock.locked()
        finally:
            await manager.stop()

    asyncio.run(run())


def test_profile_process_probe_recognizes_only_chrome_using_the_exact_saved_profile(tmp_path, monkeypatch):
    profile = tmp_path / "browser-profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("host-12345")
    monkeypatch.setattr(social_browser, "chrome_executable", lambda: "/fake/Google Chrome")

    def probe(command):
        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs:
                            subprocess.CompletedProcess(args[0], 0, stdout=command))
        return social_browser.chrome_profile_process(profile)

    assert probe(f"/fake/Google Chrome --user-data-dir={profile}-other --profile-directory=Default") is None
    assert probe(f"/fake/Other Browser --user-data-dir={profile} --profile-directory=Default") is None
    owned = probe(f"/fake/Google Chrome --user-data-dir={profile} --profile-directory=Default "
                  "--remote-debugging-port=54020 --new-window https://chatgpt.com/?temporary-chat=true")
    assert owned is not None and owned.pid == 12345


def test_orphan_browser_cleanup_waits_for_profile_owner_to_exit(tmp_path, monkeypatch):
    process = RunningProcess()
    monkeypatch.setattr(social_browser, "chrome_profile_process", lambda _: process, raising=False)

    async def run():
        assert await social_browser.close_orphaned_chrome(tmp_path) is True
        assert process.poll() == 0

    asyncio.run(run())


def test_service_restart_closes_browser_left_by_previous_service(tmp_path, monkeypatch):
    process = RunningProcess()
    monkeypatch.setattr(social_browser, "chrome_profile_process", lambda _: process)
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    with TestClient(app):
        assert process.poll() == 0


def test_chatgpt_cleanup_releases_profile_even_when_driver_shutdown_fails(tmp_path):
    class BrokenDriver:
        async def stop(self):
            raise RuntimeError("driver disconnected during cleanup")

    async def run():
        lock = asyncio.Lock()
        await lock.acquire()
        manager = ChatGPTInputManager(Settings(tmp_path), lock)
        process = RunningProcess()
        manager.process = process
        manager.playwright = BrokenDriver()
        manager.owns_browser_lock = True
        try:
            try:
                await manager.stop()
            except RuntimeError:
                pass
            assert process.poll() == 0
            assert manager.process is None
            assert not lock.locked()
        finally:
            manager.playwright = None
            await manager.stop()

    asyncio.run(run())


def test_cancelling_chatgpt_launch_closes_chrome_and_releases_profile(tmp_path, monkeypatch):
    process = RunningProcess()
    launched = asyncio.Event()

    def launch(*args, **kwargs):
        launched.set()
        return process

    monkeypatch.setattr("job_radar.chatgpt_handoff.chrome_executable", lambda: "/fake/chrome")
    monkeypatch.setattr("job_radar.chatgpt_handoff.subprocess.Popen", launch)

    async def run():
        lock = asyncio.Lock()
        manager = ChatGPTInputManager(Settings(tmp_path), lock)
        task = asyncio.create_task(manager._start_browser())
        await launched.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            assert process.poll() == 0
            assert not lock.locked()
            assert manager.process is None
        finally:
            await manager.stop()

    asyncio.run(run())


def test_chatgpt_process_watcher_keeps_lock_until_profile_cleanup_finishes(tmp_path, monkeypatch):
    started = threading.Event()
    release_watcher = threading.Event()

    class WatchedProcess(RunningProcess):
        def wait(self, timeout=None):
            if not started.is_set():
                started.set()
                release_watcher.wait()
            return super().wait(timeout)

    async def run():
        lock = asyncio.Lock()
        await lock.acquire()
        manager = ChatGPTInputManager(Settings(tmp_path), lock)
        manager.owns_browser_lock = True
        manager.process = WatchedProcess()
        manager.watch_task = asyncio.create_task(manager._watch_process(manager.process))
        await asyncio.to_thread(started.wait)

        async def cleanup(_profile):
            release_watcher.set()
            await manager.watch_task
            assert manager.owns_browser_lock
            assert lock.locked()

        monkeypatch.setattr("job_radar.chatgpt_handoff.close_orphaned_chrome", cleanup)
        try:
            await manager.stop()
            assert not lock.locked()
        finally:
            release_watcher.set()

    asyncio.run(run())


def test_social_sign_in_checks_completion_after_chrome_launcher_exits(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    db = Database(settings.database_path)
    db.set_setting("social_reauth_required_facebook", {"detected_at": now()})
    process = RunningProcess()
    process.terminate()
    monkeypatch.setattr("job_radar.browser_login.chrome_executable", lambda: "/fake/chrome")
    monkeypatch.setattr("job_radar.browser_login.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("job_radar.browser_login.chrome_login_complete", lambda *args: True)
    monkeypatch.setattr("job_radar.browser_login.return_to_job_radar", lambda *args: None)

    async def run():
        manager = BrowserLoginManager(db, settings, asyncio.Lock())
        manager.site = "facebook"
        await manager._run()
        assert manager.status()["state"] == "saved"
        assert db.get_setting("social_login_completed_at_facebook")
        assert db.get_setting("social_reauth_required_facebook") is None

    asyncio.run(run())
