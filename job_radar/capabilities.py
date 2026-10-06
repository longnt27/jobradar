"""Capability readiness and point-of-use processing disclosure contracts."""

from __future__ import annotations

from typing import Any

PROVIDER_LABELS = {
    "template": "Local template",
    "codex_local": "Codex OSS + Ollama",
    "codex": "Codex CLI",
    "agy": "Antigravity CLI",
    "claude": "Claude Code CLI",
}

REMOTE_PROVIDERS = {"codex", "agy", "claude"}


def provider_processing(provider: str) -> dict[str, Any]:
    provider = str(provider or "")
    remote = provider in REMOTE_PROVIDERS
    if not provider:
        return {
            "provider": "",
            "label": "No provider selected",
            "mode": "none",
            "destination": "No processing provider selected",
            "remote": False,
        }
    if provider == "template":
        destination = "Processed locally with deterministic templates; no AI model receives the data."
    elif provider == "codex_local":
        destination = "Processed locally through Codex OSS and Ollama; resume and application text stay on this Mac."
    else:
        destination = f"Processed by {PROVIDER_LABELS.get(provider, provider)} using a remote model; the action payload leaves this Mac."
    return {
        "provider": provider,
        "label": PROVIDER_LABELS.get(provider, provider),
        "mode": "remote" if remote else "local",
        "destination": destination,
        "remote": remote,
    }


def capability_readiness(
    *,
    profile: dict[str, Any],
    discovery: dict[str, Any],
    provider_is_available: bool,
    approved_projects: int,
    matching_model: str,
    telegram_configured: bool,
    smtp_configured: bool,
) -> dict[str, Any]:
    source_count = sum(int(discovery.get("counts", {}).get(kind, 0)) for kind in ("linkedin", "facebook", "career"))
    discovery_ready = source_count > 0
    discovery_attention = discovery_ready and discovery.get("level") in {"degraded", "limited", "unknown"}

    has_identity = bool(str(profile.get("name") or "").strip() and str(profile.get("email") or "").strip())
    has_experience = bool(profile.get("experience"))
    has_evidence = has_experience or approved_projects > 0
    provider = str(profile.get("drafting_provider") or "")
    preparation_ready = bool(has_identity and has_evidence and provider and provider_is_available)
    automatic_drafts_ready = bool(preparation_ready and matching_model)

    return {
        "discovery": {
            "ready": discovery_ready,
            "status": "attention" if discovery_attention else "ready" if discovery_ready else "not_configured",
            "label": "Discovery ready" if discovery_ready else "Add a job source",
            "detail": (
                discovery.get("label") or "Discovery is configured."
                if discovery_ready
                else "Add at least one LinkedIn search, Facebook group, or company career page."
            ),
            "source_count": source_count,
        },
        "application_preparation": {
            "ready": preparation_ready,
            "status": "ready" if preparation_ready else "not_ready",
            "label": "Application preparation ready" if preparation_ready else "Application preparation optional",
            "missing": [
                label for missing, label in (
                    (not has_identity, "name and email"),
                    (not has_evidence, "work history or one approved project"),
                    (not provider, "application writing provider"),
                    (bool(provider) and not provider_is_available, "an available application writing provider"),
                ) if missing
            ],
            "has_identity": has_identity,
            "has_evidence": has_evidence,
            "provider": provider_processing(provider),
        },
        "automatic_drafts": {
            "ready": automatic_drafts_ready,
            "status": "ready" if automatic_drafts_ready else "optional",
            "label": "Automatic draft preparation ready" if automatic_drafts_ready else "Automatic drafts optional",
            "missing": [] if automatic_drafts_ready else (
                ([] if preparation_ready else ["application preparation"]) +
                ([] if matching_model else ["local matching model"])
            ),
        },
        "review_delivery": {
            "ready": bool(telegram_configured or smtp_configured),
            "status": "ready" if telegram_configured or smtp_configured else "optional",
            "label": "Review/delivery integration connected" if telegram_configured or smtp_configured else "Review and delivery integrations optional",
            "telegram": telegram_configured,
            "email": smtp_configured,
        },
    }
