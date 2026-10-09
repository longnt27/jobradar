import os
import shutil
from pathlib import Path

from .db import Database


def clean_stale_chrome_lock(profile_dir: Path | str) -> None:
    profile = Path(profile_dir)
    lock_file = profile / "SingletonLock"
    if not lock_file.is_symlink() and not lock_file.exists():
        return
    try:
        target = os.readlink(lock_file)
        pid_str = target.rsplit("-", 1)[-1]
        pid = int(pid_str)
        try:
            os.kill(pid, 0)
            return
        except OSError:
            pass
    except Exception:
        pass
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink(missing_ok=True)
        except Exception:
            pass


SITES = ("linkedin", "facebook")
CHROME_KEYCHAIN_ARGS = ("--password-store=basic", "--use-mock-keychain")


def chrome_executable() -> str:
    for path in (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if path.is_file():
            return str(path)
    if path := shutil.which("google-chrome"):
        return path
    raise ValueError("Google Chrome is required for social sign-in. Install Chrome, then try again.")


def social_login_at(db: Database, site: str) -> str | None:
    saved = db.get_setting(f"social_login_completed_at_{site}")
    if saved:
        return saved
    # Existing installs used one shared completion date for both sites.
    return db.get_setting("browser_login_completed_at")


def chrome_context_options(*, required: bool = False) -> dict:
    try:
        chrome_executable()
    except ValueError:
        if required:
            raise
        return {}
    return {"channel": "chrome", "ignore_default_args": list(CHROME_KEYCHAIN_ARGS)}
