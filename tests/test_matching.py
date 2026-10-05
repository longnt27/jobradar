import json
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from job_radar.ingest import ObservedJob, ingest
from job_radar.local_analysis import Criterion, JobFacts, LocalModelUnavailable, MatchJudgment, _generate, _ground_facts, analyze_job, clean_saved_analysis, experience_criterion, extract_salary_range, freshness_criterion, validate_local_model, finalize_match, extract_years_required, role_fallback
from job_radar.settings import Settings
from job_radar.web import create_app
from job_radar.matching import MatchManager


def test_local_analysis_extracts_facts_and_weights_nine_scores(monkeypatch) -> None:
    prompts = []
    stages = []

    def generate(_model, prompt, result_type):
        prompts.append(prompt)
        if result_type is JobFacts:
            return JobFacts(role="AI Engineer", seniority="mid", required_skills=["Python"],
                            preferred_skills=["PyTorch"], years_required=3, location="Hanoi",
                            work_mode="hybrid", responsibilities=["Build models"], education=[],
                            languages=[], summary="Builds AI models.")
        return MatchJudgment(**{name: Criterion(score=8, reason="Supported by candidate evidence")
                                for name in MatchJudgment.model_fields if name != "summary"},
                             summary="Good match based on relevant experience.")

    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    score, detail = analyze_job(
        {"company": "Example", "title": "AI Engineer", "description": "Build Python models in Hanoi.",
         "location": "Hanoi"},
        {"skills": ["Python"], "location": "Hanoi", "experience": [{"role": "ML Engineer", "dates": "2022-2025"}]},
        [{"title": "Vision", "claim": "Built a model", "details": {"bullets": ["Built a model"], "tech_stack": ["Python"]}}],
        "test:small",
        stages.append,
    )
    assert 0 < score < 100
    assert detail["hard_exclusions"] == []
    assert len(detail["criteria"]) == 9
    assert detail["facts"]["required_skills"] == ["Python"]
    assert detail["method"] == "local_llm"
    assert "ML Engineer" in prompts[1] and "Vision" in prompts[1]
    assert stages == ["scoring"]


def test_binance_advanced_degree_is_graded_evidence_and_role_stays_about_role(monkeypatch) -> None:
    calls = []
    def generate(_model, _prompt, result_type):
        calls.append(result_type)
        if result_type is JobFacts:
            return JobFacts(role="Senior Data Scientist", seniority="Senior", required_skills=[],
                            preferred_skills=[], years_required=None, location="", work_mode="Remote",
                            responsibilities=[], education=["related field"], languages=[], summary="")
        return MatchJudgment(**{name: Criterion(score=10, reason=(
            "The candidate has a Bachelor of Computer Science from Hanoi University, matching this Senior Data Scientist role."
            if name == "role" else "Strong match"))
            for name in MatchJudgment.model_fields if name != "summary"}, summary="Strong match")
    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    job = {"title": "Binance Accelerator Program - Data Scientist (User Growth)",
           "description": "Currently pursuing a Master's or Ph.D. degree in Computer Science, Statistics, Mathematics, Artificial Intelligence, or a related field.",
           "location": "Remote"}
    profile = {"education": [{"degree": "Bachelor of Computer Science", "dates": "2022 – 2026"}]}
    score, detail = analyze_job(job, profile, [], "test:small")
    assert score > 0
    assert detail["hard_exclusions"] == []
    assert detail["facts"]["seniority"] == ""
    assert detail["facts"]["role"] == "Data Scientist"
    assert "degree" not in detail["criteria"]["role"]["reason"].casefold()
    assert detail["criteria"]["role"]["score"] == 5
    assert detail["criteria"]["education"]["score"] == 1
    assert "scoring_skipped" not in detail
    assert calls == [JobFacts, MatchJudgment]


