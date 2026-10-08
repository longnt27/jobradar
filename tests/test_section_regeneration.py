import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_radar.drafting import get_draft, prepare_draft, regenerate_draft
from job_radar.ingest import ObservedJob, ingest
from job_radar.settings import Settings
from job_radar.web import create_app


def _draft(tmp_path: Path, *, projects: bool = False) -> tuple[TestClient, dict, list[str]]:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org",
                    "summary": "Original professional summary.",
                    "skills": ["Python", "SQL"],
                    "skill_groups": {"Programming": ["Python"]},
                    "education": [{"school": "Example University", "degree": "BSc Computer Science", "dates": "2022-2026"}],
                    "achievements": ["Won a regional programming contest."]})
    assert client.put("/api/profile", json=profile).status_code == 200
    assert client.post("/api/positions", json={"company": "Prior Co", "role": "Engineer", "dates": "2024-2026",
                                               "bullets": ["Built Python search systems for 120 users."]}).status_code == 201
    project_ids = []
    if projects:
        for index in range(3):
            result = client.post("/api/evidence", json={"kind": "project", "title": f"Project {index}",
                                                         "claim": f"Built project {index} with Python.",
                                                         "approved": True})
            assert result.status_code == 201
            identifier = result.json()["id"]
            project_ids.append(identifier)
            details = {"what": f"Project {index} measures search quality.", "why": "Improve search",
                       "how": "Python ranking and evaluation", "tech_stack": ["Python", "Evaluation"],
                       "results": [{"id": f"result-{index}", "area": "Search",
                                    "outcome": f"Reached {80 + index}% recall on a public dataset."}]}
            assert client.patch(f"/api/evidence/{identifier}", json={"details": details}).status_code == 200
    job = client.post("/api/jobs/import", json={"company": "Search Co", "title": "Search Engineer",
                                                 "description": "Build and evaluate Python search systems.",
                                                 "apply_url": "https://example.org/apply"}).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    client.app.state.db.execute("UPDATE application_drafts SET provider='codex' WHERE id=?", (draft["id"],))
    return client, get_draft(client.app.state.db, draft["id"]), project_ids


def test_summary_regeneration_requests_only_summary_and_preserves_other_sections(tmp_path: Path, monkeypatch) -> None:
    client, before, _ = _draft(tmp_path)
    calls = []

    def fake_provider(provider, prompt, response_type):
        calls.append((provider, prompt, set(response_type.model_fields)))
        return response_type.model_validate({"summary": "Built reliable search systems for 120 users."})

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: pytest.fail("full draft provider called"))
    revised = regenerate_draft(client.app.state.db, client.app.state.settings, before["id"],
                               "Emphasize production impact", "summary")

    assert calls[0][0] == "codex"
    assert calls[0][2] == {"summary"}
    assert "email_subject" not in calls[0][1]
    assert revised["resume_data"] == {**before["resume_data"], "summary": "Built reliable search systems for 120 users."}
    assert revised["message_data"] == before["message_data"]
    assert revised["form_data"] == before["form_data"]
    assert revised["destination"] == before["destination"]
    assert revised["resume_hash"] != before["resume_hash"]
    assert [change["section"] for change in revised["changes"]] == ["summary"]


def test_project_regeneration_replaces_only_approved_project_section(tmp_path: Path, monkeypatch) -> None:
    client, before, project_ids = _draft(tmp_path, projects=True)
    requested_ids = project_ids[::-1]
    first = next(item for item in client.get("/api/evidence").json() if item["id"] == project_ids[0])
    details = json.loads(first["details"])
    details["results"].append({"id": "result-0b", "area": "Search",
                               "outcome": "Reduced search latency by 25% on the same public dataset."})
    assert client.patch(f"/api/evidence/{project_ids[0]}", json={"details": details}).status_code == 200

    def fake_provider(_provider, prompt, response_type):
        assert set(response_type.model_fields) == {"selected_evidence_ids", "project_bullets", "project_focus", "bold_phrases"}
        assert "email_subject" not in prompt
        assert "one to three complementary job-relevant result IDs" in prompt
        assert "combine their supported outcomes in the single second bullet" in prompt
        assert "Every metric in bullet 2 must be supported by a result ID in project_focus" in prompt
        assert "Keep internal logs and trace artifacts out of result bullets" in prompt
        return response_type.model_validate({
            "selected_evidence_ids": requested_ids,
            "project_bullets": [{"evidence_id": identifier,
                                 "bullets": [f"Built project {index} with Python ranking and evaluation.",
                                             f"Reached {80 + index}% recall on a public dataset" +
                                             (" and reduced search latency by 25%." if index == 0 else ".")],
                                 "skills": ["Python", "Search", "Evaluation"]}
                                for index, identifier in reversed(list(enumerate(project_ids)))],
            "project_focus": [{"evidence_id": identifier, "result_ids": [f"result-{index}"] + (["result-0b"] if index == 0 else [])}
                              for index, identifier in enumerate(project_ids)],
            "bold_phrases": ["82% recall", "81% recall", "80% recall"],
        })

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: pytest.fail("full draft provider called"))
    revised = regenerate_draft(client.app.state.db, client.app.state.settings, before["id"],
                               "Put the strongest measured project first", "projects")

    assert [item["id"] for item in revised["resume_data"]["projects"]] == requested_ids
    assert revised["evidence_ids"] == requested_ids
    assert revised["resume_data"]["projects"][-1]["result_ids"] == ["result-0", "result-0b"]
    assert len(revised["resume_data"]["projects"][-1]["bullets"]) == 2
    assert "recall" in revised["resume_data"]["projects"][-1]["bullets"][1]
    assert "latency" in revised["resume_data"]["projects"][-1]["bullets"][1]
    for key, value in before["resume_data"].items():
        if key not in {"projects", "evidence", "bold_phrases"}:
            assert revised["resume_data"][key] == value
    assert revised["message_data"] == before["message_data"]
    assert revised["resume_hash"] != before["resume_hash"]
    assert [change["section"] for change in revised["changes"]] == ["projects"]


