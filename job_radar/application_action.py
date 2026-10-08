from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit

from .db import Database


_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_APPLICATION_INSTRUCTION = re.compile(
    r"\b(?:apply|application|submit|send\s+(?:your\s+)?(?:cv|resume)|email\s+(?:your\s+)?(?:cv|resume)|"
    r"ứng\s+tuyển|nộp\s+(?:cv|hồ\s+sơ)|gửi\s+(?:cv|hồ\s+sơ)|gửi\s+về)\b",
    re.I,
)
_EASY_APPLY = re.compile(r"\b(?:easy\s+apply|ứng\s+tuyển\s+dễ\s+dàng)\b", re.I)
_FORM_PATH = re.compile(r"(?:^|[/_.-])(?:apply|application|career|careers|job|jobs|position|opening|recruit|form)(?:[/_.-]|$)", re.I)
_FORM_HOSTS = (
    "forms.gle", "greenhouse.io", "lever.co", "myworkdayjobs.com", "workdayjobs.com",
    "smartrecruiters.com", "successfactors.com", "ashbyhq.com", "bamboohr.com", "workable.com",
)


def _mailto(value: str | None) -> str | None:
    if not value or not value.casefold().startswith("mailto:"):
        return None
    address = unquote(value[7:].split("?", 1)[0]).strip()
    return address if _EMAIL.fullmatch(address) else None


def _http(value: str | None) -> bool:
    if not value:
        return False
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _linkedin(value: str | None) -> bool:
    if not _http(value):
        return False
    host = (urlsplit(value).hostname or "").casefold()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def is_linkedin_job_posting_url(value: str | None) -> bool:
    return _linkedin(value) and urlsplit(value or "").path.casefold().startswith("/jobs/")


def _formish(value: str | None) -> bool:
    if not _http(value):
        return False
    parts = urlsplit(value)
    host = (parts.hostname or "").casefold()
    path = f"{parts.path}?{parts.query}".casefold()
    if host == "docs.google.com":
        return "/forms/" in parts.path.casefold()
    if any(host == item or host.endswith("." + item) for item in _FORM_HOSTS):
        return True
    return bool(_FORM_PATH.search(path))


def _instruction_emails(text: str) -> list[str]:
    found: list[str] = []
    for match in _EMAIL.finditer(text or ""):
        context = text[max(0, match.start() - 140):min(len(text), match.end() + 140)]
        if _APPLICATION_INSTRUCTION.search(context):
            address = match.group(0)
            if address.casefold() not in {item.casefold() for item in found}:
                found.append(address)
    return found


def _candidate(*, kind: str, action_type: str, destination: str, source_kind: str | None,
               provenance: str, confidence: str, evidence: str, strength: int) -> dict:
    result = {
        "kind": kind, "action_type": action_type, "provenance": provenance,
        "confidence": confidence, "evidence": evidence, "_strength": strength,
    }
    if source_kind:
        result["source_kind"] = source_kind
    result["email" if kind == "email" else "url"] = destination
    return result


def _manual(provenance: str, evidence: str) -> dict:
    return {
        "kind": "manual", "action_type": "manual", "url": "",
        "provenance": provenance, "confidence": "low", "evidence": evidence,
    }


def _public(candidate: dict) -> dict:
    return {key: value for key, value in candidate.items() if not key.startswith("_")}


def _target(destination: dict) -> tuple[str, str]:
    return destination.get("kind", ""), str(destination.get("email") or destination.get("url") or "").strip()


def reviewed_application_action(destination: dict, previous: dict) -> dict:
    current = dict(destination)
    if _target(current) == _target(previous):
        merged = {**previous, **current}
        if not merged.get("action_type"):
            merged["action_type"] = "email" if merged.get("kind") == "email" else "web_form" if merged.get("kind") == "web" else "linkedin_easy_apply" if merged.get("kind") == "linkedin_easy_apply" else "manual"
        return merged
    kind = current.get("kind")
    current["action_type"] = "email" if kind == "email" else "web_form" if kind == "web" else "linkedin_easy_apply" if kind == "linkedin_easy_apply" else "manual"
    current["provenance"] = "manual_override"
    current["confidence"] = "user_confirmed"
    current["evidence"] = "Destination edited during application review."
    return current


def describe_application_action(destination: dict) -> str:
    action_type = destination.get("action_type") or destination.get("kind") or "manual"
    labels = {
        "email": "Email", "web_form": "Web form", "linkedin_easy_apply": "LinkedIn Easy Apply",
        "manual": "Manual review", "unknown": "Manual review", "web": "Web form",
    }
    label = labels.get(action_type, str(action_type).replace("_", " ").title())
    target = destination.get("email") or destination.get("url")
    return f"{label} -> {target}" if target else label


