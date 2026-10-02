from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader

from job_radar.settings import Settings
from job_radar.web import create_app
from job_radar.drafting import _run_provider


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
    revised = client.patch(f"/api/applications/{draft['id']}", json={"resume_data": {**draft["resume_data"], "summary": "Python search engineer"}}).json()
    assert revised["resume_path"] != draft["resume_path"]
    assert Path(draft["resume_path"]).exists()


def test_codex_provider_uses_scoped_cli_and_schema(monkeypatch) -> None:
    monkeypatch.setattr("job_radar.drafting.shutil.which", lambda name: f"/usr/bin/{name}")
    captured = []

    def fake_run(args, **kwargs):
        captured.extend(args)
        Path(args[args.index("-o") + 1]).write_text('{"selected_evidence_ids":["one"],"summary":"Engineer","email_subject":"Application","email_body":"I built a search system."}')
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("job_radar.drafting.subprocess.run", fake_run)
    result = _run_provider("codex_local", {"company": "Example", "title": "Engineer", "description": "Search", "location": "Hanoi"},
                           {"name": "Alex", "skills": ["Python"]}, [{"id": "one", "kind": "project", "title": "Search", "claim": "Built a search system."}])
    assert result.selected_evidence_ids == ["one"]
    assert "--oss" in captured and "--local-provider" in captured
    assert "--output-schema" in captured and "read-only" in captured
