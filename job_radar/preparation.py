"""Cheap application-preparation preflight.

This module deliberately does no drafting or browser automation. It resolves the
best known application action, validates the target shape, and tells callers
whether expensive preparation can proceed without an explicit override.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .application_action import describe_application_action, resolve_application_action
from .db import Database


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _valid_target(action: dict) -> bool:
    if action.get("kind") == "email":
        return bool(_EMAIL.fullmatch(str(action.get("email") or "")))
    if action.get("kind") == "web":
        parts = urlsplit(str(action.get("url") or ""))
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    return False


def preparation_preflight(
    db: Database, job_id: str, *, job_data: dict | None = None,
    observations: list[dict] | None = None,
) -> dict:
    job = job_data if job_data is not None else db.one("SELECT * FROM vacancies WHERE id=?", (job_id,))
    if not job:
        raise KeyError("Job not found")
    action = resolve_application_action(db, job, observations=observations)
    target_valid = _valid_target(action)
    manual_only = action.get("kind") == "manual" or action.get("action_type") in {
        "manual", "unknown", "linkedin_easy_apply",
    }

    if target_valid and not manual_only:
        state = "ready"
        reason = (
            action.get("evidence")
            or "Job Radar found a usable application destination in the posting."
        )
    else:
        state = "confirmation_required"
        if action.get("action_type") == "linkedin_easy_apply":
            reason = (
                "This posting appears to use LinkedIn Easy Apply. Job Radar will inspect the form "
                "while preparing your draft. Review the answers, then choose Approve & send "
                "to fill and submit if every step still matches."
            )
        elif action.get("provenance") == "conflicting_explicit_evidence":
            reason = (
                "The posting exposes more than one plausible application destination. "
                "Confirm the method before spending drafting work."
            )
        elif not target_valid and action.get("kind") in ("email", "web"):
            reason = "The detected application destination is incomplete or invalid."
        else:
            reason = (
                action.get("evidence")
                or "No verified application destination is available yet."
            )

    return {
        "job_id": job_id,
        "state": state,
        "ready": state == "ready",
        "requires_confirmation": state != "ready",
        "action": action,
        "action_label": describe_application_action(action),
        "reason": reason,
    }