def test_invalid_project_or_stale_section_response_does_not_replace_draft(tmp_path: Path, monkeypatch) -> None:
    client, before, project_ids = _draft(tmp_path, projects=True)
    db = client.app.state.db
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda _provider, _prompt, response_type:
                        response_type.model_validate({"selected_evidence_ids": ["not-approved", *project_ids[:2]],
                                                      "project_bullets": [], "project_focus": [], "bold_phrases": []}))
    with pytest.raises(ValueError, match="approved"):
        regenerate_draft(db, client.app.state.settings, before["id"], "Use another project", "projects")
    assert get_draft(db, before["id"])["package_hash"] == before["package_hash"]

    def edit_while_generating(_provider, _prompt, response_type):
        changed = {**before["resume_data"], "summary": "Edited while the model was working."}
        client.patch(f"/api/applications/{before['id']}", json={"resume_data": changed})
        return response_type.model_validate({"summary": "Stale model output."})

    monkeypatch.setattr("job_radar.drafting._provider_json", edit_while_generating)
    with pytest.raises(ValueError, match="changed while"):
        regenerate_draft(db, client.app.state.settings, before["id"], "Shorten summary", "summary")
    assert get_draft(db, before["id"])["resume_data"]["summary"] == "Edited while the model was working."


def test_message_regeneration_uses_verified_facebook_source_and_specific_job_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    db.set_setting("profile", {"name": "Nguyen Trung Long", "application_name": "Nguyễn Trung Long",
                               "application_school": "Đại học Bách khoa Hà Nội",
                               "education": [{"school": "Hanoi University of Science and Technology",
                                              "degree": "Bachelor of Computer Science", "dates": "2022-2026"}],
                               "email": "long@example.org", "skills": ["Python", "PyTorch"],
                               "experience": [{"company": "VinSmart Future", "role": "AI Engineering Intern",
                                               "dates": "2025-2026", "bullets": ["Built an end-to-end 3D reconstruction pipeline for robot dataset synthesis."]}]})
    source_id = db.one("SELECT id FROM sources LIMIT 1")["id"]
    db.execute("UPDATE sources SET kind='facebook',name='AI Jobs',url=? WHERE id=?",
               ("https://www.facebook.com/groups/1", source_id))
    job_id, _ = ingest(db, source_id, ObservedJob(
        url="https://www.facebook.com/groups/1/posts/2", company="Facebook post",
        title="[SETA] TUYỂN DỤNG AI ENGINEER", description=(
            "Tuyển dụng AI Engineer. Yêu cầu Python, PyTorch và một dự án AI end-to-end. "
            "Có kinh nghiệm Computer Vision và LLM.")))
    before = prepare_draft(db, client.app.state.settings, job_id, "template")
    db.execute("UPDATE application_drafts SET provider='codex' WHERE id=?", (before["id"],))
    captured = []

    def fake_provider(_provider, prompt, response_type):
        captured.append(prompt)
        return response_type.model_validate({
            "subject": "Ứng tuyển AI Engineer — Nguyễn Trung Long",
            "body": ("Kính gửi SETA,\n\nTôi thấy tin tuyển AI Engineer trên Facebook và quan tâm vì vị trí phù hợp với hướng phát triển của tôi. "
                     "Tôi học Khoa học máy tính tại Đại học Bách khoa Hà Nội. "
                     "Tại VinSmart Future, tôi xây dựng pipeline tạo dữ liệu robot bằng tái dựng 3D. "
                     "Các dự án cá nhân của tôi liên quan đến Computer Vision, LLM và học sâu. "
                     "Anh/chị vui lòng xem CV đính kèm để biết thêm chi tiết. "
                     "Rất mong có cơ hội trao đổi sâu hơn về vị trí này với quý công ty.\n\n"
                     "Trân trọng,\nNguyễn Trung Long"),
        })

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    revised = regenerate_draft(db, client.app.state.settings, before["id"], "Match the posting", "message")

    assert '"posting_source": {"kind": "facebook"' in captured[0]
    assert "Python, PyTorch" in captured[0]
    assert "Built an end-to-end 3D reconstruction pipeline for robot dataset synthesis" in captured[0]
    assert '"application_school": "Đại học Bách khoa Hà Nội"' in captured[0]
    assert '"education": [{"school": "Hanoi University of Science and Technology"' in captured[0]
    assert "why the role interests the candidate" in captured[0]
    assert "mention the field of study and school briefly before work experience" in captured[0]
    assert "without naming individual projects" in captured[0]
    assert "describe it as robot-data engineering" in captured[0]
    assert "Keep both parts of this call to action" in captured[0]
    assert "Rất mong có cơ hội trao đổi" in captured[0]
    assert revised["resume_data"] == before["resume_data"]
    assert [change["section"] for change in revised["changes"]] == ["message"]


