"""User-independent parsed job pool and its persistence boundary."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .application_action import resolve_application_action
from .db import Database, now
from .search_intent import role_family, seniority_key


PARSER_VERSION = 1
SOURCE_FIELDS = ("title", "company", "description", "location", "work_mode", "apply_url", "published_at")
_DEADLINE_LABEL = re.compile(
    r"(?:application deadline|apply before|closing date|deadline to apply|deadline|"
    r"hạn chót nhận hồ sơ|hạn nộp hồ sơ|hạn nộp|thời gian ứng tuyển)\s*:?\s*",
    re.I,
)
_DATE = re.compile(
    r"\b(?:20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}[/.\-]20\d{2}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}|"
    r"\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+20\d{2}|"
    r"\d{1,2}\s+tháng\s+\d{1,2}\s+năm\s+20\d{2})\b",
    re.I,
)
_FACEBOOK_PERSON = re.compile(
    r"https?://(?:www\.|m\.)?facebook\.com/(?:profile\.php\?id=\d+|[A-Za-z0-9.]+/?)(?:\?[^\s]*)?",
    re.I,
)


def source_signature(job: dict[str, Any]) -> str:
    payload = {key: job.get(key) or "" for key in SOURCE_FIELDS}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def due_date_from_posting(text: str) -> str:
    """Accept a date only beside an explicit application-deadline label."""
    for label in _DEADLINE_LABEL.finditer(text or ""):
        window = text[label.end():label.end() + 100]
        for token in _DATE.finditer(window):
            raw = token.group()
            normalized = re.sub(r"(\d)\s+tháng\s+(\d+)\s+năm\s+(\d{4})", r"\1/\2/\3", raw, flags=re.I)
            normalized = normalized.replace(",", "")
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y",
                        "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
                try:
                    return datetime.strptime(normalized, fmt).date().isoformat()
                except ValueError:
                    pass
    return ""


def _safe_destination(kind: str, target: str) -> dict[str, str]:
    return {"kind": kind, "target": target}


def application_destination(db: Database, job: dict[str, Any], suggested: dict | None = None) -> dict[str, str]:
    """Ground an application channel in saved posting evidence, without user data."""
    resolved = resolve_application_action(db, job)
    action = resolved.get("action_type")
    target = str(resolved.get("email") or resolved.get("url") or "").strip()
    if action in {"web_form", "linkedin_easy_apply", "email"} and target:
        return _safe_destination(action, target)

    rows = db.all(
        "SELECT o.raw_text,o.url,s.kind FROM vacancy_observations vo "
        "JOIN observations o ON o.id=vo.observation_id "
        "JOIN sources s ON s.id=o.source_id WHERE vo.vacancy_id=?",
        (job["id"],),
    )
    source_text = "\n".join([str(job.get("description") or ""), *[row["raw_text"] for row in rows]])
    if any(row["kind"] == "facebook" for row in rows):
        from .zalo_contacts import contact_candidates

        phones = list(dict.fromkeys(item["phone"] for item in contact_candidates(db, job["id"])))
        if len(phones) == 1:
            return _safe_destination("zalo_message", phones[0])

    candidate = suggested if isinstance(suggested, dict) else {}
    candidate_kind = str(candidate.get("kind") or "")
    candidate_target = str(candidate.get("target") or "").strip()
    if candidate_kind == "facebook_message" and candidate_target and candidate_target in source_text:
        parsed = urlsplit(candidate_target)
        if parsed.hostname in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            return _safe_destination("facebook_message", candidate_target)
    if re.search(r"\b(?:inbox|message|messenger|nhắn tin|liên hệ)\b", source_text, re.I):
        person_links = [match.group().rstrip(".,)") for match in _FACEBOOK_PERSON.finditer(source_text)]
        person_links = [url for url in person_links if not re.search(r"/(?:groups|posts|permalink|share|reel)/", url, re.I)]
        if len(set(person_links)) == 1:
            return _safe_destination("facebook_message", person_links[0])
    return _safe_destination("unknown", "")


def neutralize_facts(db: Database, job: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    result = dict(facts)
    role = str(result.get("role") or job.get("title") or "")
    mode = str(result.get("work_mode") or job.get("work_mode") or "").casefold()
    result["role_family_key"] = role_family(role)
    result["seniority_key"] = seniority_key(f"{result.get('seniority') or ''} {role}")
    result["work_mode_key"] = (
        "remote" if re.search(r"\b(?:remote|wfh|work from home)\b", mode) else
        "hybrid" if "hybrid" in mode else
        "onsite" if re.search(r"\bon[ -]?site\b", mode) else ""
    )
    result["due_date"] = due_date_from_posting(str(job.get("description") or ""))
    result["apply_destination"] = application_destination(db, job, result.get("apply_destination"))
    return result


def save_job_facts(conn, job: dict[str, Any], facts: dict[str, Any], parser_model: str) -> None:
    conn.execute(
        "INSERT INTO job_facts(vacancy_id,facts,parser_model,parser_version,source_signature,parsed_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(vacancy_id) DO UPDATE SET "
        "facts=excluded.facts,parser_model=excluded.parser_model,parser_version=excluded.parser_version,"
        "source_signature=excluded.source_signature,parsed_at=excluded.parsed_at",
        (job["id"], json.dumps(facts, ensure_ascii=False), parser_model, PARSER_VERSION,
         source_signature(job), now()),
    )


def backfill_legacy_facts(db: Database) -> int:
    """Move existing extracted facts without invoking a model or changing jobs."""
    rows = db.all(
        "SELECT v.* FROM vacancies v LEFT JOIN job_facts f ON f.vacancy_id=v.id "
        "WHERE f.vacancy_id IS NULL AND v.analysis_status='done' AND v.score_detail IS NOT NULL"
    )
    prepared = []
    for job in rows:
        try:
            detail = json.loads(job["score_detail"])
        except (TypeError, ValueError):
            continue
        facts = detail.get("facts") if isinstance(detail, dict) else None
        if not isinstance(facts, dict):
            continue
        prepared.append((job, neutralize_facts(db, job, facts),
                         str(job.get("analysis_parsing_model") or job.get("analysis_model") or "legacy")))
    with db.connection() as conn:
        for job, facts, parser_model in prepared:
            save_job_facts(conn, job, facts, parser_model)
    return len(prepared)


def get_job_facts(db: Database, vacancy_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT facts FROM job_facts WHERE vacancy_id=?", (vacancy_id,))
    return json.loads(row["facts"]) if row else None
