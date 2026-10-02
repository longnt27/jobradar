from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def from_env(cls) -> "Settings":
        default = Path.home() / "Library" / "Application Support" / "JobRadar"
        data_dir = Path(os.environ.get("JOB_RADAR_DATA_DIR", default)).expanduser().resolve()
        return cls(data_dir=data_dir, port=int(os.environ.get("JOB_RADAR_PORT", "8787")))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "job_radar.sqlite3"

    @property
    def browser_profile(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def repository_dir(self) -> Path:
        return self.data_dir / "repositories"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.repository_dir, self.artifact_dir):
            path.mkdir(parents=True, exist_ok=True)