def test_long_message_revision_keeps_current_draft(tmp_path: Path, monkeypatch) -> None:
    client, before, _ = _draft(tmp_path)
    db = client.app.state.db
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda _provider, _prompt, response_type:
                        response_type.model_validate({"subject": "Search Engineer — Alex Example",
                                                      "body": "I am applying for this role. " * 40}))
    with pytest.raises(ValueError, match="brief application message"):
        regenerate_draft(db, client.app.state.settings, before["id"], "Keep this short", "message")
    assert get_draft(db, before["id"])["package_hash"] == before["package_hash"]


@pytest.mark.parametrize("section,model_output", [
    ("experience", {"positions": [{"index": 0, "bullets": ["Served 120 users with Python search systems."]}]}),
    ("education", {"entries": [{"index": 0, "degree": "Bachelor of Computer Science"}]}),
    ("achievements", {"achievements": ["Placed first in a regional programming contest."]}),
    ("skills", {"groups": [{"label": "Programming", "skills": ["Python", "SQL"]}]}),
    ("message", {"subject": "Application for Search Engineer — Alex Example",
                 "body": "Dear hiring team, Alex Example is applying for this role."}),
])
def test_other_sections_use_small_schema_and_preserve_reviewed_content(
    tmp_path: Path, monkeypatch, section: str, model_output: dict
) -> None:
    client, before, _ = _draft(tmp_path)
    db = client.app.state.db
    captured = []

    def fake_provider(_provider, prompt, response_type):
        captured.append((prompt, set(response_type.model_fields)))
        return response_type.model_validate(model_output)

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: pytest.fail("full draft provider called"))
    revised = regenerate_draft(db, client.app.state.settings, before["id"], "Make this more relevant", section)

    assert len(captured) == 1
    assert len(captured[0][1]) <= 2
    assert revised["destination"] == before["destination"]
    assert revised["form_data"] == before["form_data"]
    assert revised["evidence_ids"] == before["evidence_ids"]
    assert [change["section"] for change in revised["changes"]] == [section]
    if section == "message":
        assert "about six short sentences" in captured[0][0]
        assert "attached resume" in captured[0][0]
        assert revised["resume_data"] == before["resume_data"]
        assert revised["message_data"] != before["message_data"]
    else:
        assert revised["message_data"] == before["message_data"]
        assert revised["resume_data"] != before["resume_data"]


def test_api_accepts_targeted_skills_regeneration(tmp_path: Path, monkeypatch) -> None:
    client, before, _ = _draft(tmp_path)
    client.app.state.auto_apply_manager.register_review(before)
    monkeypatch.setattr("job_radar.drafting._provider_json", lambda _provider, _prompt, response_type:
                        response_type.model_validate({"groups": [{"label": "Programming", "skills": ["Python", "SQL"]}]}))
    inspected = []

    async def unexpected_inspection(*_args):
        inspected.append(True)
        raise AssertionError("Targeted resume edit must not reinspect the web form")

    monkeypatch.setattr("job_radar.auto_apply.inspect_form", unexpected_inspection)

    response = client.post(f"/api/applications/{before['id']}/regenerate",
                           json={"section": "skills", "prompt": "Add my reviewed SQL skill"})

    assert response.status_code == 200, response.text
    assert response.json()["regenerated_section"] == "skills"
    assert response.json()["resume_data"]["skill_groups"] == {"Programming": ["Python", "SQL"]}
    assert not inspected
