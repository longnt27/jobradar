from __future__ import annotations

import argparse
import asyncio

import uvicorn

from .settings import Settings
from .collectors import login_browser


def main() -> None:
    parser = argparse.ArgumentParser(prog="job-radar")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Start the local web app")
    sub.add_parser("login", help="Open the persistent browser profile for manual sign-in")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command in (None, "serve"):
        uvicorn.run("job_radar.web:create_app", factory=True, host=settings.host, port=settings.port)
    elif args.command == "login":
        asyncio.run(login_browser(settings))


if __name__ == "__main__":
    main()
