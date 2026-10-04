import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from job_radar.db import now
from job_radar.ingest import ObservedJob, ingest
from job_radar.local_analysis import Criterion, JobFacts, LocalModelUnavailable, MatchJudgment, _generate, _ground_facts, analyze_job, clean_saved_analysis, extract_salary_range, freshness_criterion, validate_local_model
from job_radar.settings import Settings
from job_radar.web import create_app


def test_local_analysis_extracts_facts_and_sums_ten_scores(monkeypatch) -> None:
    prompts = []

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
    )
    assert score == 62
    assert len(detail["criteria"]) == 10
    assert detail["facts"]["required_skills"] == ["Python"]
    assert detail["method"] == "local_llm"
    assert "ML Engineer" in prompts[1] and "Vision" in prompts[1]


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
    assert detail["criteria"]["research"]["score"] == 5
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
    assert grounded.years_required is None
    assert grounded.education == []


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
    assert corrected["score"] == 59
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


def test_reanalysis_does_not_send_old_job_alert_again(tmp_path: Path, monkeypatch) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.execute("UPDATE sources SET enabled=0")
    db.set_setting("matching_model", "test:small")
    db.set_setting("matching_model_activated_at", "2020-01-01T00:00:00+00:00")
    monkeypatch.setattr("job_radar.matching.analyze_job", lambda *_args: (88, {"method": "local_llm"}))
    alerted = []

    async def fake_notify(_db, _settings, ids):
        alerted.extend(ids)

    monkeypatch.setattr("job_radar.matching.notify_new_jobs", fake_notify)
    client = TestClient(app)
    old_id = client.post("/api/jobs/import", json={"company": "Example", "title": "Old Engineer",
        "description": "Build Python systems."}).json()["id"]
    db.execute("UPDATE vacancies SET analyzed_at=?,analysis_status='pending' WHERE id=?", (now(), old_id))
    with TestClient(app) as running:
        for _ in range(100):
            if running.get(f"/api/jobs/{old_id}").json()["analysis_status"] == "done":
                break
            time.sleep(.02)
        assert alerted == []
        new_id = running.post("/api/jobs/import", json={"company": "Example", "title": "New Engineer",
            "description": "Build Python systems."}).json()["id"]
        for _ in range(100):
            if running.get(f"/api/jobs/{new_id}").json()["analysis_status"] == "done":
                break
            time.sleep(.02)
        assert alerted == [new_id]


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


def test_negative_role_cap_is_enforced_after_model_scoring(monkeypatch) -> None:
    def generate(_model, _prompt, result_type):
        if result_type is JobFacts:
            return JobFacts(role="Sales Manager", seniority="", required_skills=[], preferred_skills=[],
                            years_required=None, location="", work_mode="", responsibilities=[],
                            education=[], languages=[], summary="")
        return MatchJudgment(**{name: Criterion(score=10, reason="Match")
                                for name in MatchJudgment.model_fields if name != "summary"}, summary="Strong fit")

    monkeypatch.setattr("job_radar.local_analysis._generate", generate)
    score, detail = analyze_job({"title": "Sales Manager", "description": "AI and Python"}, {}, [], "test:small")
    assert score == 20
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
