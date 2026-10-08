from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from job_radar.settings import Settings
from job_radar.web import create_app
from job_radar.drafting import (ApplicationMessage, EnglishTranslations, ModelDraft, ProjectBullets,
                                 TranslationItem, _ensure_english_resume, _job_language, _relevant_results,
                                 _message_in_job_language, _run_provider, _selected_resume_projects,
                                 _template, prepare_draft,
                                 get_draft, refresh_draft_content)
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
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    assert draft["provider_mode"] == "local template; no model inference"
    assert len(draft["resume_data"]["projects"]) == 1
    assert len(draft["resume_data"]["experience"]) == 1
    assert "Secret" not in draft["message_data"]["body"]
    pdf = client.get(f"/api/applications/{draft['id']}/resume")
    assert pdf.status_code == 200
    assert pdf.headers["content-disposition"].startswith("inline;")
    pages = client.get(f"/api/applications/{draft['id']}/resume/preview/pages").json()["pages"]
    assert pages >= 1
    preview = client.get(f"/api/applications/{draft['id']}/resume/preview?page=1")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG\r\n\x1a\n")
    extracted = PdfReader(Path(draft["resume_path"])).pages[0].extract_text()
    assert "Alex Example" in extracted
    assert "Experience" in extracted and "Selected Projects" in extracted
    assert "Example Labs" in extracted and "Example University" in extracted
    assert "Secret project" not in extracted
    revised = client.patch(f"/api/applications/{draft['id']}", json={"resume_data": {**draft["resume_data"], "summary": "Python search engineer"}}).json()
    assert revised["resume_path"] != draft["resume_path"]
    assert Path(draft["resume_path"]).exists()
    sections = {**revised["resume_data"],
                "experience": [{**revised["resume_data"]["experience"][0], "bullets": ["Corrected experience bullet."]}],
                "education": [{"school": "Example University", "degree": "BSc Computer Science", "dates": "2023 – 2027"}],
                "achievements": ["Corrected achievement."]}
    updated = client.patch(f"/api/applications/{draft['id']}", json={"resume_data": sections}).json()
    text = PdfReader(Path(updated["resume_path"])).pages[0].extract_text()
    assert "Corrected experience bullet." in text
    assert "2023 – 2027" in text
    assert "Corrected achievement." in text


def test_codex_provider_uses_scoped_cli_and_schema(monkeypatch) -> None:
    monkeypatch.setattr("job_radar.drafting.shutil.which", lambda name: f"/usr/bin/{name}")
    captured = []
    schemas = []
    prompts = []

    def fake_run(args, **kwargs):
        captured.extend(args)
        prompts.append(kwargs["input"])
        import json
        schemas.append(json.loads(Path(args[args.index("--output-schema") + 1]).read_text()))
        Path(args[args.index("-o") + 1]).write_text('{"selected_evidence_ids":["one"],"summary":"Engineer","email_subject":"Application","email_body":"I built a search system."}')
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("job_radar.drafting.subprocess.run", fake_run)
    result = _run_provider("codex_local", {"company": "Example", "title": "Engineer", "description": "Search " + "x" * 35_000, "location": "Hanoi"},
                           {"name": "Alex", "skills": ["Python"]}, [{"id": "one", "kind": "project", "title": "Search", "claim": "Built a search system."}])
    assert result.selected_evidence_ids == ["one"]
    assert "--oss" in captured and "--local-provider" in captured
    assert "--output-schema" in captured and "read-only" in captured and "--ignore-user-config" in captured
    assert schemas[0]["additionalProperties"] is False
    assert set(schemas[0]["required"]) == set(schemas[0]["properties"])
    assert "bold_phrases" in schemas[0]["properties"]
    assert "Select exactly three approved project IDs" in prompts[0]
    assert "Bullet 1 explains the problem, what the project does, and how it works" in prompts[0]
    assert "one to three complementary job-relevant result IDs" in prompts[0]
    assert "combine their supported outcomes in the single second bullet" in prompts[0]
    assert "PA-MPJPE" in prompts[0]
    assert "Every metric in bullet 2 must be supported by a result ID in project_focus" in prompts[0]
    assert "three to five short, appealing skill categories" in prompts[0]
    assert "including Vietnamese diacritics" in prompts[0]
    assert "application_name" in prompts[0]
    assert "Do not copy repository caveat notes into resume bullets" in prompts[0]
    assert '"claim": "Built a search system."' in prompts[0]
    assert "three short sentences" in prompts[0]
    assert "attached resume" in prompts[0]
    assert "Do not repeat resume bullets" in prompts[0]


