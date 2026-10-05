from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.settings import Settings
from job_radar.web import create_app


def test_profile_stack_exposes_structured_editors_and_settings_boundary(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    html = client.get("/").text
    script = client.get("/app.js").text
    css = client.get("/app.css").text

    assert '<section id="settings" class="tab">' in html
    assert '<section id="profile" class="tab">' in html
    profile_html = html.split('<section id="profile" class="tab">', 1)[1].split('<section id="personal"', 1)[0]
    assert 'id="provider-panel"' not in profile_html
    assert 'id="smtp-panel"' not in profile_html
    assert 'id="telegram-panel"' not in profile_html

    for editor in ("skills-editor", "skill-groups-editor", "education-editor", "achievements-editor", "links-editor"):
        assert f'id="{editor}"' in html
    assert "movePosition(positionId, delta)" in script
    assert "window.confirm" in script
    assert "compositionstart" in script and "compositionend" in script
    assert "Include in resumes" in script and "Delete project" in script
    assert 'aria-pressed="${card.id === selectedProjectId' in script
    assert 'role="region" aria-label="Project details" tabindex="-1"' in html
    assert "$('#project-editor').focus({preventScroll:true})" in script
    assert "Open repository for" in script
    assert ".project-results-list{max-height:none;overflow:visible" in css


def test_project_can_be_deleted_without_touching_other_evidence(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    first = client.post("/api/evidence", json={
        "kind": "project", "title": "First", "claim": "Built a useful system",
        "support": [], "approved": False,
    }).json()["id"]
    second = client.post("/api/evidence", json={
        "kind": "project", "title": "Second", "claim": "Built another useful system",
        "support": [], "approved": False,
    }).json()["id"]

    response = client.delete(f"/api/evidence/{first}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    remaining = client.get("/api/evidence").json()
    assert [row["id"] for row in remaining] == [second]
    assert client.delete(f"/api/evidence/{first}").status_code == 404


def test_model_download_status_supports_progress_and_cancel(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    manager = app.state.match_manager
    manager.pull_state = "downloading"
    manager.pull_progress = 42
    manager.pull_detail = "pulling model layers"

    status = manager.status()
    assert status["download_state"] == "downloading"
    assert status["download_progress"] == 42
    assert status["download_detail"] == "pulling model layers"

    response = TestClient(app).delete("/api/matching/model/download")
    assert response.status_code == 200
    assert response.json()["download_state"] == "cancelled"
