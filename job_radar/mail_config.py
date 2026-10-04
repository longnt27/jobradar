from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

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


def smtp_config_fingerprint(config: dict) -> str:
    fields = {key: config.get(key, "") for key in ("host", "port", "user", "password", "from")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def validated_smtp_config(settings: Settings) -> dict:
    config = smtp_config(settings)
    host = config.get("host", "")
    sender = config.get("from", "")
    port = int(config.get("port", 587))
    if not host or not sender:
        raise ValueError("Save your email settings in My profile first")
    if port not in (465, 587):
        raise ValueError("SMTP port must be 465 or 587")
    if host.lower() == "smtp.gmail.com" and (not config.get("user") or not config.get("password")):
        raise ValueError("Gmail needs your full email address and a Google app password")
    return config


def send_smtp_message(config: dict, message: EmailMessage) -> None:
    host = config["host"]
    port = int(config.get("port", 587))
    user = config.get("user", "")
    password = config.get("password", "")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as connection:
            if user:
                connection.login(user, password)
            connection.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as connection:
            connection.starttls(context=ssl.create_default_context())
            if user:
                connection.login(user, password)
            connection.send_message(message)


def send_test_email(settings: Settings) -> str:
    config = validated_smtp_config(settings)
    recipient = config["from"].strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", recipient):
        raise ValueError("Enter a valid From address before sending a test email")
    message = EmailMessage()
    message["From"] = recipient
    message["To"] = recipient
    message["Subject"] = "Job Radar email test"
    message.set_content("Your Job Radar email settings successfully connected to the mail server.\n\nThis is a test message; no job application was sent.")
    send_smtp_message(config, message)
    return recipient
