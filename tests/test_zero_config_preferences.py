import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from job_radar.local_analysis import MatchJudgment, finalize_match
from job_radar.search_intent import (
    apply_auto_search_intent,
    extract_required_years,
    infer_search_intent,
)
from job_radar.settings import Settings
from job_radar.web import create_app


AS_OF = datetime(2026, 10, 6, tzinfo=timezone.utc)


def _profile(*, location: str = "Hanoi", dates: str = "Jan 2026 – Jun 2026") -> dict:
    return {
        "name": "Alex",
        "email": "alex@example.org",
        "skills": ["Python", "Machine Learning"],
        "location": location,
        "experience": [
            {
                "company": "Example",
                "role": "AI Engineering Intern",
                "dates": dates,
                "bullets": ["Built ML systems"],
            }
        ],
        "education": [],
    }


def _criteria() -> dict:
    return {
        name: {"score": 7, "reason": "Reasonable fit"}
        for name in MatchJudgment.model_fields
        if name != "summary"
    }


def test_auto_experience_limit_rounds_up_then_adds_one_year() -> None:
    half_year = infer_search_intent(_profile(), as_of=AS_OF)
    assert half_year["documented_experience_years"] == 0.5
    assert half_year["max_required_experience_years"] == 2
    assert half_year["role_families"] == ["AI Engineer"]
    assert half_year["seniority_levels"] == ["intern"]

    two_and_half = infer_search_intent(
        _profile(dates="Jan 2024 – Jun 2026"),
        as_of=AS_OF,
    )
    assert two_and_half["documented_experience_years"] == 2.5
    assert two_and_half["max_required_experience_years"] == 4


def test_required_experience_ranges_use_the_minimum_explicit_requirement() -> None:
    assert extract_required_years("2+ years of Python") == 2
    assert extract_required_years("3–5 years of ML experience") == 3
    assert extract_required_years("At least 4 years building production systems") == 4
    assert extract_required_years("Company has 10 years of history. Requires 2 years of ML experience.") == 2
    assert extract_required_years("Experience with Python is preferred") is None


def test_auto_location_and_experience_are_conservative_hard_gates() -> None:
    profile = _profile()
    preferences = apply_auto_search_intent({}, profile, as_of=AS_OF)
    assert preferences["preference_modes"]["preferred_locations"] == "auto"
    assert preferences["preferred_locations"] == ["Hanoi"]
    assert preferences["max_required_experience_years"] == 2

    onsite_hcm = {
        "title": "AI Engineer",
        "company": "Example",
        "description": "Work on site in Ho Chi Minh City. Requires 2 years of experience.",
        "location": "Ho Chi Minh City",
        "work_mode": "Onsite",
    }
    score, _, exclusions = finalize_match(
        onsite_hcm,
        {"years_required": 2, "location": "Ho Chi Minh City", "work_mode": "Onsite", "salary_range": ""},
        profile,
        _criteria(),
        preferences,
    )
    assert score == 0
    assert any("Location" in reason for reason in exclusions)

    remote_hcm = {**onsite_hcm, "description": "Fully remote. Requires 2 years of experience.", "work_mode": "Remote"}
    score, _, exclusions = finalize_match(
        remote_hcm,
        {"years_required": 2, "location": "Ho Chi Minh City", "work_mode": "Remote", "salary_range": ""},
        profile,
        _criteria(),
        preferences,
    )
    assert score > 0
    assert not any("Location" in reason for reason in exclusions)

    too_experienced = {
        "title": "AI Engineer",
        "company": "Example",
        "description": "Hanoi. Requires 3–5 years of experience.",
        "location": "Hanoi",
    }
    score, _, exclusions = finalize_match(
        too_experienced,
        {"years_required": 3, "location": "Hanoi", "work_mode": "Onsite", "salary_range": ""},
        profile,
        _criteria(),
        preferences,
    )
    assert score == 0
    assert any("Experience requirement" in reason for reason in exclusions)

    unknown = {**too_experienced, "description": "Hanoi. Experience with production ML is preferred."}
    score, _, exclusions = finalize_match(
        unknown,
        {"years_required": None, "location": "Hanoi", "work_mode": "Onsite", "salary_range": ""},
        profile,
        _criteria(),
        preferences,
    )
    assert score > 0
    assert not any("Experience requirement" in reason for reason in exclusions)