def test_salary_can_be_extracted_from_facebook_post_title() -> None:
    title = "[VCCorp-HN] Tuyển 2 AI Engineer, làm AI Agent, LLM, từ 1-2 năm kn. Offer 14- 23M."
    assert extract_salary_range(title) == "Offer 14- 23M."
    assert extract_years_required(title + "\nVới hơn 15 năm hình thành và phát triển, VCCorp tuyển AI Engineer.") == 1
    assert role_fallback({"title": title}, {"experience": [{"role": "AI Engineering Intern"}]}) == {
        "score": 9, "reason": "AI Engineer aligns with the documented AI Engineering Intern position."}


def test_vietnamese_jd_requirements_survive_small_model_noise() -> None:
    description = ("YÊU CẦU CÔNG VIỆC\n* Kiến thức OOP, Design Pattern.\n"
                   "* Kiến thức nền tốt về học máy, deep learning, LLM\n"
                   "* Có kinh nghiệm với agent framework như langchain, Autogen hoặc vector database như qdrant\n"
                   "LƯƠNG VÀ THƯỞNG:\n* Dải lương dự kiến: 14,000,000 – 23,000,000 VNĐ")
    model = JobFacts(role="AI Engineer", seniority="Senior", required_skills=["Triển khai và fine-tuning các mô hình LLM"],
                     preferred_skills=[], years_required=None, location="Hanoi", work_mode="",
                     responsibilities=[], education=[], languages=[], summary="Senior AI Engineer tại Hà Nội")
    facts = _ground_facts(model, {"title": "AI Engineer", "description": description})
    assert facts.required_skills == ["LLM", "LangChain", "AutoGen", "Vector database", "Qdrant",
                                    "Machine learning", "Deep learning", "OOP"]
    assert facts.seniority == ""
    assert "Senior" not in facts.summary


def test_analysis_policy_change_queues_existing_jobs_once(tmp_path) -> None:
    settings = Settings(tmp_path)
    app = create_app(settings)
    db = app.state.db
    job_id = TestClient(app).post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
        "description": "Build AI systems in Hanoi."}).json()["id"]
    db.set_setting("matching_model", "test:small")
    db.execute("UPDATE vacancies SET analysis_status='done' WHERE id=?", (job_id,))
    manager = MatchManager(db, settings)
    async def start_and_stop():
        await manager.start()
        assert db.one("SELECT analysis_status FROM vacancies WHERE id=?", (job_id,))["analysis_status"] == "pending"
        await manager.stop()
    asyncio.run(start_and_stop())
    assert db.get_setting("analysis_version", 0) == 5


def test_unstated_requirements_get_neutral_score(monkeypatch) -> None:
    def generate(_model, _prompt, result_type):
        if result_type is JobFacts:
            return JobFacts(role="AI Engineer", seniority="", required_skills=["Python"],
                            preferred_skills=[], years_required=None, location="Hanoi", work_mode="",
                            responsibilities=["Build models"], education=[], languages=[], summary="")
        return MatchJudgment(**{name: Criterion(score=1, reason="Missing")
                                for name in MatchJudgment.model_fields if name != "summary"}, summary="Weak fit")
    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    score, detail = analyze_job({"title": "AI Engineer", "description": "Build Python models in Hanoi.",
                                 "location": "Hanoi"}, {}, [], "test:small")
    assert detail["criteria"]["education"] == {"score": 5, "reason": "Not stated in the posting; neutral."}
    assert detail["criteria"]["preferred_skills"]["score"] == 5
    assert "research" not in detail["criteria"]
    assert score == 34


def test_extraction_discards_unsupported_language_seniority_and_years() -> None:
    facts = JobFacts(role="AI Engineer", seniority="Senior", required_skills=["Python", "Go"],
                    preferred_skills=[], years_required=5, location="Hanoi", work_mode="",
                    responsibilities=["Build models"], education=["Master's degree"],
                    languages=["English"], summary="Build models.")
    grounded = _ground_facts(facts, {"title": "AI Engineer", "description": "Build models with Python in Hanoi. 3 years required."})
    assert grounded.required_skills == ["Python"]
    assert grounded.languages == []
    assert grounded.seniority == ""
    assert grounded.years_required == 3
    assert grounded.education == []