def test_explicitly_selected_approved_results_survive_area_filtering() -> None:
    job = {"title": "Computer Vision Engineer", "description": "Evaluate pose accuracy."}
    results = [{"id": "pose", "area": "3D pose evaluation", "outcome": "PA-MPJPE 51.80 mm."},
               {"id": "graph", "area": "LLM", "outcome": "Graph recovery F1 0.50."}]
    assert [item["id"] for item in _relevant_results(job, results, ["pose", "graph"])] == ["pose", "graph"]


def test_template_application_note_is_brief_and_points_to_resume() -> None:
    job = {"company": "Example AI", "title": "AI Engineer", "description": "Build machine learning search systems."}
    profile = {"name": "Alex Example", "experience": [{"company": "Example Labs", "role": "AI Engineer Intern",
               "bullets": ["Improved model accuracy by 18% on 10,000 examples."]}]}
    card = {"id": "project", "title": "Machine learning search project", "claim": "Reached 93% accuracy on 2,000 samples.",
            "details": {"results": []}}
    body = _template(job, profile, [card]).email_body
    assert len(body.split()) <= 90
    assert "AI Engineer Intern" in body and "Example Labs" in body
    assert "related personal projects" in body.lower()
    assert "attached resume" in body.lower()
    assert "18%" not in body and "93%" not in body


def test_resume_and_application_email_use_separate_identity(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Nguyen Trung Long", "application_name": "Nguyễn Trung Long",
                    "application_school": "Đại học Bách khoa Hà Nội", "email": "long@example.org",
                    "education": [{"school": "Hanoi University of Science and Technology",
                                   "degree": "Bachelor of Computer Science", "dates": "2022 – 2026"}]})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "Engineer",
                                         "dates": "2025 – 2026", "bullets": ["Built vision systems."]})
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "Kỹ sư AI",
                                                "description": "Tuyển dụng kỹ sư phát triển hệ thống AI."}).json()
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "template")
    assert draft["resume_data"]["name"] == "Nguyen Trung Long"
    assert draft["resume_data"]["education"][0]["school"] == "Hanoi University of Science and Technology"
    assert "Nguyễn Trung Long" in draft["message_data"]["body"]
    assert "Nguyễn Trung Long" in draft["message_data"]["subject"]


def test_resume_selects_only_three_projects_in_model_order() -> None:
    cards = [{"id": str(i), "title": f"Project {i}", "claim": f"Built {i}",
              "details": {"results": []}} for i in range(4)]
    model = ModelDraft(selected_evidence_ids=["3", "1", "2", "0"], summary="Engineer",
                       email_subject="Application", email_body="Hello")
    _, projects = _selected_resume_projects({}, cards, model, "template")
    assert [project["id"] for project in projects] == ["3", "1", "2"]


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
    draft = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "codex")
    assert draft["resume_data"]["projects"][0]["bullets"] == ["Built a Python document index for search."]
    assert "Built a Python document index for search." in PdfReader(Path(draft["resume_path"])).pages[0].extract_text()


