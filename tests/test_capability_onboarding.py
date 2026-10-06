from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.capabilities import capability_readiness, provider_processing
from job_radar.settings import Settings
from job_radar.web import create_app


def test_discovery_readiness_does_not_require_identity_or_drafting_provider() -> None:
    capabilities = capability_readiness(
        profile={"name": "", "email": "", "experience": [], "drafting_provider": ""},
        discovery={"level": "good", "counts": {"linkedin": 0, "facebook": 0, "career": 1},
                   "label": "Discovery coverage looks normal"},
        provider_is_available=False,
        approved_projects=0,
        matching_model="",
        telegram_configured=False,
        smtp_configured=False,
    )
    assert capabilities["discovery"]["ready"] is True
    assert capabilities["application_preparation"]["ready"] is False
    assert capabilities["automatic_drafts"]["ready"] is False


def test_application_preparation_accepts_work_history_or_project() -> None:
    base = dict(
        discovery={"level": "good", "counts": {"linkedin": 0, "facebook": 0, "career": 1}},
        provider_is_available=True,
        matching_model="",
        telegram_configured=False,
        smtp_configured=False,
    )
    with_history = capability_readiness(
        profile={"name": "Alex", "email": "alex@example.org",
                 "experience": [{"company": "Example"}], "drafting_provider": "codex"},
        approved_projects=0,
        **base,
    )
    assert with_history["application_preparation"]["ready"] is True

    with_project = capability_readiness(
        profile={"name": "Alex", "email": "alex@example.org",
                 "experience": [], "drafting_provider": "codex"},
        approved_projects=1,
        **base,
    )
    assert with_project["application_preparation"]["ready"] is True


def test_provider_processing_contract_distinguishes_local_and_remote() -> None:
    assert provider_processing("codex_local")["remote"] is False
    assert "stay on this Mac" in provider_processing("codex_local")["destination"]
    assert provider_processing("codex")["remote"] is True
    assert "leaves this Mac" in provider_processing("codex")["destination"]
    assert provider_processing("template")["remote"] is False


def test_setup_exposes_discovery_capability_without_profile_setup(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    app.state.db.execute("UPDATE sources SET enabled=0")
    assert client.get("/api/setup").json()["capabilities"]["discovery"]["ready"] is False

    response = client.post("/api/sources", json={
        "kind": "career",
        "name": "Example careers",
        "url": "https://example.org/careers",
        "enabled": True,
        "interval_minutes": 240,
        "config": {},
    })
    assert response.status_code == 201, response.text
    setup = client.get("/api/setup").json()
    assert setup["capabilities"]["discovery"]["ready"] is True
    assert setup["capabilities"]["application_preparation"]["ready"] is False


def test_point_of_use_privacy_controls_are_visible() -> None:
    root = Path(__file__).parents[1] / "job_radar" / "static"
    html = (root / "index.html").read_text()
    js = (root / "app.js").read_text()

    assert 'name="provider" required' in html
    assert 'id="resume-processing-disclosure"' in html
    assert 'id="project-processing-provider"' in html
    assert "processingCopy(setup, resumeForm.elements.provider.value, 'Your resume text')" in js
    assert "The repository snapshot used to draft project evidence" in js
    assert "The job posting, your profile details, and approved project evidence" in js
