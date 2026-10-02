from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader

from job_radar.settings import Settings
from job_radar.web import create_app


def test_draft_uses_approved_evidence_and_renders_resume(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org", "skills": ["Python", "Machine learning"]})
    client.put("/api/profile", json=profile)
    client.post("/api/evidence", json={"kind": "project", "title": "Search pipeline", "claim": "Built a Python search pipeline.", "approved": True})
    client.post("/api/evidence", json={"kind": "project", "title": "Secret project", "claim": "Built secret models.", "approved": False})
    job = client.post("/api/jobs/import", json={"company": "Example AI", "title": "ML Engineer", "description": "Build Python machine learning search systems.", "apply_url": "https://example.org/apply"}).json()
    response = client.post(f"/api/jobs/{job['id']}/prepare", json={"provider": "template"})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["provider_mode"] == "local template; no model inference"
    assert len(draft["resume_data"]["evidence"]) == 1
    assert "Secret" not in draft["message_data"]["body"]
    pdf = client.get(f"/api/applications/{draft['id']}/resume")
    assert pdf.status_code == 200
    assert "Alex Example" in PdfReader(Path(draft["resume_path"])).pages[0].extract_text()