def test_project_result_focus_changes_with_job_without_claiming_unsupported_llm_work(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    projects = [
        ("CausClass", [
            {"id": "llm", "area": "LLM", "outcome": "LLM-guided beam search reached 0.5043 F1 in temporal graph recovery.", "source": "README.md"},
            {"id": "vision", "area": "Computer vision", "outcome": "YOLO perception improved detector Recall by 6.38 percentage points.", "source": "README.md"},
        ]),
        ("Quizzer", [
            {"id": "rag", "area": "LLM and RAG", "outcome": "RAG regression fixtures reached 100% Recall at 10.", "source": "README.md"},
            {"id": "documents", "area": "Document processing", "outcome": "Extracts text and visual figures from source documents.", "source": "README.md"},
        ]),
        ("Pronunciation Assessment", [{"id": "evaluation", "area": "Model evaluation", "outcome": "Evaluated CTC pronunciation systems with speaker-disjoint test data.", "source": "README.md"}]),
        ("WHAM", [
            {"id": "motion", "area": "Computer vision", "outcome": "Reconstructed body motion from video and gyroscope data.", "source": "README.md"},
            {"id": "models", "area": "Model deployment", "outcome": "Runs seven models on an iPhone for motion reconstruction.", "source": "README.md"},
            {"id": "pose", "area": "Pose evaluation", "outcome": "Evaluated 3D pose against a vision baseline.", "source": "README.md"},
        ]),
    ]
    ids = {}
    for title, results in projects:
        response = client.post("/api/evidence", json={"kind": "project", "title": title,
                             "claim": results[0]["outcome"], "approved": True})
        assert response.status_code == 201
        ids[title] = response.json()["id"]
        details = {"what": f"{title} research project", "why": "Improve analysis quality", "how": "Uses tested models",
                   "results": results, "bullets": [item["outcome"] for item in results], "tech_stack": []}
        assert client.patch(f"/api/evidence/{response.json()['id']}", json={"details": details}).status_code == 200

    llm_job = client.post("/api/jobs/import", json={"company": "Example", "title": "LLM Engineer",
                          "description": "Build LLM RAG systems and evaluate model quality."}).json()
    llm_draft = prepare_draft(client.app.state.db, client.app.state.settings, llm_job["id"], "template")
    llm_projects = {item["title"]: item for item in llm_draft["resume_data"]["projects"]}
    assert {"CausClass", "Quizzer", "Pronunciation Assessment"} <= llm_projects.keys()
    assert "WHAM" not in llm_projects
    assert any("0.5043 F1" in bullet for bullet in llm_projects["CausClass"]["bullets"])
    assert not any("6.38" in bullet for bullet in llm_projects["CausClass"]["bullets"])
    assert not any("LLM" in bullet for bullet in llm_projects["Pronunciation Assessment"]["bullets"])

    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: ModelDraft(
        selected_evidence_ids=[ids["WHAM"]], summary="Relevant AI work",
        email_subject="Application", email_body="I am applying for this role.",
    ))
    with pytest.raises(ValueError, match="fewer than three"):
        prepare_draft(client.app.state.db, client.app.state.settings, llm_job["id"], "codex")

    model_bullets = [ProjectBullets(evidence_id=ids[title], bullets=[what, result]) for title, what, result in [
        ("CausClass", "Analyzes classroom behavior with reviewed models.", "LLM-guided beam search reached 0.5043 F1 in temporal graph recovery."),
        ("Quizzer", "Tests document retrieval with RAG fixtures.", "RAG regression fixtures reached 100% Recall at 10."),
        ("Pronunciation Assessment", "Evaluates CTC pronunciation systems on speaker-disjoint data.", "Measured pronunciation quality with speaker-disjoint tests."),
    ]]
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: ModelDraft(
        selected_evidence_ids=[ids["CausClass"], ids["Quizzer"], ids["Pronunciation Assessment"]],
        project_bullets=model_bullets, bold_phrases=["0.5043 F1", "100% Recall at 10"],
        summary="Relevant AI work", email_subject="Application", email_body="I am applying for this role.",
    ))
    ai_draft = prepare_draft(client.app.state.db, client.app.state.settings, llm_job["id"], "codex")
    ai_titles = {item["title"] for item in ai_draft["resume_data"]["projects"]}
    assert {"CausClass", "Quizzer", "Pronunciation Assessment"} <= ai_titles
    assert "WHAM" not in ai_titles
    assert ai_draft["resume_data"]["bold_phrases"] == ["0.5043 F1", "100% Recall at 10"]

    vision_job = client.post("/api/jobs/import", json={"company": "Example", "title": "Computer Vision Engineer",
                             "description": "Improve video perception and object detection with YOLO."}).json()
    vision_draft = prepare_draft(client.app.state.db, client.app.state.settings, vision_job["id"], "template")
    vision_projects = {item["title"]: item for item in vision_draft["resume_data"]["projects"]}
    assert "Quizzer" not in vision_projects
    assert any("6.38" in bullet for bullet in vision_projects["CausClass"]["bullets"])
    assert not any("0.5043 F1" in bullet for bullet in vision_projects["CausClass"]["bullets"])


