from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .db import Database, now
from .search_intent import extract_required_years, location_matches_preference, normalize_search_intent, posting_salary_floor, seniority_key
import json


ROLE_WORDS = ("ai", "machine learning", "research", "llm", "language model", "computer vision", "data scientist", "robotics", "perception", "generative")
NEGATIVE_WORDS = ("sales", "recruiter", "accountant", "driver", "customer service", "telesales")


def score_job(job: dict[str, Any], profile: dict[str, Any], preferences: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    prefs = normalize_search_intent(preferences)
    title = (job.get("title") or "").casefold()
    description = (job.get("description") or "").casefold()
    location = (job.get("location") or "").casefold()
    role_matches = [word for word in ROLE_WORDS if word in title]
    role = 25 if role_matches else (10 if any(word in description for word in ROLE_WORDS) else 0)
    selected_families = [item.casefold() for item in prefs["role_families"]]
    family_match = not selected_families or any(item in title or item in description for item in selected_families)
    level = seniority_key(job.get("title") or "")
    selected_levels = set(prefs["seniority_levels"])
    if selected_families:
        role = max(role, 25) if family_match else min(role, 5)
    negative_role = next((word for word in prefs["negative_keywords"] if word.casefold() in title), None)
    if negative_role:
        role = min(role, 5)

    skills = [str(skill).strip() for skill in profile.get("skills", []) if str(skill).strip()]
    matched_skills = [skill for skill in skills if skill.casefold() in f"{title} {description}"]
    skill = min(25, round(25 * len(matched_skills) / max(1, min(len(skills), 5)))) if skills else 12

    years_required = extract_required_years(f"{job.get('title') or ''}\n{job.get('description') or ''}")
    experience = 15 if years_required is None else max(8, 20 - max(0, years_required - 2) * 2)

    research = 15 if any(word in f"{title} {description}" for word in ("research", "nghiên cứu", "agent", "model development")) else 8
    preferred_locations = [item.casefold() for item in prefs["preferred_locations"]]
    preferred_modes = [item.casefold() for item in prefs["work_modes"]]
    remote = any(word in f"{location} {(job.get('work_mode') or '').casefold()} {description}" for word in ("remote", "wfh", "work from home", "làm việc từ xa"))
    location_match = location_matches_preference(location, prefs["preferred_locations"])
    if not preferred_locations:
        local = 5
    elif not location:
        local = 5
    else:
        local = 10 if location_match else 3
    if preferred_modes and remote and any(item in {"remote", "wfh", "work from home"} for item in preferred_modes):
        local = max(local, 8)

    published = job.get("published_at") or job.get("first_seen_at")
    freshness = 3
    if published:
        try:
            published_time = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if published_time.tzinfo is None:
                published_time = published_time.replace(tzinfo=timezone.utc)
            age_hours = max(0, (datetime.now(timezone.utc) - published_time).total_seconds() / 3600)
            freshness = 5 if age_hours <= 24 else 4 if age_hours <= 72 else 2 if age_hours <= 336 else 0
        except ValueError:
            pass
    components = {"role": role, "skills": skill, "experience": experience, "research": research, "location": local, "freshness": freshness}
    explanation = (
        f"Role terms: {', '.join(role_matches) if role_matches else 'no direct title match'}. "
        f"Matched profile skills: {', '.join(matched_skills[:6]) if matched_skills else 'not yet identified'}. "
        f"Experience: {years_required if years_required is not None else 'not stated'} years stated; not treated as an automatic rejection."
    )
    if negative_role:
        explanation = f"Excluded role term in title: {negative_role}. " + explanation
    score = min(100, sum(components.values()))
    hard = prefs["hard_constraints"]
    exclusions = []
    maximum_years = prefs.get("max_required_experience_years")
    if years_required is not None and maximum_years is not None and years_required > maximum_years:
        exclusions.append(
            f"Experience requirement asks for {years_required} years; your search includes jobs requiring up to {maximum_years} years."
        )
    if hard.get("role_family") and selected_families and not family_match:
        exclusions.append("Role family is outside your explicit search limits.")
    if hard.get("seniority") and selected_levels and level and level not in selected_levels:
        exclusions.append("Seniority is outside your explicit search limits.")
    if hard.get("location") and prefs["preferred_locations"] and location and not remote and not location_match:
        exclusions.append("Location is outside your search area.")
    company = str(job.get("company") or "")
    preferred_employers = [item.casefold() for item in prefs["preferred_employers"]]
    excluded_employers = [item.casefold() for item in prefs["excluded_employers"]]
    if preferred_employers and any(item in company.casefold() for item in preferred_employers):
        score = min(100, score + 3)
    if excluded_employers and any(item in company.casefold() for item in excluded_employers):
        if hard.get("employer"):
            exclusions.append("Employer is on your excluded list.")
        else:
            score = max(0, score - 12)
    detected_mode = "remote" if remote else "hybrid" if "hybrid" in description else "onsite" if re.search(r"\bon[- ]?site\b|\bonsite\b", description) else ""
    preferred_modes = {item.casefold().replace("-", "").replace(" ", "") for item in prefs["work_modes"]}
    if hard.get("work_mode") and preferred_modes and detected_mode and detected_mode.replace("-", "") not in preferred_modes:
        exclusions.append("Work mode is outside your explicit search limits.")
    if prefs.get("minimum_salary") is not None and hard.get("minimum_salary"):
        pay = posting_salary_floor(f"{job.get('title') or ''}\n{job.get('description') or ''}", prefs.get("salary_currency"))
        if pay is not None and pay < prefs["minimum_salary"]:
            exclusions.append("Salary is below your explicit minimum.")
        elif pay is None and not prefs.get("salary_unknown_ok", True):
            exclusions.append("Salary is not stated or uses another currency, and your search requires known salary.")
    if exclusions:
        score = 0
    return score, {"method": "rules", "components": components, "matched_skills": matched_skills,
                   "years_required": years_required, "explanation": explanation, "excluded_role": negative_role,
                   "hard_exclusions": exclusions}


def rescore_vacancies(db: Database, profile: dict[str, Any], preferences: dict[str, Any] | None = None) -> None:
    pending = bool(db.get_setting("matching_model"))
    with db.connection() as conn:
        rows = conn.execute("SELECT id,company,title,description,location,published_at,first_seen_at FROM vacancies").fetchall()
        for row in rows:
            score, detail = score_job(dict(row), profile, preferences)
            conn.execute("UPDATE vacancies SET score=?,score_detail=?,analysis_status=?,analysis_error=NULL,updated_at=? WHERE id=? AND analysis_status!='dismissed'",
                         (score, json.dumps(detail, ensure_ascii=False), "pending" if pending else "not_configured", now(), row["id"]))
