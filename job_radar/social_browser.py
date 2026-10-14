import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

from .db import Database
from .desktop_handoff import BackgroundProcess


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


SITES = ("linkedin", "facebook", "zalo")
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


def chrome_profile_process(profile: Path) -> BackgroundProcess | None:
    """Find Chrome's actual owner, including one left behind by an older service."""
    try:
        pid = int(os.readlink(profile / "SingletonLock").rsplit("-", 1)[-1])
        if pid <= 0:
            return None
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, timeout=3, check=False)
        command = result.stdout.strip()
        if (result.returncode or not command.startswith(chrome_executable() + " ")
                or not re.search(r"(?:^|\s)--user-data-dir=" + re.escape(str(profile.resolve())) + r"(?:\s--|$)", command)):
            return None
        return BackgroundProcess(pid)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


async def close_orphaned_chrome(profile: Path) -> bool:
    """Release only Job Radar's saved profile, while the caller holds its lock."""
    process = await asyncio.to_thread(chrome_profile_process, profile)
    if process is None:
        clean_stale_chrome_lock(profile)
        return False
    process.terminate()
    try:
        await asyncio.to_thread(process.wait, timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        await asyncio.to_thread(process.wait, timeout=5)
    clean_stale_chrome_lock(profile)
    return True


def social_login_at(db: Database, site: str) -> str | None:
    saved = db.get_setting(f"social_login_completed_at_{site}")
    if saved:
        return saved
    if site == "zalo":
        return None
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