def test_refreshing_draft_projects_keeps_saved_application_answers(tmp_path: Path, monkeypatch) -> None:
    import json
    from job_radar import drafting

    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Prior Co", "role": "Engineer", "dates": "2024-2026",
                                        "bullets": ["Built Python systems."]})
    job = client.post("/api/jobs/import", json={"company": "Example", "title": "LLM Engineer",
                                                   "description": "Build RAG systems."}).json()
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: ModelDraft(
        selected_evidence_ids=[], summary="Original summary", email_subject="Original subject",
        email_body="I am applying for this role."))
    original = prepare_draft(client.app.state.db, client.app.state.settings, job["id"], "codex")
    form = {"kind": "linkedin_easy_apply", "answers": {"0": "2"}, "fields": [{"index": 0, "label": "Years"}],
            "inspection_blockers": [], "complete": False}
    message = {"subject": "Edited subject", "body": "Edited application message."}
    client.app.state.db.execute("UPDATE application_drafts SET form_data=?,message_data=? WHERE id=?",
                                (json.dumps(form), json.dumps(message), original["id"]))
    project = client.post("/api/evidence", json={"kind": "project", "title": "CausClass",
                                                   "claim": "LLM beam search recovered temporal graphs.",
                                                   "approved": True}).json()
    details = {"what": "Classroom analysis", "results": [{"id": "llm", "area": "LLM",
               "outcome": "LLM beam search recovered temporal graphs at 0.50 F1.", "source": "README.md"}],
               "tech_stack": ["Python"]}
    client.patch(f"/api/evidence/{project['id']}", json={"details": details})
    def quota_failure(*_args):
        raise RuntimeError("codex drafting failed: provider quota or rate limit reached")
    monkeypatch.setattr("job_radar.drafting._run_provider", quota_failure)
    with pytest.raises(RuntimeError, match="quota"):
        drafting.refresh_draft_projects(client.app.state.db, client.app.state.settings, original["id"])
    failed = drafting.get_draft(client.app.state.db, original["id"])
    assert failed["form_data"] == form
    assert failed["message_data"] == message
    assert failed["resume_hash"] == original["resume_hash"]
    assert "quota" in failed["project_refresh_error"]
    monkeypatch.setattr("job_radar.drafting._run_provider", lambda *_args: ModelDraft(
        selected_evidence_ids=[project["id"]], summary="Changed summary", email_subject="Changed subject",
        email_body="Changed email."))

    refreshed = drafting.refresh_draft_projects(client.app.state.db, client.app.state.settings, original["id"])
    assert refreshed["form_data"] == form
    assert refreshed["message_data"] == message
    assert refreshed["resume_data"]["summary"] == "Original summary"
    assert refreshed["resume_data"]["projects"][0]["title"] == "CausClass"
    assert refreshed["resume_data"]["projects"][0]["result_ids"] == ["llm"]
    assert refreshed["evidence_ids"] == [project["id"]]
    assert refreshed["resume_hash"] != original["resume_hash"]
    assert refreshed["project_refresh_error"] is None


