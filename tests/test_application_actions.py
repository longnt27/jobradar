from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.db import new_id, now
from job_radar.ingest import ObservedJob, ingest
from job_radar.settings import Settings
from job_radar.web import create_app


def _source(db, kind: str) -> str:
    row = db.one("SELECT id FROM sources WHERE kind=? LIMIT 1", (kind,))
    if row:
        return row["id"]
    identifier = new_id()
    db.execute(
        "INSERT INTO sources(id,kind,name,url,config,created_at) VALUES(?,?,?,?,?,?)",
        (identifier, kind, f"Test {kind}", f"https://example.org/{kind}", "{}", now()),
    )
    return identifier


def _prepare(tmp_path: Path, kind: str, observed: ObservedJob) -> dict:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={
        "company": "Prior Co", "role": "Engineer", "dates": "2024-2026", "bullets": ["Built Python systems."]
    })
    vacancy_id, _ = ingest(client.app.state.db, _source(client.app.state.db, kind), observed)
    response = client.post(f"/api/jobs/{vacancy_id}/prepare", json={"provider": "template"})
    assert response.status_code == 200, response.text
    return response.json()


def test_career_form_is_resolved_with_provenance(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "career", ObservedJob(
        "https://careers.example.org/jobs/42", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
        apply_url="https://boards.greenhouse.io/example/jobs/42#app",
    ))
    action = draft["destination"]
    assert action["kind"] == "web"
    assert action["action_type"] == "web_form"
    assert action["url"].startswith("https://boards.greenhouse.io/")
    assert action["provenance"] == "career_apply_link"
    assert action["confidence"] == "high"


def test_linkedin_easy_apply_is_distinct_from_external_apply(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/101/", "AI Engineer", "Example",
        "Build reliable AI systems in Python.",
        raw_text="AI Engineer\nEasy Apply\nBuild reliable AI systems in Python.",
    ))
    action = draft["destination"]
    assert action["kind"] == "web"
    assert action["action_type"] == "linkedin_easy_apply"
    assert action["url"] == "https://www.linkedin.com/jobs/view/101/"
    assert action["provenance"] == "linkedin_easy_apply_control"


def test_linkedin_external_apply_uses_external_form(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "linkedin", ObservedJob(
        "https://www.linkedin.com/jobs/view/102/", "Research Engineer", "Example",
        "Research and deploy computer vision systems.",
        apply_url="https://jobs.lever.co/example/abc123",
        raw_text="Research Engineer\nApply\nResearch and deploy computer vision systems.",
    ))
    action = draft["destination"]
    assert action["action_type"] == "web_form"
    assert action["url"] == "https://jobs.lever.co/example/abc123"
    assert action["provenance"] == "linkedin_external_apply"


def test_facebook_explicit_email_overrides_generic_outbound_link(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "facebook", ObservedJob(
        "https://www.facebook.com/groups/example/posts/1/", "AI Engineer", "Example",
        "We are hiring an AI Engineer. Send your CV to hiring@example.org.",
        apply_url="https://example.org/about-us",
        raw_text="We are hiring an AI Engineer. Send your CV to hiring@example.org.",
    ))
    action = draft["destination"]
    assert action["action_type"] == "email"
    assert action["email"] == "hiring@example.org"
    assert action["provenance"] == "facebook_posting_instruction"


def test_facebook_explicit_web_form_is_preserved(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "facebook", ObservedJob(
        "https://www.facebook.com/groups/example/posts/2/", "Data Engineer", "Example",
        "Apply using the form below for our Data Engineer role.",
        apply_url="https://forms.gle/ExampleForm123",
        raw_text="Apply using the form below for our Data Engineer role.",
    ))
    action = draft["destination"]
    assert action["action_type"] == "web_form"
    assert action["url"] == "https://forms.gle/ExampleForm123"
    assert action["provenance"] == "facebook_form_link"


def test_conflicting_explicit_destinations_require_manual_review(tmp_path: Path) -> None:
    draft = _prepare(tmp_path, "facebook", ObservedJob(
        "https://www.facebook.com/groups/example/posts/3/", "ML Engineer", "Example",
        "Apply at the form below or send your CV to jobs@example.org.",
        apply_url="https://forms.gle/AnotherForm123",
        raw_text="Apply at the form below or send your CV to jobs@example.org.",
    ))
    action = draft["destination"]
    assert action["kind"] == "manual"
    assert action["action_type"] == "manual"
    assert action["provenance"] == "conflicting_explicit_evidence"
    assert any("No application destination" in warning for warning in draft["warnings"])
