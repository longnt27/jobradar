import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS installer")
def test_one_command_installer_runs_install_steps(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.txt"
    fake_uv = bin_dir / "uv"
    fake_uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$JOB_RADAR_INSTALL_LOG"\n')
    fake_uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "JOB_RADAR_INSTALL_LOG": str(log), "JOB_RADAR_INSTALL_TEST_MODE": "1"}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([str(root / "install.sh")], cwd=root, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "sync --locked",
        "run --no-sync playwright install chromium",
        "run --no-sync job-radar install-service",
    ]
