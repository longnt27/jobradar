from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader

from job_radar.settings import Settings
from job_radar.web import create_app
from job_radar.drafting import ModelDraft, ProjectBullets, _run_provider
from job_radar.ingest import ObservedJob, ingest


def test_draft_uses_approved_evidence_and_renders_resume(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org", "skills": ["Python", "Machine learning"],
                    "education": [{"school": "Example University", "degree": "BSc Computer Science", "dates": "2022 – 2026"}],
                    "achievements": ["Programming award"], "skill_groups": {"Programming": "Python, C++"}})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "AI Engineer", "dates": "2024 – 2026", "bullets": ["Built Python models."]})
    client.post("/api/evidence", json={"kind": "project", "title": "Search pipeline", "claim": "Built a Python search pipeline.", "approved": True})
    client.post("/api/evidence", json={"kind": "project", "title": "Secret project", "claim": "Built secret models.", "approved": False})
    job = client.post("/api/jobs/import", json={"company": "Example AI", "title": "ML Engineer", "description": "Build Python machine learning search systems.", "apply_url": "https://example.org/apply"}).json()
    response = client.post(f"/api/jobs/{job['id']}/prepare", json={"provider": "template"})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["provider_mode"] == "local template; no model inference"
    assert len(draft["resume_data"]["projects"]) == 1
    assert len(draft["resume_data"]["experience"]) == 1
    assert "Secret" not in draft["message_data"]["body"]
    pdf = client.get(f"/api/applications/{draft['id']}/resume")
    assert pdf.status_code == 200
    extracted = PdfReader(Path(draft["resume_path"])).pages[0].extract_text()
    assert "Alex Example" in extracted
    assert "EXPERIENCE" in extracted and "SELECTED PROJECTS" in extracted
    assert "Example Labs" in extracted and "Example University" in extracted
    assert "Secret project" not in extracted
    revised = client.patch(f"/api/applications/{draft['id']}", json={"resume_data": {**draft["resume_data"], "summary": "Python search engineer"}}).json()
    assert revised["resume_path"] != draft["resume_path"]
    assert Path(draft["resume_path"]).exists()


def test_codex_provider_uses_scoped_cli_and_schema(monkeypatch) -> None:
    monkeypatch.setattr("job_radar.drafting.shutil.which", lambda name: f"/usr/bin/{name}")
    captured = []
    schemas = []

    def fake_run(args, **kwargs):
        captured.extend(args)
        import json
        schemas.append(json.loads(Path(args[args.index("--output-schema") + 1]).read_text()))
        Path(args[args.index("-o") + 1]).write_text('{"selected_evidence_ids":["one"],"summary":"Engineer","email_subject":"Application","email_body":"I built a search system."}')
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("job_radar.drafting.subprocess.run", fake_run)
    result = _run_provider("codex_local", {"company": "Example", "title": "Engineer", "description": "Search", "location": "Hanoi"},
                           {"name": "Alex", "skills": ["Python"]}, [{"id": "one", "kind": "project", "title": "Search", "claim": "Built a search system."}])
    assert result.selected_evidence_ids == ["one"]
    assert "--oss" in captured and "--local-provider" in captured
    assert "--output-schema" in captured and "read-only" in captured and "--ignore-user-config" in captured
    assert schemas[0]["additionalProperties"] is False
    assert set(schemas[0]["required"]) == set(schemas[0]["properties"])


def test_job_specific_project_bullets_are_used_in_resume(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    project = client.post("/api/evidence", json={"kind": "project", "title": "Search platform", "claim": "Built a Python index for documents.", "approved": True}).json()
    job = client.post("/api/jobs/import", json={"company": "Example AI", "title": "Search Engineer", "description": "Build Python document search and indexing systems."}).json()
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda provider, job, profile, cards: ModelDraft(
        selected_evidence_ids=[project["id"]], project_bullets=[ProjectBullets(evidence_id=project["id"], bullets=["Built a Python document index for search."])],
        summary="Python search engineer", email_subject="Search Engineer application", email_body="I built a Python index."
    ))
    response = client.post(f"/api/jobs/{job['id']}/prepare", json={"provider": "codex"})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["resume_data"]["projects"][0]["bullets"] == ["Built a Python document index for search."]
    assert "Built a Python document index for search." in PdfReader(Path(draft["resume_path"])).pages[0].extract_text()


def test_career_email_destination_prepares_email_application(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]})
    db = client.app.state.db
    source = db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")["id"]
    identifier, _ = ingest(db, source, ObservedJob("https://example.org/jobs/42", "AI Engineer", "Example", "Build AI systems with Python.", apply_url="mailto:careers@example.org"))
    draft = client.post(f"/api/jobs/{identifier}/prepare", json={"provider": "template"}).json()
    assert draft["destination"] == {"kind": "email", "email": "careers@example.org"}
