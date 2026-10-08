import base64
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from job_radar.db import Database
from job_radar.evidence import canonical_github_url, inspect_repository
from job_radar.evidence import ProjectContent, ProjectContentRaw, ProjectResult, generate_project_content
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


def test_repository_inspection_recovers_abandoned_shallow_lock(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "README.md").write_text("# Project\nThis provides a useful project summary.")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Initial"], check=True, capture_output=True)
    monkeypatch.setattr("job_radar.evidence.canonical_github_url", lambda _: (str(root), "test__project"))
    settings = Settings(tmp_path / "app")
    db = Database(settings.database_path)
    first = inspect_repository(db, settings, "https://github.com/test/project")
    lock = settings.repository_dir / "test__project" / ".git" / "shallow.lock"
    lock.touch()
    old = time.time() - 900
    os.utime(lock, (old, old))
    second = inspect_repository(db, settings, "https://github.com/test/project")
    assert second["repository_id"] == first["repository_id"]
    assert not lock.exists()


def test_large_github_repository_is_inspected_without_cloning(tmp_path: Path, monkeypatch) -> None:
    sha = "a" * 40
    readme = "# Large project\nBuilds a useful application."
    responses = {
        "/repos/test/large": {"default_branch": "main", "size": 480_000},
        "/repos/test/large/commits?per_page=12": [
            {"sha": sha, "commit": {"message": "Initial release\nDetails"}},
        ],
        f"/repos/test/large/contents?ref={sha}": [
            {"name": "README.md"}, {"name": "pyproject.toml"},
        ],
        f"/repos/test/large/readme?ref={sha}": {
            "encoding": "base64", "size": len(readme),
            "content": base64.b64encode(readme.encode()).decode(),
        },
        f"/repos/test/large/contents/pyproject.toml?ref={sha}": {
            "encoding": "base64", "size": 18,
            "content": base64.b64encode(b"[project]\nname='x'").decode(),
        },
    }
    monkeypatch.setattr("job_radar.evidence._github_json", lambda path, **_kwargs: responses[path])
    monkeypatch.setattr("job_radar.evidence._git", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Git should not run")))
    settings = Settings(tmp_path / "app")
    db = Database(settings.database_path)
    result = inspect_repository(db, settings, "https://github.com/test/large")
    assert result["commit_sha"] == sha
    assert "Builds a useful application" in result["summary"]["readme"]
    assert result["summary"]["recent_commits"] == ["aaaaaaa Initial release"]
    assert not (settings.repository_dir / "test__large").exists()
    assert db.one("SELECT local_path FROM repository_snapshots WHERE id=?", (result["repository_id"],))["local_path"] == ""
    assert db.one("SELECT id FROM evidence WHERE id=?", (result["evidence_id"],))


def test_html_readme_placeholder_cannot_be_approved(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "README.md").write_text('<div align="center">\n# Useful project\n</div>\nThis indexes documents.')
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Initial"], check=True, capture_output=True)
    monkeypatch.setattr("job_radar.evidence.canonical_github_url", lambda _: (str(root), "test__project"))
    settings = Settings(tmp_path / "app")
    client = TestClient(create_app(settings))
    result = inspect_repository(client.app.state.db, settings, "https://github.com/test/project")
    card = next(item for item in client.get("/api/evidence").json() if item["id"] == result["evidence_id"])
    assert "<div" not in card["claim"]
    blocked = client.patch(f"/api/evidence/{card['id']}", json={"approved": True, "details": {"bullets": [card["claim"]]}})
    assert blocked.status_code == 422
    reviewed = client.patch(f"/api/evidence/{card['id']}", json={"approved": True, "claim": "Built a Python document indexing pipeline.", "details": {"bullets": ["Built a Python document indexing pipeline."]}})
    assert reviewed.status_code == 200


def test_project_generation_failure_is_recorded_on_card(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    from job_radar.db import new_id, now
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/search", "/tmp/search", "abc123", '{"readme":"Python search project"}', now()))
    db.execute("INSERT INTO evidence(id,kind,title,claim,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (evidence_id, "project", "search", "Review required", repository_id, now(), now()))
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda *_args: (_ for _ in ()).throw(RuntimeError("Provider unavailable")))
    import pytest
    with pytest.raises(RuntimeError):
        generate_project_content(db, evidence_id, "codex")
    details = __import__("json").loads(db.one("SELECT details FROM evidence WHERE id=?", (evidence_id,))["details"])
    assert details["generation_status"] == "failed"
    assert "Provider unavailable" in details["generation_error"]


def test_failed_regeneration_keeps_previous_project_brief(tmp_path: Path, monkeypatch) -> None:
    import json
    import pytest
    from job_radar.db import new_id, now

    db = Database(Settings(tmp_path).database_path)
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/search", "", "abc123", '{"readme":"# Search"}', now()))
    previous = {
        "schema_version": 2, "what": "A reviewed document search application.",
        "why": "Find documents quickly.", "how": "Indexes document text.",
        "results": [{"id": "r1", "area": "Search", "outcome": "Returns cited search results.", "source": "README.md"}],
    }
    db.execute("INSERT INTO evidence(id,kind,title,claim,details,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
               (evidence_id, "project", "Search", "Returns cited search results.", json.dumps(previous), repository_id, now(), now()))
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda *_args: (_ for _ in ()).throw(RuntimeError("Provider unavailable")))
    with pytest.raises(RuntimeError):
        generate_project_content(db, evidence_id, "codex")
    retained = json.loads(db.one("SELECT details FROM evidence WHERE id=?", (evidence_id,))["details"])
    assert retained["what"] == previous["what"]
    assert retained["results"] == previous["results"]
    assert retained["generation_status"] == "failed"


def test_repository_inspection_returns_failed_card_for_invalid_model_output(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "README.md").write_text("# Document search\nIndexes documents with Python.")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-m", "Initial"], check=True, capture_output=True)
    monkeypatch.setattr("job_radar.evidence.canonical_github_url", lambda _: (str(root), "test__project"))
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda *_args: (_ for _ in ()).throw(ValueError("Invalid project bullets")))
    client = TestClient(create_app(Settings(tmp_path / "app")))
    response = client.post("/api/repositories/inspect", json={"url": "https://github.com/test/project", "provider": "codex"})
    assert response.status_code == 200
    assert "Invalid project bullets" in response.json()["generation_warning"]
    identifier = response.json()["evidence_id"]
    card = next(item for item in client.get("/api/evidence").json() if item["id"] == identifier)
    assert __import__("json").loads(card["details"])["generation_status"] == "failed"


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
        return ProjectContent(title="Document search", what="A Python document search system.",
                              why="Makes document retrieval easier to inspect.",
                              how="Indexes documents with Python and serves search results.",
                              tech_stack=["Python"],
                              results=[ProjectResult(area="Search", outcome="Indexes documents with Python for search.", source="README.md")])
    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    result = generate_project_content(db, evidence_id, "codex")
    card = db.one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
    assert result["details"]["results"][0]["outcome"] == "Indexes documents with Python for search."
    assert card["approved"] == 0
    assert captured[0][0] == "codex"
    assert "Indexes documents with Python" in captured[0][1]


