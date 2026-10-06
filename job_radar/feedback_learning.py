"""Preference suggestions derived from repeated explicit user decisions."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from .db import Database
from .search_intent import normalize_search_intent


MIN_PATTERN_COUNT = 3


def _suggestion_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{value.casefold()}".encode()).hexdigest()[:16]
    return f"{kind}:{digest}"


def _latest_feedback(db: Database) -> list[dict[str, Any]]:
    rows = db.all(
        "SELECT f.rowid AS feedback_rowid,f.vacancy_id,f.state,f.reason,f.created_at,"
        "v.company,v.location,v.title "
        "FROM feedback f JOIN vacancies v ON v.id=f.vacancy_id "
        "ORDER BY datetime(f.created_at) DESC,f.rowid DESC"
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest.setdefault(row["vacancy_id"], row)
    return list(latest.values())


def feedback_suggestions(db: Database, *, include_dismissed: bool = False) -> list[dict[str, Any]]:
    preferences = normalize_search_intent(db.get_setting("search_intent", {}))
    dismissed = set(db.get_setting("dismissed_feedback_suggestions", []) or [])
    feedback = _latest_feedback(db)
    suggestions: list[dict[str, Any]] = []

    ignored_company = Counter(
        row["company"].strip()
        for row in feedback
        if row["state"] == "ignored"
        and row.get("reason") == "Company"
        and str(row.get("company") or "").strip()
    )
    excluded = {item.casefold() for item in preferences["excluded_employers"]}
    for company, count in ignored_company.most_common():
        if count < MIN_PATTERN_COUNT or company.casefold() in excluded:
            continue
        identifier = _suggestion_id("exclude_employer", company)
        suggestions.append({
            "id": identifier,
            "kind": "exclude_employer",
            "value": company,
            "evidence_count": count,
            "title": f"Exclude {company}?",
            "description": f"You ignored {count} jobs from {company} because of the company.",
            "action_label": "Exclude employer",
        })

    shortlisted_company = Counter(
        row["company"].strip()
        for row in feedback
        if row["state"] == "shortlisted" and str(row.get("company") or "").strip()
    )
    preferred = {item.casefold() for item in preferences["preferred_employers"]}
    for company, count in shortlisted_company.most_common():
        if count < MIN_PATTERN_COUNT or company.casefold() in preferred:
            continue
        identifier = _suggestion_id("prefer_employer", company)
        suggestions.append({
            "id": identifier,
            "kind": "prefer_employer",
            "value": company,
            "evidence_count": count,
            "title": f"Prefer {company}?",
            "description": f"You shortlisted {count} jobs from {company}.",
            "action_label": "Prefer employer",
        })

    location_rows = [
        row for row in feedback
        if row["state"] == "ignored"
        and row.get("reason") == "Location or work mode"
        and str(row.get("location") or "").strip()
    ]
    ignored_locations = Counter(row["location"].strip() for row in location_rows)
    preferred_locations = {item.casefold(): item for item in preferences["preferred_locations"]}
    for location, count in ignored_locations.most_common():
        saved = preferred_locations.get(location.casefold())
        if count < MIN_PATTERN_COUNT or not saved:
            continue
        identifier = _suggestion_id("remove_preferred_location", saved)
        suggestions.append({
            "id": identifier,
            "kind": "remove_preferred_location",
            "value": saved,
            "evidence_count": count,
            "title": f"Remove {saved} from preferred locations?",
            "description": f"You ignored {count} jobs there because of location or work mode.",
            "action_label": "Remove preference",
        })

    suggestions.sort(key=lambda item: (-item["evidence_count"], item["title"].casefold()))
    if include_dismissed:
        for item in suggestions:
            item["dismissed"] = item["id"] in dismissed
        return suggestions
    return [item for item in suggestions if item["id"] not in dismissed]


def apply_feedback_suggestion(db: Database, suggestion_id: str) -> dict[str, Any]:
    suggestions = {item["id"]: item for item in feedback_suggestions(db, include_dismissed=True)}
    suggestion = suggestions.get(suggestion_id)
    if not suggestion:
        raise KeyError("Preference suggestion not found")

    preferences = normalize_search_intent(db.get_setting("search_intent", {}))
    kind = suggestion["kind"]
    value = suggestion["value"]

    if kind == "exclude_employer":
        existing = {item.casefold() for item in preferences["excluded_employers"]}
        if value.casefold() not in existing:
            preferences["excluded_employers"].append(value)
    elif kind == "prefer_employer":
        existing = {item.casefold() for item in preferences["preferred_employers"]}
        if value.casefold() not in existing:
            preferences["preferred_employers"].append(value)
    elif kind == "remove_preferred_location":
        preferences["preferred_locations"] = [
            item for item in preferences["preferred_locations"] if item.casefold() != value.casefold()
        ]
    else:
        raise ValueError("Unsupported preference suggestion")

    db.set_setting("search_intent", preferences)
    dismissed = set(db.get_setting("dismissed_feedback_suggestions", []) or [])
    dismissed.add(suggestion_id)
    db.set_setting("dismissed_feedback_suggestions", sorted(dismissed))
    return preferences


def dismiss_feedback_suggestion(db: Database, suggestion_id: str) -> None:
    available = {item["id"] for item in feedback_suggestions(db, include_dismissed=True)}
    if suggestion_id not in available:
        raise KeyError("Preference suggestion not found")
    dismissed = set(db.get_setting("dismissed_feedback_suggestions", []) or [])
    dismissed.add(suggestion_id)
    db.set_setting("dismissed_feedback_suggestions", sorted(dismissed))
