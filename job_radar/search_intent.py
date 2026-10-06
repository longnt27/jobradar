"""Canonical job-search intent and fit semantics shared across product surfaces."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_SEARCH_INTENT = {
    "role_families": [],
    "seniority_levels": [],
    "preferred_locations": [],
    "work_modes": [],
    "preferred_employers": [],
    "excluded_employers": [],
    "negative_keywords": [],
    "hard_constraints": {
        "role_family": False,
        "seniority": False,
        "location": False,
        "work_mode": False,
        "employer": True,
        "minimum_salary": False,
    },
    "minimum_salary": None,
    "salary_currency": "VND",
    "salary_unknown_ok": True,
    "strong_match_threshold": 80,
}

SENIORITY_LABELS = {
    "intern": "Intern",
    "entry": "Entry / Junior",
    "mid": "Mid",
    "senior": "Senior",
    "lead_plus": "Lead+",
}

_SENIORITY_PATTERNS = (
    ("intern", re.compile(r"\b(?:intern|internship|trainee)\b|thực\s*tập", re.I)),
    ("entry", re.compile(r"\b(?:entry|junior|jr\.?|graduate|fresher)\b|mới\s*tốt\s*nghiệp", re.I)),
    ("mid", re.compile(r"\b(?:mid|middle)(?:[- ]level)?\b", re.I)),
    ("senior", re.compile(r"\b(?:senior|sr\.?)\b|cao\s*cấp", re.I)),
    ("lead_plus", re.compile(r"\b(?:lead|principal|staff|manager|director|head)\b|trưởng\s*nhóm|quản\s*lý", re.I)),
)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and text.casefold() not in {saved.casefold() for saved in result}:
            result.append(text[:120])
    return result[:50]


def normalize_search_intent(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    result = {**DEFAULT_SEARCH_INTENT}
    result["hard_constraints"] = {**DEFAULT_SEARCH_INTENT["hard_constraints"]}
    for key in ("role_families", "preferred_locations", "work_modes", "preferred_employers",
                "excluded_employers", "negative_keywords"):
        result[key] = _strings(raw.get(key))
    valid_levels = set(SENIORITY_LABELS)
    result["seniority_levels"] = [item for item in _strings(raw.get("seniority_levels")) if item in valid_levels]
    hard = raw.get("hard_constraints") if isinstance(raw.get("hard_constraints"), dict) else {}
    for key in result["hard_constraints"]:
        if key in hard:
            result["hard_constraints"][key] = bool(hard[key])
    minimum = raw.get("minimum_salary")
    try:
        result["minimum_salary"] = max(0, int(minimum)) if minimum not in (None, "") else None
    except (TypeError, ValueError):
        result["minimum_salary"] = None
    currency = str(raw.get("salary_currency") or "VND").strip().upper()
    result["salary_currency"] = currency[:12] or "VND"
    result["salary_unknown_ok"] = bool(raw.get("salary_unknown_ok", True))
    try:
        result["strong_match_threshold"] = min(100, max(0, int(raw.get("strong_match_threshold", 80))))
    except (TypeError, ValueError):
        result["strong_match_threshold"] = 80
    return result


def seniority_key(value: str) -> str:
    text = str(value or "")
    for key, pattern in _SENIORITY_PATTERNS:
        if pattern.search(text):
            return key
    return ""


def salary_currency(value: str) -> str:
    text = str(value or "").casefold()
    if re.search(r"\b(?:vnd|vnđ)\b|đồng|triệu", text):
        return "VND"
    if re.search(r"\b(?:usd)\b|us\$|\$", text):
        return "USD"
    if re.search(r"\beur\b|€", text):
        return "EUR"
    if re.search(r"\bgbp\b|£", text):
        return "GBP"
    return ""


def salary_floor(value: str, expected_currency: str | None = None) -> float | None:
    """Lower bound for grounded salary text; currencies are never converted or guessed."""
    text = str(value or "").casefold()
    detected_currency = salary_currency(text)
    expected = str(expected_currency or "").strip().upper()
    if expected and detected_currency != expected:
        return None
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]
    if not numbers:
        return None
    floor = min(numbers)
    if any(unit in text for unit in ("million", "triệu", " tr")) or re.search(r"\b\d+(?:\.\d+)?\s*m\b", text):
        floor *= 1_000_000
    elif re.search(r"\b\d+(?:\.\d+)?\s*k\b", text):
        floor *= 1_000
    return floor


def posting_salary_floor(value: str, expected_currency: str | None = None) -> float | None:
    """Read only salary-context fragments so unrelated years/headcounts cannot become pay."""
    for line in str(value or "").splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if not re.search(r"\b(?:salary|compensation|pay|offer)\b|(?:mức\s+)?lương|thu\s+nhập", sentence, re.I):
                continue
            amount = salary_floor(sentence, expected_currency)
            if amount is not None:
                return amount
    return None


def fit_summary(score: int | None, detail: dict[str, Any] | None, preferences: dict[str, Any]) -> dict[str, Any]:
    detail = detail if isinstance(detail, dict) else {}
    prefs = normalize_search_intent(preferences)
    criteria = detail.get("criteria") if isinstance(detail.get("criteria"), dict) else {}
    facts = detail.get("facts") if isinstance(detail.get("facts"), dict) else {}
    missing = []
    field_labels = {
        "required_skills": "required skills",
        "experience": "years of experience",
        "location": "location",
        "work_mode": "work mode",
        "education": "education",
    }
    for key, label in field_labels.items():
        criterion = criteria.get(key) if isinstance(criteria.get(key), dict) else {}
        reason = str(criterion.get("reason") or "").casefold()
        fact_missing = (
            key == "required_skills" and not facts.get("required_skills") or
            key == "experience" and facts.get("years_required") is None or
            key in {"location", "work_mode", "education"} and not facts.get(key)
        )
        if fact_missing or any(word in reason for word in ("not stated", "unknown", "unavailable", "cannot verify", "unverified")):
            missing.append(label)
    ranked = []
    for key, item in criteria.items():
        if isinstance(item, dict) and isinstance(item.get("score"), (int, float)):
            ranked.append((float(item["score"]), key, str(item.get("reason") or "")))
    ranked.sort(reverse=True)
    positive = next(({"criterion": key, "reason": reason} for value, key, reason in ranked if value >= 7), None)
    gap = next(({"criterion": key, "reason": reason} for value, key, reason in reversed(ranked) if value <= 4), None)
    threshold = prefs["strong_match_threshold"]
    exclusions = detail.get("hard_exclusions") if isinstance(detail.get("hard_exclusions"), list) else []
    uncertainty = min(100, len(set(missing)) * 18 + (20 if score is None else 0))
    if exclusions:
        fit_class = "outside"
    elif score is None or uncertainty >= 54:
        fit_class = "uncertain"
    elif score >= threshold:
        fit_class = "strong"
    else:
        fit_class = "stretch"
    return {
        "fit_class": fit_class,
        "strong_match_threshold": threshold,
        "uncertainty": uncertainty,
        "missing_evidence": list(dict.fromkeys(missing)),
        "strongest_signal": positive,
        "main_gap": gap,
    }
