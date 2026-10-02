from __future__ import annotations

import plistlib
import os
import subprocess
import sys
from pathlib import Path

from .settings import Settings


LABEL = "com.local.job-radar"


def service_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def service_definition(settings: Settings) -> dict:
    paths = [str(Path.home() / ".local" / "bin"), str(Path(sys.executable).parent), os.environ.get("PATH", ""), "/usr/bin", "/bin"]
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "job_radar.cli", "serve"],
        "EnvironmentVariables": {"JOB_RADAR_DATA_DIR": str(settings.data_dir), "JOB_RADAR_PORT": str(settings.port),
                                 "PATH": os.pathsep.join(dict.fromkeys(part for value in paths for part in value.split(os.pathsep) if part))},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(settings.data_dir / "service.stdout.log"),
        "StandardErrorPath": str(settings.data_dir / "service.stderr.log"),
        "ProcessType": "Background",
    }


def install_service(settings: Settings) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("Background service installation is supported on macOS")
    settings.ensure_dirs()
    path = service_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(service_definition(settings)))
    domain = f"gui/{subprocess.check_output(['id', '-u'], text=True).strip()}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True, check=False)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(path)], text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "launchctl bootstrap failed")
    return path


def uninstall_service() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Background service installation is supported on macOS")
    path = service_path()
    if path.exists():
        domain = f"gui/{subprocess.check_output(['id', '-u'], text=True).strip()}"
        subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True, check=False)
        path.unlink()
