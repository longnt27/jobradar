from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .settings import Settings


DEFAULT_TELEGRAM_MODES = {
    "application_reviews": True,
    "strong_job_alerts": False,
    "daily_digest": False,
}


def notify_social_sign_in_required(site: str) -> None:
    """Show one local alert when a saved social session first needs renewal."""
    if sys.platform != "darwin":
        return
    label = {"linkedin": "LinkedIn", "facebook": "Facebook"}.get(site)
    if not label:
        return
    subprocess.run(
        ["osascript", "-e", f'display notification "{label} sign-in expired. Open Job Radar to sign in again." with title "Job Radar"'],
        capture_output=True, timeout=5, check=False,
    )


def _time_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError:
        return ""
    return parsed.strftime("%H:%M")


def normalize_telegram_config(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    modes_raw = raw.get("modes") if isinstance(raw.get("modes"), dict) else {}
    modes = {
        key: bool(modes_raw.get(key, default))
        for key, default in DEFAULT_TELEGRAM_MODES.items()
    }
    digest_time = _time_value(raw.get("digest_time")) or "18:00"
    quiet_start = _time_value(raw.get("quiet_start"))
    quiet_end = _time_value(raw.get("quiet_end"))
    return {
        "token": str(raw.get("token") or ""),
        "chat_id": str(raw.get("chat_id") or ""),
        "modes": modes,
        "digest_time": digest_time,
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
    }


def telegram_mode_enabled(config: dict, mode: str) -> bool:
    return bool(normalize_telegram_config(config)["modes"].get(mode, False))


def telegram_quiet_now(config: dict, current: datetime | None = None) -> bool:
    normalized = normalize_telegram_config(config)
    start = normalized.get("quiet_start") or ""
    end = normalized.get("quiet_end") or ""
    if not start or not end or start == end:
        return False
    local = (current or datetime.now().astimezone()).astimezone()
    minute = local.hour * 60 + local.minute
    start_hour, start_minute = (int(part) for part in start.split(":"))
    end_hour, end_minute = (int(part) for part in end.split(":"))
    start_value = start_hour * 60 + start_minute
    end_value = end_hour * 60 + end_minute
    if start_value < end_value:
        return start_value <= minute < end_value
    return minute >= start_value or minute < end_value


def configure_telegram(settings: Settings) -> None:
    settings.ensure_dirs()
    token = getpass.getpass("Telegram bot token: ").strip()
    chat_id = input("Your Telegram chat ID: ").strip()
    save_telegram(settings, {"token": token, "chat_id": chat_id})
    print(f"Telegram settings saved to {settings.data_dir / 'telegram.json'}")


def save_telegram(settings: Settings, config: dict) -> None:
    normalized = normalize_telegram_config(config)
    if not normalized.get("token") or not normalized.get("chat_id"):
        raise ValueError("Bot token and chat ID are required")
    settings.ensure_dirs()
    path = settings.data_dir / "telegram.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(normalized, file)
    path.chmod(0o600)


async def discover_telegram_chats(token: str) -> list[dict]:
    if not token:
        raise ValueError("Enter a bot token first")
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 20})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError("Telegram could not find chats for this bot. Check the token and try again") from error
    if not payload.get("ok"):
        raise ValueError("Telegram rejected this bot token")
    chats = {}
    for update in payload.get("result", []):
        event = next((update[key] for key in ("message", "edited_message", "channel_post", "my_chat_member") if key in update), {})
        chat = event.get("chat", {})
        if "id" in chat:
            chats[str(chat["id"])] = {
                "id": str(chat["id"]),
                "name": chat.get("title")
                or " ".join(filter(None, (chat.get("first_name"), chat.get("last_name"))))
                or str(chat["id"]),
            }
    return list(chats.values())


def telegram_config(settings: Settings) -> dict:
    path = settings.data_dir / "telegram.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    if token := os.environ.get("JOB_RADAR_TELEGRAM_TOKEN"):
        config["token"] = token
    if chat_id := os.environ.get("JOB_RADAR_TELEGRAM_CHAT_ID"):
        config["chat_id"] = chat_id
    return normalize_telegram_config(config)
