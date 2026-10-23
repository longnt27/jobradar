import json

from fastapi.testclient import TestClient

from job_radar.job_pool import due_date_from_posting, neutralize_facts, save_job_facts
from job_radar.job_inbox import auto_shortlist_strong_matches
from job_radar.local_analysis import JobFacts, extract_job_facts
from job_radar.settings import Settings
from job_radar.user_scoring import rescore_cached_jobs, score_job_for_user
from job_radar.web import create_app


def test_multiplicative_preferred_and_strict_factors() -> None:
    job = {"title": "Data Analyst", "company": "Acme", "description": "Build dashboards"}
    facts = {"role": "Data Analyst", "role_family_key": "Data Analyst", "location": "Hanoi",
             "work_mode": "remote", "work_mode_key": "remote", "required_skills": ["Python", "SQL"],
             "preferred_skills": [], "salary_range": "20–30 million VND"}
    profile = {"skills": ["Python", "SQL"]}
    prefs = {"role_families": ["AI Engineer"], "preferred_locations": ["Hanoi"],
             "work_modes": ["hybrid"], "minimum_salary": 40_000_000,
             "hard_constraints": {"role_family": False, "work_mode": False, "minimum_salary": False}}
    score, confidence, detail = score_job_for_user(job, facts, profile, prefs)
    assert score == 19  # .5 role × .5 mode × .75 salary × full location and skills
    assert confidence == 85  # Preferred skills were not stated.
    assert detail["factors"]["minimum_salary"]["factor"] == .75
    facts["salary_range"] = "10 million VND"
    assert score_job_for_user(job, facts, profile, prefs)[2]["factors"]["minimum_salary"]["factor"] == .25
    facts["salary_range"] = "20–30 million VND"
    prefs["hard_constraints"]["role_family"] = True
    assert score_job_for_user(job, facts, profile, prefs)[0] == 0


def test_half_points_round_up() -> None:
    prefs = {"role_families": ["AI Engineer"], "preferred_locations": ["Hanoi"],
             "work_modes": ["hybrid"]}
    score, _, _ = score_job_for_user(
        {"title": "Data Analyst", "company": "Acme", "description": "Dashboards"},
        {"role": "Data Analyst", "role_family_key": "Data Analyst", "location": "Da Nang",
         "work_mode": "remote", "work_mode_key": "remote"}, {}, prefs)
    assert score == 13  # 100 × .5 × .5 × .5 = 12.5


def test_unknown_facts_keep_factor_one_and_reduce_confidence() -> None:
    prefs = {"preferred_locations": ["Hanoi"], "minimum_salary": 30_000_000,
             "hard_constraints": {"location": True, "minimum_salary": True}}
    score, confidence, detail = score_job_for_user(
        {"title": "Engineer", "company": "Acme", "description": "Build systems"},
        {"role": "Engineer", "location": "", "salary_range": ""}, {}, prefs)
    assert score == 100
    assert confidence == 70
    assert detail["factors"]["location"]["factor"] == 1
    assert detail["factors"]["minimum_salary"]["factor"] == 1
    assert set(detail["missing_evidence"]) == {"location", "salary"}


def test_remote_preference_and_country_only_location() -> None:
    prefs = {"preferred_locations": ["Hanoi", "Remote"], "hard_constraints": {"location": True}}
    remote = {"location": "Ho Chi Minh City", "work_mode": "Remote"}
    assert score_job_for_user({}, remote, {}, prefs)[2]["factors"]["location"]["factor"] == 1
    unknown = {"location": "Vietnam", "work_mode": ""}
    score, confidence, detail = score_job_for_user({}, unknown, {}, prefs)
    assert score == 100 and confidence == 85
    assert detail["factors"]["location"]["available"] is False


def test_deadline_requires_an_application_label() -> None:
    assert due_date_from_posting("Company founded in 2020. Deadline: Dec 31, 2026.") == "2026-12-31"
    assert due_date_from_posting("Company founded in 2020. Next event Dec 31, 2026.") == ""


def test_parser_receives_posting_only(monkeypatch) -> None:
    calls = []
    def fake_generate(model, prompt, schema):
        calls.append((model, prompt, schema))
        return JobFacts(role="AI Engineer", seniority="", required_skills=["Python"],
                        preferred_skills=[], years_required=None, location="", work_mode="",
                        responsibilities=[], education=[], languages=[], summary="")
    monkeypatch.setattr("job_radar.local_analysis._generate", fake_generate)
    facts = extract_job_facts({"title": "AI Engineer", "description": "Python is required."}, "parser:test")
    assert facts["required_skills"] == ["Python"]
    assert len(calls) == 1 and calls[0][2] is JobFacts
    assert "candidate fit" in calls[0][1] and "profile" not in calls[0][1].casefold()


def test_skills_use_iou_and_rescoring_does_not_reparse(tmp_path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    job = client.post("/api/jobs/import", json={
        "title": "AI Engineer", "company": "Acme", "description": "Python and SQL required.",
    }).json()
    row = db.one("SELECT * FROM vacancies WHERE id=?", (job["id"],))
    facts = neutralize_facts(db, row, {"role": "AI Engineer", "company": "Acme",
        "required_skills": ["Python", "SQL"], "preferred_skills": [], "location": "",
        "work_mode": "", "years_required": None, "salary_range": "", "summary": ""})
    with db.connection() as conn:
        save_job_facts(conn, row, facts, "test:parser")
        conn.execute("UPDATE vacancies SET analysis_status='done' WHERE id=?", (job["id"],))
    db.set_setting("profile", {"skills": ["Python", "Go"]})
    prefs = {"hard_constraints": {"required_skills": True}}
    assert rescore_cached_jobs(db, {"skills": ["Python", "Go"]}, prefs) == 1
    saved = db.one("SELECT score,confidence,detail FROM user_job_scores WHERE vacancy_id=?", (job["id"],))
    assert saved["score"] == 33  # Python / {Python, SQL, Go}
    assert saved["confidence"] == 85  # Preferred skills were not stated.
    assert rescore_cached_jobs(db, {"skills": ["Python", "Go"]}, prefs) == 0
    pooled = client.get(f"/api/job-pool/jobs/{job['id']}").json()
    assert pooled["facts"]["required_skills"] == ["Python", "SQL"]
    assert "score" not in pooled and "confidence" not in pooled
    assert client.get("/api/job-pool/jobs").json()["total"] == 1


def test_unknown_preferences_do_not_auto_shortlist(tmp_path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    db = app.state.db
    job_id = client.post("/api/jobs/import", json={
        "title": "Engineer", "company": "Acme", "description": "Build systems.",
    }).json()["id"]
    facts = {"role": "Engineer", "company": "Acme", "location": "", "salary_range": ""}
    score, confidence, detail = score_job_for_user({"title": "Engineer", "company": "Acme"}, facts, {}, {})
    assert (score, confidence) == (100, 0)
    db.execute("UPDATE vacancies SET score=?,score_detail=?,analysis_status='done' WHERE id=?",
               (score, json.dumps(detail), job_id))
    assert auto_shortlist_strong_matches(db, 80) == 0
    assert db.one("SELECT decision_state FROM vacancies WHERE id=?", (job_id,))["decision_state"] == "undecided"
