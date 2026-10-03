from __future__ import annotations

import re
import subprocess


def frontmost_app_bundle() -> str | None:
    """Remember the app that displayed Job Radar before Chrome takes focus."""
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e",
             'ObjC.import("AppKit"); ObjC.unwrap($.NSWorkspace.sharedWorkspace.frontmostApplication.bundleIdentifier)'],
            capture_output=True, text=True, timeout=3, check=False,
        )
        bundle = result.stdout.strip()
        return bundle if result.returncode == 0 and re.fullmatch(r"[A-Za-z0-9_.-]+", bundle) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def return_to_job_radar(bundle: str | None, port: int) -> None:
    url = f"http://127.0.0.1:{port}/#profile"
    browsers = {"com.google.Chrome", "com.apple.Safari", "org.mozilla.firefox", "com.microsoft.edgemac"}
    command = ["open", "-b", bundle, url] if bundle in browsers else ["open", "-b", bundle] if bundle else ["open", url]
    result = subprocess.run(command, capture_output=True, timeout=5, check=False)
    if result.returncode and bundle:
        subprocess.run(["open", url], capture_output=True, timeout=5, check=False)