def test_experience_score_uses_documented_years_and_merges_overlaps() -> None:
    as_of = datetime(2026, 10, 5, tzinfo=timezone.utc)
    profile = {"experience": [
        {"role": "AI Engineering Intern", "dates": "Dec 2025 – Jun 2026"},
        {"role": "Applied AI Trainee", "dates": "Jul 2026 – Present"},
        {"role": "Overlapping project role", "dates": "Jan 2026 – Mar 2026"},
    ]}
    result = experience_criterion(3, profile, as_of=as_of)
    assert result["score"] == 4
    assert "0.9 years" in result["reason"]
    assert "3 years" in result["reason"]
    assert experience_criterion(None, profile, as_of=as_of)["score"] == 5
    assert experience_criterion(3, {"experience": [{"dates": "Unknown"}]}, as_of=as_of)["score"] == 5


def test_saved_match_replaces_role_similarity_with_years_of_experience(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("profile", {"experience": [
        {"role": "AI Engineering Intern", "dates": "Dec 2025 – Jun 2026"},
        {"role": "Applied AI Trainee", "dates": "Jul 2026 – Sep 2026"},
    ]})
    job_id = TestClient(app).post("/api/jobs/import", json={"company": "Facebook post",
        "title": "AI Engineer", "description": "Từ 3 năm kinh nghiệm làm việc thực tế ở vị trí AI Engineer"}).json()["id"]
    criteria = {name: {"score": 10, "reason": "Strong role fit"}
                for name in MatchJudgment.model_fields if name != "summary"}
    detail = {"method": "local_llm", "facts": {"years_required": 3},
              "criteria": criteria, "explanation": "The candidate has extensive experience."}
    db.execute("UPDATE vacancies SET analysis_status='done',published_at=?,score=100,score_detail=? WHERE id=?",
               (datetime.now(timezone.utc).isoformat(), json.dumps(detail), job_id))
    assert clean_saved_analysis(db) == 1
    updated = db.one("SELECT score,score_detail FROM vacancies WHERE id=?", (job_id,))
    result = json.loads(updated["score_detail"])
    assert result["criteria"]["experience"]["score"] < 5
    assert "3 years" in result["criteria"]["experience"]["reason"]
    assert result["hard_exclusions"] == []
    assert updated["score"] > 0


def test_new_match_does_not_call_short_role_history_three_years_of_experience(monkeypatch) -> None:
    def generate(_model, _prompt, result_type):
        if result_type is JobFacts:
            return JobFacts(role="AI Engineer", seniority="", required_skills=[], preferred_skills=[],
                            years_required=None, location="", work_mode="", responsibilities=[],
                            education=[], languages=[], summary="")
        return MatchJudgment(**{name: Criterion(score=10, reason="Relevant role")
                                for name in MatchJudgment.model_fields if name != "summary"},
                             summary="The candidate has extensive experience.")
    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    _, detail = analyze_job({"title": "AI Engineer", "description": "Từ 3 năm kinh nghiệm làm việc thực tế"},
        {"experience": [{"dates": "Dec 2025 – Jun 2026"}, {"dates": "Jul 2026 – Sep 2026"}]}, [], "test:small")
    assert detail["facts"]["years_required"] == 3
    assert detail["criteria"]["experience"]["score"] < 5
    assert detail["hard_exclusions"] == []


def test_language_salary_and_freshness_are_grounded_in_the_job_posting(tmp_path: Path) -> None:
    description = ("English proficiency required. Build Python systems. "
                   "Salary: VND 18–30 million per month. Anomaly detection and Explainable AI.")
    facts = JobFacts(role="AI Engineer", seniority="", required_skills=["Python"],
        preferred_skills=[], years_required=None, location="", work_mode="", responsibilities=[],
        education=[], languages=["Python", "Anomaly detection", "English"],
        salary_range="VND 18–30 million", summary="")
    grounded = _ground_facts(facts, {"title": "AI Engineer", "description": description})
    assert grounded.languages == ["English"]
    assert grounded.salary_range == "VND 18–30 million"
    assert extract_salary_range("Thu nhập 14–16 tháng/năm.") == ""
    assert extract_salary_range("Lương: 20–30 triệu/tháng") == "Lương: 20–30 triệu/tháng"
    assert freshness_criterion({"first_seen_at": datetime.now(timezone.utc).isoformat()}) == {
        "score": 5, "reason": "Posting date not provided; job freshness is unknown."}
    published = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert freshness_criterion({"published_at": published})["score"] == 8

    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    job_id = client.post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
        "description": description}).json()["id"]
    criteria = {name: {"score": 6, "reason": "Prior local result"}
                for name in MatchJudgment.model_fields if name != "summary"}
    criteria["freshness"] = {"score": 10, "reason": "Candidate experience is fresh."}
    detail = {"method": "local_llm", "facts": {"languages": ["Python", "Anomaly detection", "English"]},
              "criteria": criteria, "excluded_role": None}
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=64,score_detail=? WHERE id=?",
                         (json.dumps(detail), job_id))
    assert clean_saved_analysis(app.state.db) == 1
    corrected = app.state.db.one("SELECT score,score_detail FROM vacancies WHERE id=?", (job_id,))
    corrected_detail = json.loads(corrected["score_detail"])
    assert corrected_detail["facts"]["languages"] == ["English"]
    assert corrected_detail["facts"]["salary_range"] == "Salary: VND 18–30 million per month."
    assert corrected_detail["criteria"]["freshness"]["score"] == 5
    assert corrected_detail["criteria"]["experience"]["score"] == 5
    assert corrected["score"] == 56
    assert clean_saved_analysis(app.state.db) == 0