def test_custom_overrides_survive_profile_changes_and_can_reset_to_auto(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)

    assert client.put("/api/profile", json=_profile()).status_code == 200
    automatic = client.get("/api/search-intent").json()
    assert automatic["preferred_locations"] == ["Hanoi"]
    assert automatic["max_required_experience_years"] == 2
    assert automatic["preference_modes"]["preferred_locations"] == "auto"

    response = client.put("/api/search-intent", json={
        "preferred_locations": ["Hanoi", "Remote"],
        "max_required_experience_years": 5,
        "strong_match_threshold": 80,
        "preference_modes": {
            "preferred_locations": "custom",
            "max_required_experience_years": "custom",
            "strong_match_threshold": "auto",
        },
    })
    assert response.status_code == 200
    assert response.json()["preference_modes"]["preferred_locations"] == "custom"

    changed = _profile(location="Ho Chi Minh City", dates="Jan 2024 – Jun 2026")
    assert client.put("/api/profile", json=changed).status_code == 200
    preserved = client.get("/api/search-intent").json()
    assert preserved["preferred_locations"] == ["Hanoi", "Remote"]
    assert preserved["max_required_experience_years"] == 5
    assert preserved["inferred"]["preferred_locations"] == ["Ho Chi Minh City"]
    assert preserved["inferred"]["max_required_experience_years"] == 4

    reset_location = client.post("/api/search-intent/reset/preferred_locations")
    assert reset_location.status_code == 200
    assert reset_location.json()["preferred_locations"] == ["Ho Chi Minh City"]
    assert reset_location.json()["preference_modes"]["preferred_locations"] == "auto"

    reset_experience = client.post("/api/search-intent/reset/max_required_experience_years")
    assert reset_experience.status_code == 200
    assert reset_experience.json()["max_required_experience_years"] == 4
    assert reset_experience.json()["preference_modes"]["max_required_experience_years"] == "auto"


def test_legacy_explicit_preferences_migrate_to_custom_while_unset_fields_become_auto(tmp_path: Path) -> None:
    settings = Settings(tmp_path)
    app = create_app(settings)
    app.state.db.set_setting("profile", _profile())
    app.state.db.set_setting("search_intent", {
        "preferred_locations": ["Hanoi"],
        "strong_match_threshold": 91,
    })

    migrated = TestClient(create_app(settings)).get("/api/search-intent").json()
    assert migrated["preference_modes"]["preferred_locations"] == "custom"
    assert migrated["preference_modes"]["strong_match_threshold"] == "custom"
    assert migrated["preference_modes"]["max_required_experience_years"] == "auto"
    assert migrated["max_required_experience_years"] == 2


def test_outside_search_jobs_stay_stored_but_leave_the_normal_scope(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path))
    client = TestClient(app)
    client.put("/api/profile", json=_profile())

    outside_id = client.post("/api/jobs/import", json={
        "company": "Outside Co",
        "title": "AI Engineer",
        "description": "On-site in Ho Chi Minh City. Requires 4 years of experience.",
        "location": "Ho Chi Minh City",
    }).json()["id"]
    eligible_id = client.post("/api/jobs/import", json={
        "company": "Inside Co",
        "title": "AI Engineer",
        "description": "Hanoi. Experience with Python is preferred.",
        "location": "Hanoi",
    }).json()["id"]

    outside = client.get(f"/api/jobs/{outside_id}").json()
    detail = json.loads(outside["score_detail"])
    assert detail["hard_exclusions"]
    assert client.get(f"/api/jobs/{outside_id}").status_code == 200

    normal_ids = {item["id"] for item in client.get("/api/jobs/page?fit=eligible").json()["items"]}
    outside_ids = {item["id"] for item in client.get("/api/jobs/page?fit=outside").json()["items"]}
    assert eligible_id in normal_ids
    assert outside_id not in normal_ids
    assert outside_id in outside_ids


def test_settings_ui_is_summary_first_with_explicit_auto_custom_controls() -> None:
    static = Path(__file__).parents[1] / "job_radar" / "static"
    html = (static / "index.html").read_text()
    js = (static / "app.js").read_text()

    assert 'id="search-intent-summary"' in html
    assert 'id="search-intent-advanced"' in html
    assert 'name="max_required_experience_years"' in html
    assert 'name="auto_preferred_locations" type="checkbox" role="switch"' in html
    assert 'name="hard_experience" type="checkbox" role="switch"' in html
    assert 'id="job-fit"' in html and "Outside search" in html
    assert "preference_modes" in js
    assert "auto_${name}" in js
    assert "Jobs outside your search are still available in Jobs" in js


def test_auto_value_and_hard_constraint_are_independent() -> None:
    intent = apply_auto_search_intent({
        "preference_modes": {"role_families": "auto", "max_required_experience_years": "auto"},
        "hard_constraints": {"role_family": True, "experience": False},
    }, _profile(), as_of=AS_OF)
    assert intent["role_families"] == ["AI Engineer"]
    assert intent["hard_constraints"]["role_family"] is True
    assert intent["max_required_experience_years"] == 2
    assert intent["hard_constraints"]["experience"] is False
    score, _, exclusions = finalize_match(
        {"title": "AI Engineer", "company": "Example", "description": "Requires 5 years of experience."},
        {"years_required": 5}, _profile(), _criteria(), intent,
    )
    assert score > 0
    assert not any("Experience requirement" in reason for reason in exclusions)
