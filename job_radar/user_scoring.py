"""Deterministic, user-specific scores over neutral parsed job facts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .db import Database, now
from .search_intent import (
    location_matches_preference, normalize_search_intent, role_family,
    salary_currency, seniority_key,
)


SCORE_VERSION = 3
LOCAL_USER = "local"


def signature(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def preference_signature(preferences: dict | None) -> str:
    return signature({"score_version": SCORE_VERSION,
                      "preferences": normalize_search_intent(preferences)})


def _skill(value: str) -> str:
    normalized = re.sub(r"[^\w+#.]+", " ", str(value or "").casefold()).strip()
    return {"ml": "machine learning", "ai": "artificial intelligence",
            "js": "javascript", "py": "python"}.get(normalized, normalized)


def skill_iou(job_skills: list[str], user_skills: list[str]) -> float | None:
    """Jaccard overlap of the two named skill sets; unknown when either is empty."""
    job_set = {_skill(item) for item in job_skills if _skill(item)}
    user_set = {_skill(item) for item in user_skills if _skill(item)}
    return len(job_set & user_set) / len(job_set | user_set) if job_set and user_set else None


def salary_ceiling(value: str, expected_currency: str) -> float | None:
    text = str(value or "")
    if not text or (expected_currency and salary_currency(text) != expected_currency.upper()):
        return None
    amounts = []
    for token in re.findall(r"(?<!\w)\d+(?:[.,]\d+)*", text):
        clean = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", token)
        clean = clean.replace(",", ".")
        try:
            amounts.append(float(clean))
        except ValueError:
            pass
    if not amounts:
        return None
    unit = 1_000_000 if re.search(r"\b(?:million|triệu|tr)\b|\d\s*m\b", text, re.I) else \
        1_000 if re.search(r"\d\s*k\b", text, re.I) else 1
    return max(amounts) * unit


def score_job_for_user(job: dict, facts: dict, profile: dict,
                       preferences: dict | None = None) -> tuple[int, int, dict]:
    """Multiply configured match factors. Missing posting facts multiply by one."""
    prefs = normalize_search_intent(preferences)
    hard = prefs["hard_constraints"]
    factors: dict[str, dict[str, Any]] = {}
    criteria: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    exclusions: list[str] = []

    def add(key: str, matched: bool | None, reason: str, *, partial: float | None = None,
            missing_label: str | None = None, strict_key: str | None = None) -> None:
        strict = bool(hard.get(strict_key or key, False))
        if matched is None and partial is None:
            factor, criterion_score = 1.0, 5
            if missing_label:
                missing.append(missing_label)
        elif partial is not None:
            partial = min(1.0, max(0.0, partial))
            factor = partial if strict else 0.5 + 0.5 * partial
            criterion_score = round(partial * 10)
        else:
            factor = 1.0 if matched else 0.0 if strict else 0.5
            criterion_score = 10 if matched else 0
        if strict and factor == 0:
            exclusions.append(reason)
        factors[key] = {"factor": round(factor, 6), "mode": "strict" if strict else "preferred",
                        "available": matched is not None or partial is not None, "reason": reason}
        criteria[key] = {"score": criterion_score, "reason": reason}

    role_prefs = prefs["role_families"]
    if role_prefs:
        role = str(facts.get("role") or job.get("title") or "").strip()
        family = str(facts.get("role_family_key") or role_family(role))
        matched = any(choice.casefold() in role.casefold() or family.casefold() == choice.casefold()
                      for choice in role_prefs) if role else None
        add("role_family", matched,
            f"Role {role or 'unknown'} {'matches' if matched else 'does not match' if matched is False else 'is unavailable for'} your chosen role families.",
            missing_label="role", strict_key="role_family")

    levels = prefs["seniority_levels"]
    if levels:
        level = str(facts.get("seniority_key") or seniority_key(
            f"{facts.get('seniority') or ''} {facts.get('role') or job.get('title') or ''}"))
        matched = level in levels if level else None
        add("seniority", matched,
            f"Seniority {level or 'unknown'} {'matches' if matched else 'does not match' if matched is False else 'is unavailable for'} your selected levels.",
            missing_label="seniority")

    user_skills = [str(item) for item in profile.get("skills", []) if str(item).strip()]
    for key, label in (("required_skills", "required skills"), ("preferred_skills", "preferred skills")):
        job_skills = facts.get(key) if isinstance(facts.get(key), list) else []
        if not user_skills:
            continue
        overlap = skill_iou(job_skills, user_skills)
        add(key, None, f"{label.capitalize()} overlap: {overlap:.0%} IoU." if overlap is not None
            else f"{label.capitalize()} are not stated in the posting.",
            partial=overlap, missing_label=label)

    maximum_years = prefs.get("max_required_experience_years")
    if maximum_years is not None:
        years = facts.get("years_required")
        matched = int(years) <= maximum_years if isinstance(years, int) else None
        add("experience", matched,
            f"Posting asks for {years} years; your limit is {maximum_years}." if years is not None
            else "Years of experience are not stated in the posting.", missing_label="experience")

    locations = prefs["preferred_locations"]
    if locations:
        location = str(facts.get("location") or job.get("location") or "").strip()
        if re.fullmatch(r"(?:vietnam|việt nam|anywhere|unspecified|unknown|multiple locations|search by location)",
                        location, re.I):
            location = ""
        remote = bool(re.search(r"\b(?:remote|wfh|work from home)\b",
                                str(facts.get("work_mode") or job.get("work_mode") or ""), re.I))
        remote_wanted = "remote" in {item.casefold() for item in locations}
        matched = True if remote and remote_wanted else location_matches_preference(location, locations) if location else None
        add("location", matched,
            f"Location {location or 'unknown'} {'matches' if matched else 'does not match' if matched is False else 'is unavailable for'} your preferred locations.",
            missing_label="location")

    modes = prefs["work_modes"]
    if modes:
        mode = str(facts.get("work_mode") or job.get("work_mode") or "").strip()
        normalized = str(facts.get("work_mode_key") or re.sub(r"[\s-]+", "", mode.casefold()))
        matched = normalized in {re.sub(r"[\s-]+", "", item.casefold()) for item in modes} if normalized else None
        add("work_mode", matched,
            f"Work mode {mode or 'unknown'} {'matches' if matched else 'does not match' if matched is False else 'is unavailable for'} your preferences.",
            missing_label="work mode")

    minimum = prefs.get("minimum_salary")
    if minimum is not None and minimum > 0:
        ceiling = salary_ceiling(str(facts.get("salary_range") or ""), prefs["salary_currency"])
        if ceiling is None:
            add("minimum_salary", None, "Salary is not stated in the expected currency.", missing_label="salary")
        elif ceiling >= minimum:
            add("minimum_salary", True, f"Salary ceiling {ceiling:,.0f} meets your minimum {minimum:,.0f}.")
        else:
            ratio = max(0.0, ceiling / minimum)
            strict = bool(hard.get("minimum_salary"))
            factor = 0.0 if strict else ratio
            reason = f"Salary ceiling {ceiling:,.0f} is below your minimum {minimum:,.0f}."
            factors["minimum_salary"] = {"factor": round(factor, 6), "mode": "strict" if strict else "preferred",
                                         "available": True, "reason": reason}
            criteria["minimum_salary"] = {"score": round(ratio * 10), "reason": reason}
            if strict:
                exclusions.append(reason)

    company = str(facts.get("company") or job.get("company") or "").strip()
    if prefs["preferred_employers"]:
        matched = any(item.casefold() in company.casefold() for item in prefs["preferred_employers"]) if company else None
        add("preferred_employers", matched,
            f"Employer {company or 'unknown'} {'is' if matched else 'is not' if matched is False else 'cannot be checked against'} a preferred employer.",
            missing_label="employer")
    if prefs["excluded_employers"]:
        excluded = any(item.casefold() in company.casefold() for item in prefs["excluded_employers"]) if company else None
        add("employer", not excluded if excluded is not None else None,
            f"Employer {company} is excluded." if excluded else "Employer is not excluded." if excluded is False else "Employer is unknown.",
            missing_label="employer")

    if prefs["negative_keywords"]:
        posting = f"{job.get('title') or ''} {job.get('description') or ''}".casefold()
        found = next((item for item in prefs["negative_keywords"] if item.casefold() in posting), None)
        add("negative_keywords", found is None,
            f"Posting contains negative keyword: {found}." if found else "No excluded keyword appears in the posting.")

    product = math.prod(item["factor"] for item in factors.values())
    score = math.floor(100 * product + 0.5)
    missing = list(dict.fromkeys(missing))
    if not factors:
        missing.append("scoring preferences")
    confidence = max(0, 100 - 15 * len(missing)) if factors else 0
    detail = {
        "method": "deterministic", "score_version": SCORE_VERSION,
        "score_formula": "round(100 * product(configured criterion factors))",
        "facts": facts, "factors": factors, "criteria": criteria,
        "confidence": confidence, "missing_evidence": missing,
        "hard_exclusions": exclusions,
        "explanation": "Strong match on configured criteria." if score >= prefs["strong_match_threshold"] and not exclusions
        else "One or more strict criteria do not match." if exclusions
        else "Some configured preferences do not match.",
        "fit": "", "watch_out": "",
    }
    return score, confidence, detail


def save_user_score(conn, vacancy_id: str, facts: dict, profile: dict, preferences: dict,
                    score: int, confidence: int, detail: dict) -> None:
    payload = json.dumps(detail, ensure_ascii=False)
    timestamp = now()
    conn.execute(
        "INSERT INTO user_job_scores(user_id,vacancy_id,score,confidence,detail,profile_signature,"
        "preference_signature,facts_signature,scored_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id,vacancy_id) DO UPDATE SET score=excluded.score,confidence=excluded.confidence,"
        "detail=excluded.detail,profile_signature=excluded.profile_signature,"
        "preference_signature=excluded.preference_signature,facts_signature=excluded.facts_signature,"
        "scored_at=excluded.scored_at",
        (LOCAL_USER, vacancy_id, score, confidence, payload, signature(profile),
         preference_signature(preferences), signature(facts), timestamp),
    )
    # Existing Jobs and application queries read these columns until their API is migrated.
    conn.execute("UPDATE vacancies SET score=?,score_detail=? WHERE id=?", (score, payload, vacancy_id))


def rescore_cached_jobs(db: Database, profile: dict, preferences: dict | None = None,
                        *, force: bool = False) -> int:
    """Recompute user scores from cached facts without a parser or LLM call."""
    prefs = normalize_search_intent(preferences)
    profile_hash, pref_hash = signature(profile), preference_signature(prefs)
    rows = db.all(
        "SELECT v.*,f.facts,s.profile_signature,s.preference_signature,s.facts_signature "
        "FROM job_facts f JOIN vacancies v ON v.id=f.vacancy_id "
        "LEFT JOIN user_job_scores s ON s.user_id=? AND s.vacancy_id=v.id "
        "WHERE v.analysis_status='done'",
        (LOCAL_USER,),
    )
    changes = []
    for job in rows:
        facts = json.loads(job.pop("facts"))
        facts_hash = signature(facts)
        if not force and job["profile_signature"] == profile_hash and \
                job["preference_signature"] == pref_hash and job["facts_signature"] == facts_hash:
            continue
        score, confidence, detail = score_job_for_user(job, facts, profile, prefs)
        changes.append((job["id"], facts, score, confidence, detail))
    with db.connection() as conn:
        for vacancy_id, facts, score, confidence, detail in changes:
            save_user_score(conn, vacancy_id, facts, profile, prefs, score, confidence, detail)
    return len(changes)
