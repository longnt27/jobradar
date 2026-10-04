from __future__ import annotations

import argparse
import asyncio

import uvicorn

from .settings import Settings
from .browser_login import BrowserLoginManager
from .db import Database
from .service import install_service, uninstall_service
from .mail_config import configure_smtp
from .notifications import configure_telegram


def main() -> None:
    parser = argparse.ArgumentParser(prog="job-radar")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Start the local web app")
    login_parser = sub.add_parser("login", help="Sign in to LinkedIn or Facebook in regular Chrome")
    login_parser.add_argument("site", choices=("linkedin", "facebook"))
    sub.add_parser("install-service", help="Start Job Radar in the background at macOS login")
    sub.add_parser("uninstall-service", help="Stop and remove the macOS background service")
    sub.add_parser("configure-smtp", help="Save SMTP credentials in a restricted local file")
    sub.add_parser("configure-telegram", help="Save Telegram bot settings for application reviews")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command in (None, "serve"):
        uvicorn.run("job_radar.web:create_app", factory=True, host=settings.host, port=settings.port)
    elif args.command == "login":
        async def sign_in():
            manager = BrowserLoginManager(Database(settings.database_path), settings, asyncio.Lock())
            manager.start(args.site)
            print(f"Sign in to {args.site.capitalize()} in Chrome. Job Radar will detect completion automatically.")
            await manager.task
            if manager.state != "saved":
                raise RuntimeError(manager.error or "Sign-in did not complete")
            print(f"{args.site.capitalize()} session saved")
        asyncio.run(sign_in())
    elif args.command == "install-service":
        print(f"Installed {install_service(settings)}")
    elif args.command == "uninstall-service":
        uninstall_service()
        print("Background service removed")
    elif args.command == "configure-smtp":
        configure_smtp(settings)
    elif args.command == "configure-telegram":
        configure_telegram(settings)


if __name__ == "__main__":
    main()
