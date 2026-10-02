from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from .db import Database, now
from .settings import Settings


class BrowserLoginManager:
    def __init__(self, db: Database, settings: Settings, browser_lock: asyncio.Lock):
        self.db = db
        self.settings = settings
        self.browser_lock = browser_lock
        self.state = "saved" if db.get_setting("browser_login_completed_at") else "idle"
        self.error: str | None = None
        self.task: asyncio.Task | None = None
        self.finished = asyncio.Event()

    def status(self) -> dict:
        return {"state": self.state, "error": self.error,
                "last_saved_at": self.db.get_setting("browser_login_completed_at")}

    def start(self) -> dict:
        if self.task and not self.task.done():
            return self.status()
        self.finished = asyncio.Event()
        self.error = None
        self.state = "opening"
        self.task = asyncio.create_task(self._run())
        return self.status()

    async def finish(self) -> dict:
        if self.state != "open" or not self.task:
            raise ValueError("Open the sign-in browser first")
        self.finished.set()
        await self.task
        return self.status()

    async def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        try:
            async with self.browser_lock:
                async with async_playwright() as playwright:
                    context = await playwright.chromium.launch_persistent_context(
                        str(self.settings.browser_profile), headless=False, viewport={"width": 1365, "height": 900})
                    try:
                        for url in ("https://www.linkedin.com/login", "https://www.facebook.com/"):
                            page = await context.new_page()
                            try:
                                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                            except Exception:
                                pass
                        self.state = "open"
                        await self.finished.wait()
                        self.db.set_setting("browser_login_completed_at", now())
                        self.state = "saved"
                    finally:
                        await context.close()
        except asyncio.CancelledError:
            self.state = "idle"
            raise
        except Exception as error:
            self.state = "failed"
            self.error = str(error)[:300]
