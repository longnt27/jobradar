from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

import httpx

from .db import Database
from .settings import Settings


def configure_telegram(settings: Settings) -> None:
    settings.ensure_dirs()
    token = getpass.getpass("Telegram bot token: ").strip()
    chat_id = input("Your Telegram chat ID: ").strip()
    save_telegram(settings, {"token": token, "chat_id": chat_id})
    print(f"Telegram settings saved to {settings.data_dir / 'telegram.json'}")


def save_telegram(settings: Settings, config: dict) -> None:
    if not config.get("token") or not config.get("chat_id"):
        raise ValueError("Bot token and chat ID are required")
    settings.ensure_dirs()
    path = settings.data_dir / "telegram.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(config, file)
    path.chmod(0o600)


def telegram_config(settings: Settings) -> dict:
    path = settings.data_dir / "telegram.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    if token := os.environ.get("JOB_RADAR_TELEGRAM_TOKEN"):
        config["token"] = token
    if chat_id := os.environ.get("JOB_RADAR_TELEGRAM_CHAT_ID"):
        config["chat_id"] = chat_id
    return config


async def notify_new_jobs(db: Database, settings: Settings, vacancy_ids: list[str]) -> int:
    config = telegram_config(settings)
    if not config.get("token") or not config.get("chat_id"):
        return 0
    profile = db.get_setting("profile", {})
    minimum = int(profile.get("alert_min_score", 60))
    sent = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for identifier in dict.fromkeys(vacancy_ids):
            job = db.one("SELECT title,company,location,score,apply_url FROM vacancies WHERE id=? AND excluded_location=0", (identifier,))
            if not job or (job["score"] or 0) < minimum:
                continue
            source = db.one("SELECT o.url FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id WHERE vo.vacancy_id=? ORDER BY o.first_seen_at LIMIT 1", (identifier,))
            url = job["apply_url"] or (source["url"] if source else "")
            message = f"Job Radar · {job['score']}/100\n{job['title']} · {job['company']}\n{job['location'] or 'Location unknown'}\n{url}"[:4000]
            try:
                response = await client.post(f"https://api.telegram.org/bot{config['token']}/sendMessage",
                                             json={"chat_id": config["chat_id"], "text": message, "disable_web_page_preview": True})
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise RuntimeError(f"Telegram alert failed ({type(error).__name__})") from None
            sent += 1
    return sent
