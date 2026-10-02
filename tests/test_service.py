import json
import sys
from pathlib import Path

from job_radar.mail_config import smtp_config
from job_radar.service import service_definition
from job_radar.settings import Settings


def test_background_service_reuses_local_data_directory(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    definition = service_definition(settings)
    assert definition["RunAtLoad"] is True
    assert definition["KeepAlive"] is True
    assert definition["ProgramArguments"][:2] == [sys.executable, "-m"]
    assert definition["EnvironmentVariables"]["JOB_RADAR_DATA_DIR"] == str(tmp_path)


def test_smtp_configuration_reads_restricted_local_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "smtp.json"
    path.write_text(json.dumps({"host": "mail.example.org", "port": 465, "user": "alex", "password": "secret"}))
    monkeypatch.delenv("JOB_RADAR_SMTP_HOST", raising=False)
    config = smtp_config(Settings(tmp_path))
    assert config["host"] == "mail.example.org"
    assert config["from"] == "alex"
