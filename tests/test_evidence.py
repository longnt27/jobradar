import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import Database
from job_radar.evidence import canonical_github_url, inspect_repository
from job_radar.evidence import ProjectContent, generate_project_content
from job_radar.settings import Settings
from job_radar.web import create_app


def test_evidence_review_api(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    response = client.post("/api/evidence", json={"kind": "experience", "title": "ML engineer", "claim": "Built a search pipeline."})
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert client.get("/api/evidence").json()[0]["approved"] == 0
    assert client.patch(f"/api/evidence/{identifier}", json={"claim": "Built a Python search pipeline.", "approved": True}).status_code == 200
    assert client.get("/api/evidence").json()[0]["approved"] == 1


def test_repository_inspection_requires_claim_review(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "README.md").write_text("# Useful project\nThis indexes documents.")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Initial"], check=True, capture_output=True)
    monkeypatch.setattr("job_radar.evidence.canonical_github_url", lambda _: (str(root), "test__project"))
    settings = Settings(tmp_path / "app")
    db = Database(settings.database_path)
    result = inspect_repository(db, settings, "https://github.com/test/project")
    card = db.one("SELECT * FROM evidence WHERE id=?", (result["evidence_id"],))
    assert card["approved"] == 0
    assert "Useful project" in card["claim"]
    assert canonical_github_url("https://github.com/test/project.git")[0] == "https://github.com/test/project"


def test_codex_project_content_stays_unapproved_until_review(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    from job_radar.db import new_id, now
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/search", "/tmp/search", "abc123", '{"readme":"# Search\\nIndexes documents with Python.","files":["main.py"]}', now()))
    db.execute("INSERT INTO evidence(id,kind,title,claim,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (evidence_id, "project", "search", "Repository available for review.", repository_id, now(), now()))
    captured = []
    def fake_provider(provider, prompt, response_type):
        captured.append((provider, prompt, response_type))
        return ProjectContent(title="Document search", summary="Python document search system.", tech_stack=["Python"], bullets=["Indexes documents with Python."])
    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    result = generate_project_content(db, evidence_id, "codex")
    card = db.one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    assert result["details"]["bullets"] == ["Indexes documents with Python."]
    assert card["approved"] == 0
    assert captured[0][0] == "codex"
    assert "Indexes documents with Python" in captured[0][1]
