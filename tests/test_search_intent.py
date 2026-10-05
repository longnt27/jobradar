import json
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.search_intent import fit_summary, normalize_search_intent, seniority_key
from job_radar.settings import Settings
from job_radar.web import create_app


def test_search_intent_defaults_are_flexible_and_normalized() -> None:
    intent = normalize_search_intent({
        "role_families": ["AI Engineer", "ai engineer", ""],
        "seniority_levels": ["entry", "senior", "made-up"],
        "strong_match_threshold": 87,
        "hard_constraints": {"seniority": True},
    })
    assert intent["role_families"] == ["AI Engineer"]
    assert intent["seniority_levels"] == ["entry", "senior"]
    assert intent["strong_match_threshold"] == 87
    assert intent["hard_constraints"]["seniority"] is True
    assert intent["hard_constraints"]["location"] is False
    assert intent["salary_unknown_ok"] is True


def test_seniority_taxonomy_is_structured() -> None:
    assert seniority_key("AI Engineering Intern") == "intern"
    assert seniority_key("Junior ML Engineer") == "entry"
    assert seniority_key("Mid-level Data Scientist") == "mid"
    assert seniority_key("Senior AI Engineer") == "senior"
    assert seniority_key("Principal Engineer") == "lead_plus"
    assert seniority_key("AI Engineer") == ""


def test_fit_summary_distinguishes_strong_stretch_uncertain_and_outside() -> None:
    prefs = {"strong_match_threshold": 80}
    complete = {
        "facts": {"required_skills": ["Python"], "years_required": 2, "location": "Hanoi",
                  "work_mode": "Hybrid", "education": ["Bachelor"]},
        "criteria": {
            "role": {"score": 9, "reason": "Direct role fit"},
            "required_skills": {"score": 8, "reason": "Python is documented"},
            "experience": {"score": 7, "reason": "Experience is close"},
            "location": {"score": 10, "reason": "Location matches"},
            "work_mode": {"score": 8, "reason": "Hybrid matches"},
            "education": {"score": 8, "reason": "Degree matches"},
        },
    }
    strong = fit_summary(88, complete, prefs)
    assert strong["fit_class"] == "strong"
    assert strong["strongest_signal"]["criterion"] in {"role", "location"}
    stretch = fit_summary(68, complete, prefs)
    assert stretch["fit_class"] == "stretch"

    sparse = {"facts": {"required_skills": [], "years_required": None, "location": "", "work_mode": "", "education": []},
              "criteria": {}}
    uncertain = fit_summary(85, sparse, prefs)
    assert uncertain["fit_class"] == "uncertain"
    assert "required skills" in uncertain["missing_evidence"]

    outside = fit_summary(0, {**complete, "hard_exclusions": ["Location is outside limits"]}, prefs)
    assert outside["fit_class"] == "outside"


def test_search_intent_api_is_separate_from_application_salary_and_migrates_threshold(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    db = app.state.db
    db.set_setting("profile", {"name": "Alex", "skills": [], "salary_expectation": "45M VND"})
    db.set_setting("auto_apply", {"enabled": False, "threshold": 86})
    client = TestClient(app)

    initial = client.get("/api/search-intent").json()
    assert initial["strong_match_threshold"] == 86
    assert initial["minimum_salary"] is None
    assert client.get("/api/profile").json()["salary_expectation"] == "45M VND"

    response = client.put("/api/search-intent", json={
        "role_families": ["AI Engineer"],
        "seniority_levels": ["entry", "mid", "senior"],
        "preferred_locations": ["Hanoi"],
        "work_modes": ["remote", "hybrid"],
        "preferred_employers": ["OpenAI"],
        "excluded_employers": ["Blocked Co"],
        "negative_keywords": ["night shift"],
        "hard_constraints": {"location": False, "employer": True},
        "minimum_salary": 30000000,
        "salary_currency": "VND",
        "salary_unknown_ok": True,
        "strong_match_threshold": 88,
    })
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["minimum_salary"] == 30000000
    assert saved["strong_match_threshold"] == 88
    assert client.get("/api/profile").json()["salary_expectation"] == "45M VND"
    assert client.get("/api/auto-apply").json()["threshold"] == 88


def test_automation_toggle_keeps_shared_threshold(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    app.state.db.set_setting("search_intent", normalize_search_intent({"strong_match_threshold": 91}))
    app.state.db.set_setting("profile", {"name": "Alex", "email": "alex@example.org", "skills": [],
                                         "drafting_provider": "template",
                                         "experience": [{"company": "Co", "role": "Engineer", "dates": "2025-2026", "bullets": ["Built systems"]}]})
    # Configuration prerequisites intentionally remain incomplete for enabling; disabling still exercises persistence.
    response = client.put("/api/auto-apply", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["threshold"] == 91
    assert client.get("/api/search-intent").json()["strong_match_threshold"] == 91


def test_search_intent_ui_contract_is_unified() -> None:
    static = Path(__file__).parents[1] / "job_radar" / "static"
    html = (static / "index.html").read_text()
    js = (static / "app.js").read_text()

    assert 'id="search-intent-panel"' in html
    assert "What JobRadar is looking for" in html
    assert 'name="minimum_salary"' in html
    assert 'name="salary_expectation"' in html
    assert 'id="job-seniority"' in html
    assert '<option value="intern">Intern</option>' in html
    assert '<option value="lead_plus">Lead+</option>' in html
    assert 'name="threshold" type="number" min="0" max="100" value="80" readonly' in html
    assert "fitClassLabel" in js
    assert "Missing evidence:" in js
    assert "strong_match_threshold" in js
