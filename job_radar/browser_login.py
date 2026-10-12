from __future__ import annotations

import asyncio
import subprocess
import socket
import time
from collections.abc import Callable

from .chrome_auth_watch import chrome_login_complete
from .db import Database, now
from .desktop_handoff import frontmost_app_bundle, return_to_job_radar
from .settings import Settings
from .social_browser import SITES, chrome_executable, social_login_at


LOGIN_URLS = {"linkedin": "https://www.linkedin.com/feed/", "facebook": "https://www.facebook.com/settings", "zalo": "https://chat.zalo.me/"}


class BrowserLoginManager:
    def __init__(self, db: Database, settings: Settings, browser_lock: asyncio.Lock,
                 on_complete: Callable[[], None] | None = None):
        self.db = db
        self.settings = settings
        self.browser_lock = browser_lock
        self.state = "idle"
        self.error: str | None = None
        self.site: str | None = None
        self.task: asyncio.Task | None = None
        self.process: subprocess.Popen | None = None
        self.return_app: str | None = None
        self.on_complete = on_complete
        self.zalo_cdp_port: int | None = None

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
            raise ValueError("Choose LinkedIn, Facebook, or Zalo")
        if self.task and not self.task.done():
            if site != self.site:
                raise ValueError(f"Complete {self.site.capitalize()} sign-in before opening {site.capitalize()}")
            return self.status()
        chrome_executable()
        self.error = None
        self.site = site
        self.return_app = frontmost_app_bundle()
        self.state = "opening"
        self.task = asyncio.create_task(self._run())
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
                started_at = time.time()
                arguments = [chrome_executable(), f"--user-data-dir={self.settings.browser_profile}",
                     "--profile-directory=Default", "--no-first-run", "--no-default-browser-check",
                     "--new-window", LOGIN_URLS[self.site]]
                if self.site == "zalo":
                    with socket.socket() as sock:
                        sock.bind(("127.0.0.1", 0))
                        self.zalo_cdp_port = sock.getsockname()[1]
                    arguments.extend([f"--remote-debugging-port={self.zalo_cdp_port}", "--remote-debugging-address=127.0.0.1"])
                self.process = subprocess.Popen(
                    arguments,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.state = "open"
                while self.process.poll() is None:
                    try:
                        ready = (await self._zalo_login_ready() if self.site == "zalo" else
                                 await asyncio.to_thread(chrome_login_complete, self.settings.browser_profile,
                                                        self.site, started_at))
                        self.error = None
                        if ready:
                            break
                    except Exception:
                        self.error = "Job Radar could not check Chrome's sign-in status. Try again shortly."
                    await asyncio.sleep(1)
                else:
                    self.state = "failed"
                    self.error = "Chrome closed before sign-in completed. Open it again to continue."
                    return
                await self._close_process()
                self.db.set_setting(f"social_login_completed_at_{self.site}", now())
                self.db.set_setting(f"social_reauth_required_{self.site}", None)
                if self.site == "linkedin":
                    self.db.set_setting("linkedin_automation_paused", False)
                if self.on_complete:
                    try:
                        self.on_complete()
                    except Exception:
                        pass
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

    async def _zalo_login_ready(self) -> bool:
        from playwright.async_api import async_playwright
        from .zalo_browser import zalo_authenticated
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.zalo_cdp_port}")
                for context in browser.contexts:
                    for page in context.pages:
                        if await zalo_authenticated(page):
                            return True
        except Exception:
            return False
        return False
