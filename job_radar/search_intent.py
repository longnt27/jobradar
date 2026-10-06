"""Canonical job-search intent and fit semantics shared across product surfaces."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

MODE_KEYS = (
    "role_families",
    "seniority_levels",
    "preferred_locations",
    "work_modes",
    "preferred_employers",
    "excluded_employers",
    "negative_keywords",
    "max_required_experience_years",
    "minimum_salary",
    "strong_match_threshold",
)

DEFAULT_SEARCH_INTENT = {
    "role_families": [],
    "seniority_levels": [],
    "preferred_locations": [],
    "work_modes": [],
    "preferred_employers": [],
    "excluded_employers": [],
    "negative_keywords": [],
    "max_required_experience_years": None,
    "preference_modes": {key: "auto" for key in MODE_KEYS},
    "hard_constraints": {
        "role_family": False,
        "seniority": False,
        "location": False,
        "work_mode": False,
        "employer": True,
        "minimum_salary": False,
        "experience": True,
    },
    "minimum_salary": None,
    "salary_currency": "VND",
    "salary_unknown_ok": True,
    "strong_match_threshold": 80,
    "inferred": {},
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

_ROLE_FAMILY_PATTERNS = (
    ("Data Scientist", re.compile(r"\bdata scientist\b|\bkhoa học dữ liệu\b", re.I)),
    ("Data Analyst", re.compile(r"\bdata analyst\b|\bphân tích dữ liệu\b", re.I)),
    ("Data Engineer", re.compile(r"\bdata engineer\b", re.I)),
    ("AI Engineer", re.compile(
        r"\b(?:ai|ml|machine learning|applied ai|llm)\b.{0,30}\b(?:engineer|engineering|trainee|intern)\b|"
        r"\b(?:engineer|engineering)\b.{0,20}\b(?:ai|ml)\b", re.I)),
    ("Software Engineer", re.compile(r"\b(?:software|backend|frontend|fullstack)\b.{0,20}\b(?:engineer|developer)\b", re.I)),
    ("Research", re.compile(r"\b(?:research scientist|research engineer|researcher)\b", re.I)),
    ("Robotics", re.compile(r"\b(?:robotics|robot|perception|manipulation)\b.{0,30}\b(?:engineer|research|intern)\b", re.I)),
)

YEARS_REQUIRED = re.compile(r"(?<!\d)(\d{1,2})\s*(?:\+|[-–]\s*\d{1,2})?\s*(?:years?|yrs?|yoe|năm)\b", re.I)
MONTH_NAMES = {name: number for number, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
    ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
    ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")), 1) for name in names}
DATE_TOKEN = re.compile(
    r"(?<!\w)(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{4}\b|"
    r"(?<!\d)\d{4}[-/](?:0?[1-9]|1[0-2])(?!\d)|"
    r"(?<!\d)(?:0?[1-9]|1[0-2])[-/]\d{4}(?!\d)|"
    r"(?<!\d)\d{4}(?!\d)|\b(?:present|current|now|hiện tại|nay)\b", re.I)

_LOCATION_ALIASES = (
    ("Hanoi", re.compile(r"\b(?:hanoi|ha\s*noi|hà\s*nội|hn|cau\s*giay|cầu\s*giấy|dong\s*da|đống\s*đa|"
                          r"thanh\s*xuan|thanh\s*xuân|ha\s*dong|hà\s*đông|long\s*bien|long\s*biên|"
                          r"tay\s*ho|tây\s*hồ|hoang\s*mai|hoàng\s*mai)\b", re.I)),
    ("Ho Chi Minh City", re.compile(r"\b(?:ho\s*chi\s*minh(?:\s*city)?|hồ\s*chí\s*minh|hcmc|hcm|saigon|sài\s*gòn)\b", re.I)),
    ("Da Nang", re.compile(r"\b(?:da\s*nang|đà\s*nẵng)\b", re.I)),
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


def _explicit_legacy_value(raw: dict[str, Any], key: str) -> bool:
    if key not in raw:
        return False
    value = raw.get(key)
    if key == "strong_match_threshold":
        try:
            return int(value) != 80
        except (TypeError, ValueError):
            return False
    if key == "minimum_salary":
        return value not in (None, "")
    if key == "max_required_experience_years":
        return value not in (None, "")
    if isinstance(value, list):
        return bool(value)
    return value not in (None, "")


def normalize_search_intent(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    result = {**DEFAULT_SEARCH_INTENT}
    result["hard_constraints"] = {**DEFAULT_SEARCH_INTENT["hard_constraints"]}
    result["preference_modes"] = {**DEFAULT_SEARCH_INTENT["preference_modes"]}
    for key in ("role_families", "preferred_locations", "work_modes", "preferred_employers",
                "excluded_employers", "negative_keywords"):
        result[key] = _strings(raw.get(key))
    valid_levels = set(SENIORITY_LABELS)
    result["seniority_levels"] = [item for item in _strings(raw.get("seniority_levels")) if item in valid_levels]
    hard = raw.get("hard_constraints") if isinstance(raw.get("hard_constraints"), dict) else {}
    for key in result["hard_constraints"]:
        if key in hard:
            result["hard_constraints"][key] = bool(hard[key])
    modes = raw.get("preference_modes") if isinstance(raw.get("preference_modes"), dict) else {}
    for key in MODE_KEYS:
        explicit = str(modes.get(key) or "").casefold()
        result["preference_modes"][key] = explicit if explicit in {"auto", "custom"} else (
            "custom" if _explicit_legacy_value(raw, key) else "auto"
        )
    maximum = raw.get("max_required_experience_years")
    try:
        result["max_required_experience_years"] = (
            min(50, max(0, int(maximum))) if maximum not in (None, "") else None
        )
    except (TypeError, ValueError):
        result["max_required_experience_years"] = None
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
    result["inferred"] = raw.get("inferred") if isinstance(raw.get("inferred"), dict) else {}
    return result


def seniority_key(value: str) -> str:
    text = str(value or "")
    for key, pattern in _SENIORITY_PATTERNS:
        if pattern.search(text):
            return key
    return ""


def role_family(value: str) -> str:
    text = str(value or "")
    for family, pattern in _ROLE_FAMILY_PATTERNS:
        if pattern.search(text):
            return family
    return ""


def canonical_location(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for label, pattern in _LOCATION_ALIASES:
        if pattern.search(text):
            return label
    return text[:120]


def location_matches_preference(location: str, preferred_locations: list[str]) -> bool:
    actual = canonical_location(location)
    if not actual:
        return False
    actual_folded = actual.casefold()
    for preferred in preferred_locations:
        expected = canonical_location(preferred)
        if not expected:
            continue
        expected_folded = expected.casefold()
        if actual_folded == expected_folded or expected_folded in actual_folded or actual_folded in expected_folded:
            return True
    return False


def extract_required_years(text: str) -> int | None:
    """Minimum explicit requirement per range; highest independent requirement wins."""
    unrelated = re.compile(
        r"^\s*(?:ago\b|old\b|in\s+business\b|of\s+(?:history|operation|innovation|service)\b|anniversary\b|"
        r"(?:hình\s+thành|thành\s+lập|hoạt\s+động|phát\s+triển|đồng\s+hành|kinh\s+doanh)\b)", re.I)
    years = [int(match.group(1)) for match in YEARS_REQUIRED.finditer(str(text or ""))
             if not unrelated.search(str(text or "")[match.end():match.end() + 50])]
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    years.extend(words[match.group(1).casefold()] for match in re.finditer(
        r"\b(" + "|".join(words) + r")\s+(?:years?|yrs?)\b", str(text or ""), re.I)
                 if not unrelated.search(str(text or "")[match.end():match.end() + 50]))
    return max(years) if years else None


def _month_index(token: str, *, end: bool, current: datetime) -> int | None:
    value = token.strip().casefold().replace(".", "")
    if value in {"present", "current", "now", "hiện tại", "nay"}:
        return current.year * 12 + current.month - 1 if end else None
    named = re.fullmatch(r"([a-z]+)\s+(\d{4})", value)
    if named:
        month, year = MONTH_NAMES.get(named.group(1)), int(named.group(2))
    elif year_month := re.fullmatch(r"(\d{4})[-/](\d{1,2})", value):
        year, month = int(year_month.group(1)), int(year_month.group(2))
    elif month_year := re.fullmatch(r"(\d{1,2})[-/](\d{4})", value):
        month, year = int(month_year.group(1)), int(month_year.group(2))
    elif re.fullmatch(r"\d{4}", value):
        year, month = int(value), 1 if not end else 12
    else:
        return None
    return year * 12 + month - 1 if month and 1 <= month <= 12 else None


def documented_experience_months(profile: dict[str, Any], *, as_of: datetime | None = None) -> int | None:
    current = as_of or datetime.now().astimezone()
    positions = [item for item in profile.get("experience") or [] if isinstance(item, dict)]
    if not positions:
        return 0
    intervals: list[tuple[int, int]] = []
    for position in positions:
        tokens = [match.group() for match in DATE_TOKEN.finditer(str(position.get("dates") or ""))]
        if len(tokens) < 2:
            continue
        start = _month_index(tokens[0], end=False, current=current)
        end = _month_index(tokens[1], end=True, current=current)
        if start is not None and end is not None and start <= end:
            intervals.append((start, min(end, current.year * 12 + current.month - 1)))
    if not intervals:
        return None
    months: set[int] = set()
    for start, end in intervals:
        months.update(range(start, end + 1))
    return len(months)


def infer_search_intent(profile: dict[str, Any], *, as_of: datetime | None = None) -> dict[str, Any]:
    roles = []
    levels = []
    for position in profile.get("experience") or []:
        if not isinstance(position, dict):
            continue
        title = str(position.get("role") or position.get("title") or "").strip()
        family = role_family(title)
        if family and family.casefold() not in {item.casefold() for item in roles}:
            roles.append(family)
        level = seniority_key(title)
        if level and level not in levels:
            levels.append(level)
    if not roles:
        family = role_family(str(profile.get("summary") or ""))
        if family:
            roles.append(family)

    location = canonical_location(str(profile.get("location") or ""))
    months = documented_experience_months(profile, as_of=as_of)
    documented_years = round(months / 12, 1) if months is not None else None
    max_years = math.ceil(months / 12) + 1 if months is not None else None
    return {
        "role_families": roles,
        "seniority_levels": levels,
        "preferred_locations": [location] if location else [],
        "work_modes": [],
        "preferred_employers": [],
        "excluded_employers": [],
        "negative_keywords": [],
        "max_required_experience_years": max_years,
        "minimum_salary": None,
        "strong_match_threshold": 80,
        "documented_experience_years": documented_years,
        "profile_location": location,
    }


def apply_auto_search_intent(value: Any, profile: dict[str, Any], *, as_of: datetime | None = None) -> dict[str, Any]:
    result = normalize_search_intent(value)
    inferred = infer_search_intent(profile, as_of=as_of)
    for key in MODE_KEYS:
        if result["preference_modes"].get(key) == "auto":
            result[key] = inferred.get(key)
    result["inferred"] = inferred

    # Auto location is a conservative eligibility gate only when the profile has a usable location.
    if result["preference_modes"].get("preferred_locations") == "auto":
        result["hard_constraints"]["location"] = bool(result["preferred_locations"])
    # Auto role/seniority/work-mode remain ranking guidance, never implicit hard gates.
    for key, hard_key in (
        ("role_families", "role_family"),
        ("seniority_levels", "seniority"),
        ("work_modes", "work_mode"),
    ):
        if result["preference_modes"].get(key) == "auto":
            result["hard_constraints"][hard_key] = False
    result["hard_constraints"]["experience"] = result["max_required_experience_years"] is not None
    return result


def migrate_search_intent(value: Any, profile: dict[str, Any], *, legacy_threshold: int = 80) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, dict) else {}
    if not raw:
        raw = {"strong_match_threshold": legacy_threshold}
    return apply_auto_search_intent(raw, profile)


def reset_search_preference(value: Any, profile: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in MODE_KEYS:
        raise KeyError("Unknown search preference")
    result = normalize_search_intent(value)
    result["preference_modes"][key] = "auto"
    if key in {"role_families", "seniority_levels", "preferred_locations", "work_modes",
               "preferred_employers", "excluded_employers", "negative_keywords"}:
        result[key] = []
    elif key in {"max_required_experience_years", "minimum_salary"}:
        result[key] = None
    elif key == "strong_match_threshold":
        result[key] = 80
    return apply_auto_search_intent(result, profile)


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
