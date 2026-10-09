from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


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


def activate_app(bundle: str | None) -> None:
    if bundle and re.fullmatch(r"[A-Za-z0-9_.-]+", bundle):
        try:
            subprocess.run(["open", "-b", bundle], capture_output=True, timeout=2, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def return_to_job_radar(bundle: str | None, port: int) -> None:
    url = f"http://127.0.0.1:{port}/#profile"
    browsers = {"com.google.Chrome", "com.apple.Safari", "org.mozilla.firefox", "com.microsoft.edgemac"}
    command = ["open", "-b", bundle, url] if bundle in browsers else ["open", "-b", bundle] if bundle else ["open", url]
    result = subprocess.run(command, capture_output=True, timeout=5, check=False)
    if result.returncode and bundle:
        subprocess.run(["open", url], capture_output=True, timeout=5, check=False)


def hide_chrome() -> None:
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to set visible of process "Google Chrome" to false'],
            capture_output=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


class BackgroundProcess:
    """Process wrapper for applications launched hidden via macOS AppKit."""

    def __init__(self, pid: int):
        self.pid = pid
        self._returncode: int | None = None

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        try:
            os.kill(self.pid, 0)
            return None
        except OSError:
            self._returncode = 0
            return 0

    def wait(self, timeout: float | None = None) -> int:
        start = time.time()
        while self.poll() is None:
            if timeout and (time.time() - start) > timeout:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.1)
        return self._returncode or 0

    def terminate(self) -> None:
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
        except OSError:
            pass


def launch_background_browser(app_bundle: str, args: list[str]) -> BackgroundProcess | None:
    """Launch Chrome hidden without activation using macOS NSWorkspace."""
    if sys.platform != "darwin" or not Path(app_bundle).is_dir():
        return None
    js = f"""
    ObjC.import("AppKit");
    const ws = $.NSWorkspace.sharedWorkspace;
    const url = $.NSURL.fileURLWithPath({json.dumps(app_bundle)});
    const config = $.NSWorkspaceOpenConfiguration.configuration;
    config.activates = false;
    config.hides = true;
    config.createsNewApplicationInstance = true;
    config.arguments = $({json.dumps(args)});

    let runningApp = null;
    ws.openApplicationAtURLConfigurationCompletionHandler(url, config, function(app, error) {{
        if (app) runningApp = app;
    }});

    const start = $.NSDate.date;
    while (!runningApp && $.NSDate.date.timeIntervalSinceDate(start) < 2) {{
        $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.05));
    }}
    let pid = runningApp ? runningApp.processIdentifier : null;
    console.log(JSON.stringify({{pid: pid}}));
    """
    try:
        res = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=5, check=False)
        output = (res.stderr or res.stdout).strip()
        data = json.loads(output)
        pid = data.get("pid")
        if isinstance(pid, int) and pid > 0:
            return BackgroundProcess(pid)
    except Exception:
        pass
    return None