def test_refresh_one_draft_uses_exact_profile_identity_and_keeps_reviewed_data(tmp_path: Path, monkeypatch) -> None:
    import json

    client = TestClient(create_app(Settings(tmp_path)))
    db = client.app.state.db
    profile = client.get("/api/profile").json()
    profile.update({"name": "Nguyen Trung Long", "email": "long@example.org",
                    "education": [{"school": "Hanoi University of Science and Technology",
                                   "degree": "Bachelor of Computer Science", "dates": "2022–2026"}]})
    db.set_setting("profile", profile)
    project = client.post("/api/evidence", json={"kind": "project", "title": "Vision project",
                                           "claim": "Measured image quality.", "approved": True}).json()

    def model(_provider, _job, candidate, _cards):
        name = candidate["name"]
        return ModelDraft(selected_evidence_ids=[project["id"]],
                          project_bullets=[ProjectBullets(evidence_id=project["id"],
                                                          bullets=["Built image processing with Python.",
                                                                   "Improved measured image quality."],
                                                          skills=["Computer Vision", "Python", "Image Processing", "Evaluation"])],
                          summary="Computer vision engineer.",
                          email_subject=f"Ứng tuyển kỹ sư AI – {name}",
                          email_body=f"Kính gửi nhà tuyển dụng. Tôi ứng tuyển vị trí kỹ sư AI.\n\nTrân trọng,\n{name}")

    monkeypatch.setattr("job_radar.drafting._run_provider", model)
    jobs = [client.post("/api/jobs/import", json={"company": "Example", "title": f"Kỹ sư AI {index}",
                                                 "description": "Tuyển dụng kỹ sư AI phát triển hệ thống thị giác máy tính."}).json()
            for index in (1, 2)]
    first = prepare_draft(db, client.app.state.settings, jobs[0]["id"], "codex")
    second = prepare_draft(db, client.app.state.settings, jobs[1]["id"], "codex")
    db.execute("UPDATE application_drafts SET form_data=?,destination=? WHERE id=?",
               (json.dumps({"answers": {"experience": "2"}}),
                json.dumps({"kind": "email", "email": "jobs@example.org"}), first["id"]))
    profile["name"] = "Nguyễn Trung Long"
    profile["education"][0]["school"] = "Đại học Bách khoa Hà Nội"
    db.set_setting("profile", profile)

    refreshed = refresh_draft_content(db, client.app.state.settings, first["id"])
    assert refreshed["resume_data"]["name"] == "Nguyễn Trung Long"
    assert refreshed["resume_data"]["education"][0]["school"] == "Đại học Bách khoa Hà Nội"
    assert refreshed["resume_data"]["projects"][0]["tech_stack"] == ["Computer Vision", "Python", "Image Processing", "Evaluation"]
    assert "Nguyễn Trung Long" in refreshed["message_data"]["subject"]
    assert refreshed["message_data"]["body"].endswith("Nguyễn Trung Long")
    assert refreshed["form_data"]["answers"] == {"experience": "2"}
    assert refreshed["destination"]["email"] == "jobs@example.org"
    assert "Nguyễn Trung Long" in PdfReader(Path(refreshed["resume_path"])).pages[0].extract_text()
    assert get_draft(db, second["id"])["resume_hash"] == second["resume_hash"]