def test_model_setting_backfills_jobs_without_changing_drafting_provider(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    monkeypatch.setattr("job_radar.matching.validate_local_model", lambda model: model)
    monkeypatch.setattr("job_radar.matching.analyze_job", lambda *_args: (83, {
        "method": "local_llm", "facts": {"required_skills": ["Python"], "responsibilities": ["Build models"]},
        "criteria": {"role": {"score": 9, "reason": "Relevant role"}}, "explanation": "Good fit.",
    }))
    with TestClient(app) as client:
        identifier = client.post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
            "description": "Build Python models in Hanoi."}).json()["id"]
        assert client.get(f"/api/jobs/{identifier}").json()["analysis_status"] == "not_configured"
        assert client.put("/api/matching/model", json={"model": "test:small"}).status_code == 200
        for _ in range(100):
            job = client.get(f"/api/jobs/{identifier}").json()
            if job["analysis_status"] == "done":
                break
            time.sleep(.02)
        assert job["analysis_status"] == "done"
        assert job["score"] == 83
        assert json.loads(job["score_detail"])["facts"]["responsibilities"] == ["Build models"]
        assert client.get("/api/profile").json()["drafting_provider"] == ""
        assert client.get("/api/setup").json()["matching"]["completed"] == 1


def test_ollama_outage_keeps_jobs_queued(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.execute("UPDATE sources SET enabled=0")
    monkeypatch.setattr("job_radar.matching.validate_local_model", lambda model: model)
    def unavailable(*_args):
        raise LocalModelUnavailable("Ollama is offline")
    monkeypatch.setattr("job_radar.matching.analyze_job", unavailable)
    with TestClient(app) as client:
        identifier = client.post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
            "description": "Build Python models in Hanoi."}).json()["id"]
        client.put("/api/matching/model", json={"model": "test:small"})
        for _ in range(100):
            if client.get("/api/setup").json()["matching"]["service_error"]:
                break
            time.sleep(.02)
        assert client.get(f"/api/jobs/{identifier}").json()["analysis_status"] == "pending"
        assert client.get("/api/setup").json()["matching"]["failed"] == 0


