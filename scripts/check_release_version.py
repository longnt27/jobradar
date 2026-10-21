"""Fail a release build when its tag and package versions disagree."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


root = Path(__file__).resolve().parents[1]
tag = sys.argv[1] if len(sys.argv) == 2 else ""
if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
    raise SystemExit("Release tag must look like v1.2.3")

version = tag[1:]
project_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
package_source = (root / "job_radar" / "__init__.py").read_text()
package_match = re.search(r'^__version__\s*=\s*"([^"]+)"', package_source, re.MULTILINE)
package_version = package_match.group(1) if package_match else None
if project_version != version or package_version != version:
    raise SystemExit(f"Release {tag} disagrees with pyproject.toml ({project_version}) or job_radar ({package_version})")
print(f"Release version {tag} verified")