def test_career_email_destination_prepares_email_application(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    profile = client.get("/api/profile").json()
    profile.update({"name": "Alex Example", "email": "alex@example.org"})
    client.put("/api/profile", json=profile)
    client.post("/api/positions", json={"company": "Example Labs", "role": "Engineer", "dates": "2024–2026", "bullets": ["Built Python systems."]})
    db = client.app.state.db
    source = db.one("SELECT id FROM sources WHERE kind='career' LIMIT 1")["id"]
    identifier, _ = ingest(db, source, ObservedJob("https://example.org/jobs/42", "AI Engineer", "Example", "Build AI systems with Python.", apply_url="mailto:careers@example.org"))
    draft = prepare_draft(client.app.state.db, client.app.state.settings, identifier, "template")
    assert draft["destination"]["kind"] == "email"
    assert draft["destination"]["action_type"] == "email"
    assert draft["destination"]["email"] == "careers@example.org"
    assert draft["destination"]["confidence"] == "high"


def test_vietnamese_posting_gets_vietnamese_email_and_english_cv_rule(monkeypatch) -> None:
    job = {"title": "Kỹ sư dữ liệu", "company": "Ví dụ", "description":
           "Tuyển dụng kỹ sư dữ liệu. Yêu cầu kinh nghiệm phát triển hệ thống dữ liệu."}
    profile = {"name": "Alex", "summary": "Python engineer", "experience":
               [{"company": "Prior Co", "role": "Engineer", "bullets": ["Built data systems."]}]}
    assert _job_language(job) == "Vietnamese"
    assert _job_language({"title": "AI Engineer", "description": "Build Python systems."}) == "English"
    template = _template(job, profile, [])
    assert "Kính gửi" in template.email_body and "Ứng tuyển" in template.email_subject
    english = ModelDraft(summary="Python engineer", email_subject="Application", email_body="Dear team. I built data systems.")
    prompts = []

    def fake_provider(_provider, prompt, response_type):
        prompts.append(prompt)
        assert response_type is ApplicationMessage
        return ApplicationMessage(subject="Ứng tuyển vị trí Kỹ sư dữ liệu", body="Kính gửi bộ phận tuyển dụng. Tôi có kinh nghiệm phát triển hệ thống dữ liệu.")

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    revised = _message_in_job_language("codex", job, english)
    assert revised.email_body.startswith("Kính gửi")
    assert "Vietnamese" in prompts[0]


def test_english_email_keeps_accented_name_and_vietnamese_job_title(monkeypatch) -> None:
    job = {
        "title": "MB Trainee - AI Engineer - Khối Công Nghệ Thông Tin",
        "company": "MBBank",
        "description": "Develop and evaluate AI models for banking applications.",
    }
    name = "Nguyễn Trung Long"
    body = (f"Dear MBBank team,\n\nI am applying for the {job['title']} role. "
            f"My experience in model evaluation fits this position.\n\nBest,\n{name}")
    draft = ModelDraft(summary="AI engineer", email_subject=f"Application - {name}", email_body=body)

    def fail_if_called(*_args):
        raise AssertionError("An English email must not be translated because of proper names")

    monkeypatch.setattr("job_radar.drafting._provider_json", fail_if_called)
    result = _message_in_job_language("codex", job, draft, name)
    assert result.email_body == body


def test_overlong_application_message_uses_small_rewrite(monkeypatch) -> None:
    name = "Alex Example"
    original = ModelDraft(summary="AI engineer", email_subject=f"AI Engineer application — {name}",
                          email_body="Dear team,\n\n" + "I built relevant systems and models. " * 35 + f"\n\nBest,\n{name}")
    prompts = []

    def fake_provider(_provider, prompt, response_type):
        prompts.append((prompt, response_type))
        return ApplicationMessage(subject=original.email_subject,
                                  body=f"Dear team,\n\nI saw your AI Engineer posting and am interested. "
                                       "My AI internship and personal projects are relevant. "
                                       f"Please check my attached resume.\n\nBest,\n{name}")

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    revised = _message_in_job_language("codex", {"title": "AI Engineer", "company": "Example",
                                                 "description": "Build AI systems."}, original, name)
    assert revised.summary == original.summary
    assert len(revised.email_body.split()) < 90
    assert len(prompts) == 1
    assert prompts[0][1] is ApplicationMessage


def test_vietnamese_experience_is_translated_before_cv_render(monkeypatch) -> None:
    resume = {"name": "Alex", "summary": "Kỹ sư dữ liệu có kinh nghiệm phát triển hệ thống.",
              "experience": [{"company": "Prior Co", "role": "Kỹ sư dữ liệu",
                              "bullets": ["Phát triển hệ thống dữ liệu bằng Python."]}],
              "projects": [], "skills": ["Python"]}
    with pytest.raises(ValueError, match="AI drafting provider"):
        _ensure_english_resume("template", resume)

    def fake_provider(_provider, prompt, response_type):
        assert response_type is EnglishTranslations
        assert "Prior Co" not in prompt
        return EnglishTranslations(items=[
            TranslationItem(index=0, text="Data engineer experienced in building systems."),
            TranslationItem(index=1, text="Data engineer"),
            TranslationItem(index=2, text="Built data systems with Python."),
        ])

    monkeypatch.setattr("job_radar.drafting._provider_json", fake_provider)
    translated = _ensure_english_resume("codex", resume)
    assert translated["summary"] == "Data engineer experienced in building systems."
    assert translated["experience"][0]["company"] == "Prior Co"
    assert translated["experience"][0]["bullets"] == ["Built data systems with Python."]