def resolve_application_action(db: Database, job: dict, *, observations: list[dict] | None = None) -> dict:
    rows = observations if observations is not None else db.all(
        "SELECT s.kind AS source_kind,o.url AS observation_url,o.raw_text,o.payload,o.last_seen_at "
        "FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id "
        "JOIN sources s ON s.id=o.source_id WHERE vo.vacancy_id=? ORDER BY o.last_seen_at DESC",
        (job["id"],),
    )
    candidates: list[dict] = []

    for address in _instruction_emails(str(job.get("description") or "")):
        candidates.append(_candidate(
            kind="email", action_type="email", destination=address, source_kind=None,
            provenance="posting_instruction", confidence="high",
            evidence="The posting explicitly instructs candidates to apply by email.", strength=3,
        ))

    for row in rows:
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        source_kind = row.get("source_kind")
        raw = "\n".join(str(value or "") for value in (
            row.get("raw_text"), payload.get("description"), payload.get("raw_text"),
        ))
        for address in _instruction_emails(raw):
            candidates.append(_candidate(
                kind="email", action_type="email", destination=address, source_kind=source_kind,
                provenance=f"{source_kind}_posting_instruction" if source_kind else "posting_instruction",
                confidence="high",
                evidence="The source posting explicitly instructs candidates to apply by email.", strength=3,
            ))

        apply_url = payload.get("apply_url")
        mail = _mailto(apply_url)
        if mail:
            candidates.append(_candidate(
                kind="email", action_type="email", destination=mail, source_kind=source_kind,
                provenance=f"{source_kind}_apply_link" if source_kind else "apply_link",
                confidence="high", evidence="The collected application destination is a mailto link.", strength=3,
            ))
            continue
        if source_kind == "linkedin" and _EASY_APPLY.search(raw):
            candidates.append(_candidate(
                kind="manual", action_type="linkedin_easy_apply",
                destination=str(payload.get("url") or row.get("observation_url") or apply_url or job.get("apply_url") or ""),
                source_kind="linkedin", provenance="linkedin_easy_apply_control", confidence="high",
                evidence="The LinkedIn posting indicates Easy Apply. Job Radar will verify the button and form before submission.", strength=3,
            ))
        if not _http(apply_url):
            continue
        if source_kind == "linkedin":
            if not _linkedin(apply_url):
                candidates.append(_candidate(
                    kind="web", action_type="web_form", destination=apply_url, source_kind="linkedin",
                    provenance="linkedin_external_apply", confidence="high",
                    evidence="LinkedIn exposes an external application destination.", strength=3,
                ))
        elif source_kind == "career":
            same_page = str(apply_url).rstrip("/") == str(row.get("observation_url") or "").rstrip("/")
            candidates.append(_candidate(
                kind="web", action_type="web_form", destination=apply_url, source_kind="career",
                provenance="career_posting_page" if same_page else "career_apply_link",
                confidence="medium" if same_page else "high",
                evidence="The company career source provides the application page." if same_page
                         else "The company career posting provides a dedicated application link.",
                strength=2 if same_page else 3,
            ))
        elif source_kind == "facebook" and _formish(apply_url):
            candidates.append(_candidate(
                kind="web", action_type="web_form", destination=apply_url, source_kind="facebook",
                provenance="facebook_form_link", confidence="high",
                evidence="The Facebook post links to a recognizable application form or career page.", strength=3,
            ))

    direct = job.get("apply_url")
    mail = _mailto(direct)
    if mail:
        candidates.append(_candidate(
            kind="email", action_type="email", destination=mail, source_kind=None,
            provenance="vacancy_apply_url", confidence="high",
            evidence="The vacancy has an explicit email application destination.", strength=3,
        ))
    elif _http(direct):
        source_kinds = {row.get("source_kind") for row in rows}
        if not rows or ("facebook" not in source_kinds and "linkedin" not in source_kinds):
            candidates.append(_candidate(
                kind="web", action_type="web_form", destination=direct, source_kind=None,
                provenance="vacancy_apply_url", confidence="medium",
                evidence="The vacancy has a web application destination.", strength=2,
            ))

    if not candidates:
        return _manual("no_application_evidence", "No safe application destination was detected.")
    strongest = max(item["_strength"] for item in candidates)
    top = [item for item in candidates if item["_strength"] == strongest and (item.get("email") or item.get("url"))]
    identities = {(item["action_type"], item.get("email") or item.get("url")) for item in top}
    if len(identities) != 1:
        return _manual(
            "conflicting_explicit_evidence",
            "The posting exposes multiple plausible application destinations. Confirm the intended action before sending.",
        )
    preferred = max(top, key=lambda item: (bool(item.get("source_kind")), bool(item.get("provenance", "").endswith("_posting_instruction"))))
    return _public(preferred)