def test_unchanged_rescan_preserves_local_score(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("matching_model", "test:small")
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    observed = ObservedJob("https://example.org/job", "AI Engineer", "Example", "Build Python models.", location="Hanoi")
    identifier, _ = ingest(db, source, observed)
    db.execute("UPDATE vacancies SET score=88,score_detail=?,analysis_status='done',analysis_model='test:small' WHERE id=?",
               (json.dumps({"method": "local_llm"}), identifier))
    assert ingest(db, source, observed) == (identifier, False)
    row = db.one("SELECT score,analysis_status FROM vacancies WHERE id=?", (identifier,))
    assert row == {"score": 88, "analysis_status": "done"}


def test_newly_discovered_posting_date_requeues_local_score(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("matching_model", "test:small")
    source = db.one("SELECT id FROM sources LIMIT 1")["id"]
    observed = ObservedJob("https://example.org/dates", "AI Engineer", "Example", "Build Python models.")
    identifier, _ = ingest(db, source, observed)
    db.execute("UPDATE vacancies SET analysis_status='done',analysis_model='test:small',score=80 WHERE id=?", (identifier,))
    dated = ObservedJob(observed.url, observed.title, observed.company, observed.description,
                        published_at="2026-10-01T12:00:00+00:00")
    ingest(db, source, dated)
    refreshed = db.one("SELECT analysis_status,published_at FROM vacancies WHERE id=?", (identifier,))
    assert refreshed["published_at"] == dated.published_at
    assert refreshed["analysis_status"] == "pending"


def test_negative_role_is_excluded_after_model_scoring(monkeypatch) -> None:
    def generate(_model, _prompt, result_type):
        if result_type is JobFacts:
            return JobFacts(role="Sales Manager", seniority="", required_skills=[], preferred_skills=[],
                            years_required=None, location="", work_mode="", responsibilities=[],
                            education=[], languages=[], summary="")
        return MatchJudgment(**{name: Criterion(score=10, reason="Match")
                                for name in MatchJudgment.model_fields if name != "summary"}, summary="Strong fit")

    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    score, detail = analyze_job({"title": "Sales Manager", "description": "AI and Python"}, {}, [], "test:small")
    assert score == 0
    assert detail["excluded_role"] == "sales"


def test_model_picker_excludes_embedding_only_models(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(create_app(Settings(tmp_path)))
    monkeypatch.setattr("job_radar.web.list_local_models", lambda: [
        {"name": "bge-m3:latest", "size": 100}, {"name": "small:latest", "size": 200},
    ])
    def validate(model):
        if model == "bge-m3:latest":
            raise ValueError("embedding only")
        return model
    monkeypatch.setattr("job_radar.web.validate_local_model", validate)
    response = client.get("/api/matching/models")
    assert response.status_code == 200
    assert response.json()["models"] == [{"name": "small:latest", "size": 200}]


def test_cloud_tag_is_rejected_for_local_matching() -> None:
    try:
        validate_local_model("example:cloud")
    except ValueError as error:
        assert "this Mac" in str(error)
    else:
        assert False, "Cloud model should not be accepted for local matching"


def test_truncated_local_model_json_retries_with_more_output_space(monkeypatch) -> None:
    budgets = []
    facts = JobFacts(role="Engineer", seniority="", required_skills=["Python"],
        preferred_skills=[], years_required=None, location="", work_mode="",
        responsibilities=["Build services"], education=[], languages=[], summary="")
    def handler(request: httpx.Request) -> httpx.Response:
        budgets.append(json.loads(request.content)["options"]["num_predict"])
        if len(budgets) == 1:
            return httpx.Response(200, json={"response": '{"role":"Engineer",', "done_reason": "length"})
        return httpx.Response(200, json={"response": facts.model_dump_json(), "done_reason": "stop"})
    client_type = httpx.Client
    monkeypatch.setattr("job_radar.local_analysis.httpx.Client",
                        lambda **kwargs: client_type(transport=httpx.MockTransport(handler)))
    assert _generate("test:small", "Extract facts", JobFacts) == facts
    assert budgets[0] == 1400
    assert budgets[1] > budgets[0]


def test_job_fact_output_is_bounded_for_small_local_models(monkeypatch) -> None:
    schema = JobFacts.model_json_schema()
    assert schema["properties"]["responsibilities"]["maxItems"] <= 6
    assert schema["properties"]["summary"]["maxLength"] <= 250
    prompts = []
    def generate(_model, prompt, result_type):
        prompts.append(prompt)
        if result_type is JobFacts:
            return JobFacts(role="Engineer", seniority="", required_skills=[], preferred_skills=[],
                years_required=None, location="", work_mode="", responsibilities=[],
                education=[], languages=[], summary="")
        return MatchJudgment(**{name: Criterion(score=5, reason="Neutral")
            for name in MatchJudgment.model_fields if name != "summary"}, summary="Neutral fit")
    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    analyze_job({"title": "Engineer", "description": "Build Python systems."}, {}, [], "test:small")
    assert "Do not quote the posting" in prompts[0]
    assert "at most 6 responsibilities" in prompts[0]


def test_failed_job_analyses_are_listed_and_can_be_retried_together(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    app.state.db.set_setting("matching_model", "test:small")
    ids = [client.post("/api/jobs/import", json={"company": "Example", "title": f"Engineer {number}",
        "description": "Build Python services."}).json()["id"] for number in (1, 2)]
    for identifier in ids:
        app.state.db.execute("UPDATE vacancies SET analysis_status='failed',analysis_error=? WHERE id=?",
                             ("Model output was cut off", identifier))
    failures = client.get("/api/matching/failures")
    assert failures.status_code == 200
    assert {item["id"] for item in failures.json()} == set(ids)
    assert all(item["error"] == "Model output was cut off" for item in failures.json())
    single = client.post(f"/api/jobs/{ids[0]}/analyze")
    assert single.status_code == 202
    retried = client.post("/api/matching/retry-failed")
    assert retried.status_code == 202
    assert retried.json()["queued"] == 1
    assert client.get("/api/matching/failures").json() == []


def test_failed_analysis_can_be_dismissed_without_deleting_the_job(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    app.state.db.set_setting("matching_model", "test:small")
    job_id = client.post("/api/jobs/import", json={"company": "Example", "title": "Engineer",
        "description": "Build Python services."}).json()["id"]
    app.state.db.execute("UPDATE vacancies SET analysis_status='failed',analysis_error='Invalid local JSON' WHERE id=?", (job_id,))
    response = client.post(f"/api/jobs/{job_id}/dismiss-analysis")
    assert response.status_code == 200
    assert client.get("/api/matching/failures").json() == []
    assert client.get("/api/queue").json()["analysis"]["failed"] == []
    assert client.get(f"/api/jobs/{job_id}").json()["analysis_status"] == "dismissed"
    assert app.state.match_manager.status()["failed"] == 0
    app.state.match_manager.invalidate_all()
    assert client.get(f"/api/jobs/{job_id}").json()["analysis_status"] == "dismissed"
    app.state.db.set_setting("profile", {"name": "New name", "skills": ["Python"]})
    from job_radar.ranking import rescore_vacancies
    rescore_vacancies(app.state.db, app.state.db.get_setting("profile"))
    assert client.get(f"/api/jobs/{job_id}").json()["analysis_status"] == "dismissed"
    assert client.post(f"/api/jobs/{job_id}/analyze").status_code == 202
    assert client.get(f"/api/jobs/{job_id}").json()["analysis_status"] == "pending"


def test_match_policy_uses_explicit_preferences_for_hard_constraints() -> None:
    criteria = {name: {"score": 5, "reason": "Neutral"}
                for name in MatchJudgment.model_fields if name != "summary"}
    base = {"title": "AI Engineer", "company": "Example", "description": "Build AI models", "location": "Hanoi"}
    facts = {"years_required": 3, "location": "Hanoi", "work_mode": "Onsite", "salary_range": ""}
    profile = {"experience": [{"dates": "Jan 2026 – Jun 2026"}]}

    score, graded, exclusions = finalize_match(base, facts, profile, criteria)
    assert score > 0
    assert not exclusions
    assert graded["location"]["score"] == 5
    assert graded["experience"]["score"] < 10

    role_boost = {**criteria, "role": {"score": 10, "reason": "Match"}}
    fresh_boost = {**criteria, "freshness": {"score": 10, "reason": "Recent"}}
    assert finalize_match(base, facts, profile, role_boost)[0] - score > \
        finalize_match(base, facts, profile, fresh_boost)[0] - score

    # Mid/senior roles and 3+ years are stretch evidence by default, never implicit rejections.
    senior = {**base, "title": "Senior AI Engineer"}
    assert finalize_match(senior, {**facts, "seniority": "Senior"}, profile, criteria)[0] > 0
    experienced = {**base, "description": "Requires 5 years of experience in AI"}
    assert finalize_match(experienced, {**facts, "years_required": 5}, profile, criteria)[0] > 0

    preferences = {
        "role_families": ["AI Engineer"],
        "seniority_levels": ["entry"],
        "preferred_locations": ["Hanoi"],
        "work_modes": ["remote"],
        "excluded_employers": ["Blocked Co"],
        "hard_constraints": {"seniority": True, "location": True, "work_mode": True, "employer": True},
    }
    score, _, reasons = finalize_match(senior, {**facts, "seniority": "Senior"}, profile, criteria, preferences)
    assert score == 0
    assert any("Seniority" in reason for reason in reasons)

    elsewhere = {**base, "location": "Ho Chi Minh City"}
    score, _, reasons = finalize_match(elsewhere, {**facts, "location": "Ho Chi Minh City"}, profile, criteria, preferences)
    assert score == 0
    assert any("Location" in reason for reason in reasons)

    blocked = {**base, "company": "Blocked Co"}
    score, _, reasons = finalize_match(blocked, facts, profile, criteria, preferences)
    assert score == 0
    assert any("Employer" in reason for reason in reasons)

    assert extract_years_required("2 years in Python; 4 years building ML systems") == 4
    assert extract_years_required("Company has 10 years of history. Requires 2 years of ML experience.") == 2


def test_saved_match_uses_structured_location_for_hard_exclusion(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    app.state.db.set_setting("search_intent", {
        "preferred_locations": ["Hanoi"],
        "hard_constraints": {"location": True},
    })
    client = TestClient(app)
    job_id = client.post("/api/jobs/import", json={"company": "Example", "title": "AI Engineer",
        "description": "Build Python models on site.", "location": "Ho Chi Minh City"}).json()["id"]
    criteria = {name: {"score": 9, "reason": "Earlier match"}
                for name in MatchJudgment.model_fields if name != "summary"}
    detail = {"method": "local_llm", "facts": {"location": "", "years_required": None},
              "criteria": criteria, "explanation": "Looks good."}
    app.state.db.execute("UPDATE vacancies SET analysis_status='done',score=90,score_detail=? WHERE id=?",
        (json.dumps(detail), job_id))
    assert clean_saved_analysis(app.state.db) == 1
    updated = client.get(f"/api/jobs/{job_id}").json()
    assert updated["score"] == 0
    assert "Location" in " ".join(json.loads(updated["score_detail"])["hard_exclusions"])
