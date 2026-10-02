from __future__ import annotations

import getpass
import json
import os

from .settings import Settings


def configure_smtp(settings: Settings) -> None:
    settings.ensure_dirs()
    config = {
        "host": input("SMTP host: ").strip(),
        "port": int(input("SMTP port [587]: ").strip() or "587"),
        "user": input("SMTP username: ").strip(),
        "password": getpass.getpass("SMTP password or app password: "),
        "from": input("From address: ").strip(),
    }
    save_smtp(settings, config)
    print(f"SMTP configuration saved to {settings.data_dir / 'smtp.json'}")


def save_smtp(settings: Settings, config: dict) -> None:
    if not config.get("host") or not config.get("from") or int(config.get("port", 0)) not in (465, 587):
        raise ValueError("SMTP host, from address, and port 465 or 587 are required")
    settings.ensure_dirs()
    path = settings.data_dir / "smtp.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(config, file)
    path.chmod(0o600)


def smtp_config(settings: Settings) -> dict:
    path = settings.data_dir / "smtp.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    names = {"host": "JOB_RADAR_SMTP_HOST", "port": "JOB_RADAR_SMTP_PORT", "user": "JOB_RADAR_SMTP_USER",
             "password": "JOB_RADAR_SMTP_PASSWORD", "from": "JOB_RADAR_SMTP_FROM"}
    for key, env in names.items():
        if env in os.environ:
            data[key] = os.environ[env]
    data.setdefault("port", 587)
    data.setdefault("from", data.get("user", ""))
    return data
