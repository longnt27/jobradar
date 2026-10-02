from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

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
    if not config["host"] or not config["from"] or config["port"] not in (465, 587):
        raise ValueError("SMTP host, from address, and port 465 or 587 are required")
    path = settings.data_dir / "smtp.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(config, file)
    path.chmod(0o600)
    print(f"SMTP configuration saved to {path}")


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
