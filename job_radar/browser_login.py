from __future__ import annotations

import asyncio
import secrets
import subprocess

from .db import Database, now
from .desktop_handoff import frontmost_app_bundle, return_to_job_radar
from .settings import Settings
from .social_browser import SITES, chrome_executable, social_login_at


LOGIN_URLS = {"linkedin": "https://www.linkedin.com/login", "facebook": "https://www.facebook.com/"}


class BrowserLoginManager:
    def __init__(self, db: Database, settings: Settings, browser_lock: asyncio.Lock):
        self.db = db
        self.settings = settings
        self.browser_lock = browser_lock
        self.state = "idle"
        self.error: str | None = None
        self.site: str | None = None
        self.task: asyncio.Task | None = None
        self.process: subprocess.Popen | None = None
        self.finished = asyncio.Event()
        self.token: str | None = None
        self.return_app: str | None = None
        self.finish_scheduled = False

    def status(self) -> dict:
        expired = [site for site in SITES if self.db.get_setting(f"social_reauth_required_{site}")]
        connected = [site for site in SITES if social_login_at(self.db, site)]
        dates = [social_login_at(self.db, site) for site in SITES]
        state = self.state if self.state in ("opening", "open", "failed") else (
            "reauth_required" if expired else "saved" if connected else "idle")
        return {"state": state, "sites": expired, "connected_sites": connected,
                "active_site": self.site if self.state in ("opening", "open") else None,
                "error": self.error, "last_saved_at": max((date for date in dates if date), default=None)}

    def start(self, site: str) -> dict:
        if site not in SITES:
            raise ValueError("Choose LinkedIn or Facebook")
        if self.task and not self.task.done():
            if site != self.site:
                raise ValueError(f"Finish {self.site.capitalize()} sign-in before opening {site.capitalize()}")
            return self.status()
        chrome_executable()
        self.finished = asyncio.Event()
        self.error = None
        self.site = site
        self.token = secrets.token_urlsafe(24)
        self.return_app = frontmost_app_bundle()
        self.finish_scheduled = False
        self.state = "opening"
        self.task = asyncio.create_task(self._run())
        return self.status()

    async def finish(self, token: str | None = None) -> dict:
        if token is not None and token != self.token:
            raise ValueError("This sign-in window is no longer active")
        if self.state != "open" or not self.task:
            raise ValueError("Open the sign-in browser first")
        self.finished.set()
        await self.task
        if self.state == "failed":
            raise ValueError(self.error or "Sign-in browser failed")
        return self.status()

    async def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _close_process(self) -> None:
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
        self.process = None

    async def _run(self) -> None:
        try:
            async with self.browser_lock:
                self.settings.ensure_dirs()
                finish_url = f"http://127.0.0.1:{self.settings.port}/signin/finish/{self.token}"
                self.process = subprocess.Popen(
                    [chrome_executable(), f"--user-data-dir={self.settings.browser_profile}",
                     "--no-first-run", "--no-default-browser-check", "--new-window", finish_url, LOGIN_URLS[self.site]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.state = "open"
                while not self.finished.is_set() and self.process.poll() is None:
                    await asyncio.sleep(0.25)
                if not self.finished.is_set():
                    self.state = "failed"
                    self.error = "Chrome closed before sign-in was saved. Open it again and click finished after signing in."
                    return
                await self._close_process()
                self.db.set_setting(f"social_login_completed_at_{self.site}", now())
                self.db.set_setting(f"social_reauth_required_{self.site}", None)
                try:
                    await asyncio.to_thread(return_to_job_radar, self.return_app, self.settings.port)
                except Exception:
                    pass
                self.state = "saved"
        except asyncio.CancelledError:
            await self._close_process()
            self.state = "idle"
            raise
        except Exception as error:
            await self._close_process()
            self.state = "failed"
            self.error = str(error)[:300]