def test_generated_project_separates_purpose_method_and_result_areas(tmp_path: Path, monkeypatch) -> None:
    from job_radar.db import new_id, now

    db = Database(Settings(tmp_path).database_path)
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/classroom", "", "abc123",
                '{"readme":"# Classroom research\\nLLM Beam reached 0.5043 F1. The detector improved Recall by 6.38 points."}', now()))
    db.execute("INSERT INTO evidence(id,kind,title,claim,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (evidence_id, "project", "classroom", "Review required", repository_id, now(), now()))

    def provider(_provider, _prompt, _response_type):
        return SimpleNamespace(
            title="Classroom behavior research", summary="Classroom analysis pipeline.",
            bullets=["LLM Beam reached 0.5043 F1."], tech_stack=["Python"],
            what="A research pipeline that converts classroom video into behavior timelines and temporal graphs.",
            why="Makes classroom behavior patterns inspectable across time.",
            how="Combines tracked visual events with verified graph search.",
            results=[
                SimpleNamespace(area="LLM", outcome="LLM Beam reached 0.5043 F1 on temporal graph recovery.", source="README.md: Results"),
                SimpleNamespace(area="Computer vision", outcome="The detector improved mean Recall by 6.38 percentage points.", source="README.md: Results"),
            ],
        )

    monkeypatch.setattr("job_radar.drafting._provider_json", provider)
    result = generate_project_content(db, evidence_id, "codex")
    details = result["details"]
    assert details["what"].startswith("A research pipeline")
    assert details["why"].startswith("Makes classroom")
    assert details["how"].startswith("Combines tracked")
    assert [item["area"] for item in details["results"]] == ["LLM", "Computer vision"]
    assert all(item["source"] == "README.md: Results" for item in details["results"])
    assert details["results"][0]["id"] != details["results"][1]["id"]
    assert not db.one("SELECT approved FROM evidence WHERE id=?", (evidence_id,))["approved"]


def test_structured_project_needs_complete_reviewed_brief_before_resumes(tmp_path: Path) -> None:
    from job_radar.db import new_id, now

    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/classroom", "", "abc123", "{}", now()))
    db.execute("INSERT INTO evidence(id,kind,title,claim,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (evidence_id, "project", "Classroom research", "Review required", repository_id, now(), now()))
    incomplete = {"schema_version": 2, "what": "Classroom analysis project", "why": "", "how": "",
                  "results": [], "tech_stack": ["Python"]}
    assert client.patch(f"/api/evidence/{evidence_id}", json={"details": incomplete}).status_code == 200
    blocked = client.patch(f"/api/evidence/{evidence_id}", json={"approved": True})
    assert blocked.status_code == 422
    complete = {**incomplete, "why": "Makes classroom behavior inspectable over time.",
                "how": "Combines tracked visual events with graph search.",
                "results": [{"id": "llm", "area": "LLM", "outcome": "LLM-guided graph search reached 0.5043 F1.",
                             "source": "README.md: Results"}]}
    saved = client.patch(f"/api/evidence/{evidence_id}", json={"details": complete})
    assert saved.status_code == 200
    reviewed = client.patch(f"/api/evidence/{evidence_id}", json={"approved": True})
    assert reviewed.status_code == 200
    card = next(item for item in client.get("/api/evidence").json() if item["id"] == evidence_id)
    assert card["claim"] == complete["results"][0]["outcome"]


def test_reviewing_project_does_not_requeue_completed_job_matches(tmp_path: Path) -> None:
    import json
    from job_radar.db import new_id, now

    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    job = client.post("/api/jobs/import", json={
        "company": "Example", "title": "LLM Engineer", "description": "Build retrieval systems."
    }).json()
    db.set_setting("matching_model", "test:small")
    db.execute("UPDATE vacancies SET analysis_status='done',analysis_model='test:small',"
               "score=82,score_detail=?,analyzed_at=? WHERE id=?",
               (json.dumps({"method": "local_llm"}), now(), job["id"]))
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/search", "", "abc123", "{}", now()))
    details = {"schema_version": 2, "what": "A document retrieval system.",
               "why": "It helps users find cited answers.",
               "how": "It combines indexed search with grounded generation.",
               "results": [{"id": "r1", "area": "RAG", "outcome": "Returns grounded answers with source citations.",
                            "source": "README.md — Results"}]}
    db.execute("INSERT INTO evidence(id,kind,title,claim,details,repository_id,created_at,updated_at) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (evidence_id, "project", "Search", "Returns grounded answers with source citations.",
                json.dumps(details), repository_id, now(), now()))

    for changes in ({"approved": True}, {"title": "Search project"}, {"approved": False}):
        assert client.patch(f"/api/evidence/{evidence_id}", json=changes).status_code == 200
        saved = db.one("SELECT analysis_status,score,analysis_model FROM vacancies WHERE id=?", (job["id"],))
        assert saved == {"analysis_status": "done", "score": 82, "analysis_model": "test:small"}


def test_project_generation_safely_bounds_provider_results(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    from job_radar.db import new_id, now
    repository_id, evidence_id = new_id(), new_id()
    db.execute("INSERT INTO repository_snapshots(id,url,local_path,commit_sha,summary,inspected_at) VALUES(?,?,?,?,?,?)",
               (repository_id, "https://github.com/alex/search", "/tmp/search", "abc123", '{"readme":"Python search project"}', now()))
    db.execute("INSERT INTO evidence(id,kind,title,claim,repository_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
               (evidence_id, "project", "search", "Review required", repository_id, now(), now()))
    prompts = []
    def fake_provider(_provider, prompt, response_type):
        prompts.append(prompt)
        assert response_type is ProjectContentRaw
        return ProjectContentRaw(title="Search", what="A Python search system.",
                                 why="Helps users find relevant documents.",
                                 how="Builds a searchable index from document text.",
                                 results=[{"area": "Search", "outcome": ("Supported search result with complete words. " * 12) if i == 0 else f"Supported search result number {i}.", "source": "README.md"}
                                          for i in range(10)])
    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    result = generate_project_content(db, evidence_id, "codex")
    assert len(result["details"]["results"]) == 5
    assert result["details"]["results"][0]["outcome"].endswith("…")
    assert db.one("SELECT approved FROM evidence WHERE id=?", (evidence_id,))["approved"] == 0
    assert "2 to 5 of the strongest, distinct results" in prompts[0]
    assert "Omit incidental viewers" in prompts[0]
    assert "recognizable repository or product name" in prompts[0]
