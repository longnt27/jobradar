from __future__ import annotations

import argparse

import uvicorn

from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="job-radar")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Start the local web app")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command in (None, "serve"):
        uvicorn.run("job_radar.web:create_app", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
