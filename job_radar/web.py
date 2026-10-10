from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import json
import logging
import re
import shutil
import smtplib
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
import pypdfium2 as pdfium
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field, HttpUrl

from .db import Database, new_id, now
from .discovery import merge_reason_label, source_coverage, split_observation, summarize_discovery
from .apply import (
    CONFIRMED_SUBMISSION_STATUSES,
    inspect_form,
    send_application,
    send_readiness,
    submission_attachment,
    submission_record,
    submission_resume_path,
)
from .auto_apply import AutoApplyManager
from .browser_login import BrowserLoginManager
from .chatgpt_handoff import ChatGPTInputManager, application_prompt, apply_chatgpt_reply
from .capabilities import capability_readiness, provider_processing
from .drafting import PROVIDERS, get_draft, prepare_draft, refresh_draft_projects, set_discovered_linkedin_destination, set_discovered_web_destination, set_unavailable_linkedin_destination, update_draft
from .evidence import generate_project_content, inspect_repository
from .feed_catalog import CAREER_SCAN_INTERVAL_MINUTES
from .feedback_learning import (
    apply_feedback_suggestion,
    dismiss_feedback_suggestion,
    feedback_suggestions,
)
from .facebook_groups import group_from_url, lookup_facebook_group_name
from .linkedin_searches import LINKEDIN_SCAN_INTERVAL_MINUTES, search_from_url
from .linkedin_application import discover_linkedin_apply
from .github import list_public_repositories
from .mail_config import save_smtp, send_test_email, smtp_config, smtp_config_fingerprint
from .local_analysis import clean_saved_analysis, list_local_models, validate_local_model
from .job_inbox import (application_filter_sql, enrich_jobs, mark_seen, release_due_snoozes,
                        set_decision, set_manual_applied, set_recruiting_outcome)
from .home_dashboard import build_home_dashboard
from .matching import MatchManager
from .notifications import discover_telegram_chats, save_telegram, telegram_config
from .preparation import preparation_preflight
from .ranking import rescore_vacancies, score_job
from .search_intent import (apply_auto_search_intent, fit_summary, migrate_search_intent,
                            normalize_search_intent, reset_search_preference, seniority_key)
from .resume_import import parse_resume_template
from .resume_extract import extract_resume
from .resume_pdf import editable_bullet_lines
from .seeds import seed
from .settings import Settings
from .scanner import ScanManager
from .service import service_path
from .social_browser import chrome_executable, social_login_at
from .work_queue import work_queue


log = logging.getLogger(__name__)


JOBS_ORDER = ("CASE WHEN v.analysis_status='done' THEN 0 ELSE 1 END, "
              "CASE WHEN v.analysis_status='done' THEN v.score END DESC, "
              "v.first_seen_at DESC,v.id")


JOB_VIEW_FILTER_KEYS = {
    "q", "inbox", "decision", "application", "outcome", "score", "freshness",
    "mode", "location", "source", "seniority", "fit", "sort",
}


class SourceInput(BaseModel):
    kind: Literal["linkedin", "facebook", "career"]
    name: str = ""
    url: HttpUrl
    employer_id: str | None = None
    enabled: bool = True
    interval_minutes: int = Field(default=240, ge=15, le=10080)
    config: dict[str, Any] = Field(default_factory=dict)


class BrowserLoginInput(BaseModel):
    site: Literal["linkedin", "facebook"]


class EmployerInput(BaseModel):
    name: str = Field(min_length=2)
    category: str = "custom"
    aliases: list[str] = Field(default_factory=list)
    career_url: str | None = None


class JobInput(BaseModel):
    company: str = Field(min_length=2)
    title: str = Field(min_length=2)
    description: str = Field(min_length=10)
    location: str = ""
    apply_url: HttpUrl | None = None


class StateInput(BaseModel):
    state: Literal["new", "interesting", "ignored", "prepare", "ready", "applied", "interview", "rejected", "offer"]
    reason: str | None = None


class DecisionInput(BaseModel):
    decision: Literal["undecided", "shortlisted", "ignored", "later"]
    reason: str | None = Field(default=None, max_length=200)
    snoozed_until: str | None = None


class RecruitingOutcomeInput(BaseModel):
    outcome: Literal["none", "interview", "rejected", "offer"]


class ManualAppliedInput(BaseModel):
    applied: bool = True


class SavedJobViewInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    filters: dict[str, Any] = Field(default_factory=dict)
    set_default: bool = False


class EvidenceInput(BaseModel):
    kind: Literal["experience", "project", "education", "achievement", "certification"]
    title: str = Field(min_length=2)
    claim: str = Field(min_length=5)
    approved: bool = False
    support: list[str] = Field(default_factory=list)


class PositionInput(BaseModel):
    company: str = Field(min_length=2)
    role: str = Field(min_length=2)
    dates: str = Field(min_length=2)
    bullets: list[str] = Field(min_length=1)


class ResumeImportInput(BaseModel):
    latex: str = Field(min_length=100, max_length=100_000)


class ProviderInput(BaseModel):
    provider: Literal["codex_local", "codex", "agy", "claude", "chatgpt_web"]


class MatchingModelInput(BaseModel):
    model: str = Field(min_length=2, max_length=100)


class AutoApplyInput(BaseModel):
    enabled: bool = False
    threshold: int | None = Field(default=None, ge=0, le=100)
    include_shortlisted: bool | None = None
    max_job_age_days: int | None = Field(default=None, ge=1, le=30)
    require_verified_destination: bool | None = None
    require_preferred_location: bool | None = None
    max_auto_drafts_per_day: int | None = Field(default=None, ge=1, le=50)
    max_review_notifications_per_day: int | None = Field(default=None, ge=1, le=50)


class SearchIntentInput(BaseModel):
    role_families: list[str] = Field(default_factory=list)
    seniority_levels: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    preferred_employers: list[str] = Field(default_factory=list)
    excluded_employers: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    max_required_experience_years: int | None = Field(default=None, ge=0, le=50)
    preference_modes: dict[str, str] = Field(default_factory=dict)
    hard_constraints: dict[str, bool] = Field(default_factory=dict)
    minimum_salary: int | None = Field(default=None, ge=0)
    salary_currency: str = "VND"
    salary_unknown_ok: bool = True
    strong_match_threshold: int = Field(default=80, ge=0, le=100)


class ProjectGenerationInput(BaseModel):
    provider: Literal["template", "codex_local", "codex", "agy", "claude"] | None = None


class RepositoryInput(BaseModel):
    url: HttpUrl
    provider: Literal["template", "codex_local", "codex", "agy", "claude"] | None = None


class PrepareInput(BaseModel):
    provider: Literal["template", "codex_local", "codex", "agy", "claude", "chatgpt_web"] | None = None
    prepare_anyway: bool = False


class SendInput(BaseModel):
    package_hash: str = Field(min_length=64, max_length=64)


class RegenerateInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    section: Literal["all", "summary", "experience", "projects", "education", "achievements", "skills", "message"] = "all"


class ChatGPTInput(BaseModel):
    instruction: str = Field(default="", max_length=2000)
    section: Literal["all", "summary", "experience", "projects", "education", "achievements", "skills", "message"] = "all"


class SmtpInput(BaseModel):
    host: str = Field(min_length=2)
    port: Literal[465, 587] = 587
    user: str = ""
    password: str = ""
    from_address: str = Field(min_length=3)


class TelegramInput(BaseModel):
    token: str = ""
    chat_id: str = Field(min_length=1)
    application_reviews: bool | None = None
    strong_job_alerts: bool | None = None
    daily_digest: bool | None = None
    digest_time: str | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None


class TelegramLookupInput(BaseModel):
    token: str = ""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    db = Database(settings.database_path)
    seed(db)
    profile = db.get_setting("profile", {})
    raw_intent = db.get_setting("search_intent", {})
    legacy_auto_apply = db.get_setting("auto_apply", {})
    migrated_intent = migrate_search_intent(
        raw_intent, profile, legacy_threshold=legacy_auto_apply.get("threshold", 80)
    )
    if migrated_intent != raw_intent:
        db.set_setting("search_intent", migrated_intent)
        if raw_intent:
            rescore_vacancies(db, profile, migrated_intent)
    clean_saved_analysis(db)
    scan_manager = ScanManager(db, settings)
    chatgpt_input = ChatGPTInputManager(settings, scan_manager.browser_lock, scan_manager.priority_browser, db=db)
    auto_apply_manager = AutoApplyManager(
        db, settings, scan_manager.browser_lock, scan_manager.priority_browser, chatgpt_input=chatgpt_input
    )
    match_manager = MatchManager(db, settings, auto_apply_manager)
    login_manager = BrowserLoginManager(db, settings, scan_manager.browser_lock, scan_manager.queue_due)
    ai_retry_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal ai_retry_task
        await scan_manager.start()
        await match_manager.start()
        await auto_apply_manager.start()
        try:
            yield
        finally:
            if ai_retry_task and not ai_retry_task.done():
                ai_retry_task.cancel()
                try:
                    await ai_retry_task
                except asyncio.CancelledError:
                    pass
            await login_manager.stop()
            await chatgpt_input.stop()
            await auto_apply_manager.stop()
            await match_manager.stop()
            await scan_manager.stop()

    app = FastAPI(title="Job Radar", version="0.1.0", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.scan_manager = scan_manager
    app.state.match_manager = match_manager
    app.state.auto_apply_manager = auto_apply_manager
    app.state.login_manager = login_manager
    app.state.chatgpt_input = chatgpt_input

    def provider_available(provider: str) -> bool:
        if provider == "template":
            return True
        if provider == "chatgpt_web":
            try:
                chrome_executable()
                return True
            except ValueError:
                return False
        command = "codex" if provider.startswith("codex") else provider
        return bool(shutil.which(command)) and (provider != "codex_local" or bool(shutil.which("ollama")))

    def configured_provider() -> str:
        provider = db.get_setting("profile", {}).get("drafting_provider", "")
        if not provider:
            raise HTTPException(409, "Choose a drafting provider in Settings first")
        if not provider_available(provider):
            raise HTTPException(422, f"{provider} is not available on this Mac; change it in Settings")
        return provider

    def configured_project_provider() -> str:
        provider = configured_provider()
        return "template" if provider == "chatgpt_web" else provider

    def save_profile(profile: dict[str, Any]) -> None:
        previous = db.get_setting("profile", {})
        db.set_setting("profile", profile)
        matching_fields = ("skills", "location", "relocation", "experience", "education", "summary")
        if any(previous.get(field) != profile.get(field) for field in matching_fields):
            preferences = apply_auto_search_intent(db.get_setting("search_intent", {}), profile)
            db.set_setting("search_intent", preferences)
            rescore_vacancies(db, profile, preferences)
            match_manager.wake()
            auto_apply_manager.wake()

    def attach_career_source(employer_id: str, name: str, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HTTPException(422, "Career page must be an HTTP or HTTPS URL")
        db.execute("UPDATE sources SET enabled=0 WHERE employer_id=? AND kind='career' AND url<>?", (employer_id, url))
        if db.one("SELECT id FROM sources WHERE employer_id=? AND kind='career' AND url=?", (employer_id, url)):
            db.execute("UPDATE sources SET enabled=1,interval_minutes=? WHERE employer_id=? AND kind='career' AND url=?",
                       (CAREER_SCAN_INTERVAL_MINUTES, employer_id, url))
        else:
            db.execute("INSERT INTO sources(id,kind,name,url,employer_id,interval_minutes,created_at) VALUES(?,?,?,?,?,?,?)",
                       (new_id(), "career", f"{name} careers", url, employer_id, CAREER_SCAN_INTERVAL_MINUTES, now()))

    def saved_job_views() -> list[dict[str, Any]]:
        views = db.get_setting("job_saved_views", [])
        return views if isinstance(views, list) else []

    def sanitize_job_view_filters(filters: dict[str, Any]) -> dict[str, str]:
        clean: dict[str, str] = {}
        for key, value in filters.items():
            if key not in JOB_VIEW_FILTER_KEYS or value in (None, ""):
                continue
            clean[key] = str(value)[:200]
        return clean

    @app.get("/")
    def index():
        static = Path(__file__).parent / "static"
        html = (static / "index.html").read_text()
        for asset, attribute in (("app.js", "src"), ("app.css", "href")):
            version = hashlib.sha256((static / asset).read_bytes()).hexdigest()[:12]
            html = html.replace(f'{attribute}="/{asset}"', f'{attribute}="/{asset}?v={version}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/app.js")
    def javascript():
        return FileResponse(Path(__file__).parent / "static" / "app.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-store"})

    @app.get("/app.css")
    def stylesheet():
        return FileResponse(Path(__file__).parent / "static" / "app.css", media_type="text/css",
                            headers={"Cache-Control": "no-store"})

    @app.get("/logo.svg")
    def logo():
        return FileResponse(Path(__file__).parent / "static" / "logo.svg", media_type="image/svg+xml")

    @app.get("/api/status")
    def status():
        counts = {}
        with db.connection() as conn:
            for table in ("sources", "evidence", "application_drafts", "submissions"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts["employers"] = conn.execute("SELECT COUNT(*) FROM employers WHERE coverage_status!='excluded_hcm'").fetchone()[0]
            counts["vacancies"] = conn.execute("SELECT COUNT(*) FROM vacancies v WHERE NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm')").fetchone()[0]
            counts["active_sources"] = conn.execute("SELECT COUNT(*) FROM sources WHERE enabled=1").fetchone()[0]
            counts["career_sources_enabled"] = conn.execute("SELECT COUNT(*) FROM sources WHERE enabled=1 AND kind='career'").fetchone()[0]
            threshold = normalize_search_intent(db.get_setting("search_intent", {}))["strong_match_threshold"]
            counts["unseen_jobs"] = conn.execute("SELECT COUNT(*) FROM vacancies WHERE seen_at IS NULL AND decision_state='undecided' AND snoozed_until IS NULL").fetchone()[0]
            counts["recent_jobs"] = conn.execute("SELECT COUNT(*) FROM vacancies WHERE julianday(first_seen_at)>=julianday('now','-1 day')").fetchone()[0]
            counts["drafts_needing_review"] = conn.execute("SELECT COUNT(*) FROM auto_application_attempts WHERE status IN ('awaiting_review','needs_review')").fetchone()[0]
            counts["analysis_failures"] = conn.execute("SELECT COUNT(*) FROM vacancies WHERE analysis_status='failed'").fetchone()[0]
        recent = db.all("SELECT scan_runs.*, sources.name AS source_name FROM scan_runs JOIN sources ON sources.id=scan_runs.source_id ORDER BY started_at DESC LIMIT 10")
        preferences = db.get_setting("search_intent", {})
        strong_jobs = []
        for item in db.all("SELECT id,title,company,score,score_detail FROM vacancies WHERE decision_state='undecided' AND snoozed_until IS NULL AND analysis_status='done' AND score>=? ORDER BY score DESC,first_seen_at DESC", (threshold,)):
            try:
                detail = json.loads(item.pop("score_detail") or "{}")
            except (TypeError, ValueError):
                detail = {}
            item.update(fit_summary(item.get("score"), detail, preferences))
            if item["fit_class"] == "strong":
                strong_jobs.append(item)
        counts["high_fit_new"] = len(strong_jobs)
        attention = {
            "jobs": strong_jobs[:4],
            "drafts": db.all("SELECT a.draft_id AS id,v.title,v.company,a.status FROM auto_application_attempts a JOIN vacancies v ON v.id=a.vacancy_id WHERE a.status IN ('awaiting_review','needs_review') AND a.draft_id IS NOT NULL ORDER BY a.updated_at DESC LIMIT 4"),
            "failures": db.all("SELECT id,title,company,analysis_error AS detail FROM vacancies WHERE analysis_status='failed' ORDER BY updated_at DESC LIMIT 4"),
        }
        discovery = summarize_discovery(sources())
        home = build_home_dashboard(db, discovery, threshold)
        counts["unseen_jobs"] = home["counts"]["unseen_jobs"]
        counts["high_fit_new"] = home["counts"]["strong_matches"]
        counts["drafts_needing_review"] = home["counts"]["drafts_to_review"]
        return {
            "counts": counts,
            "attention": attention,
            "recent_runs": recent,
            "data_dir": str(settings.data_dir),
            "strong_match_threshold": threshold,
            "home": home,
        }

    @app.get("/api/queue")
    async def queue():
        return work_queue(db, scan_manager, match_manager, auto_apply_manager)

    def setup_discovery() -> dict:
        """Read source health for readiness without counting every saved job."""
        linkedin_paused = bool(db.get_setting("linkedin_automation_paused", False))
        with db.connection() as conn:
            rows = [dict(row) for row in conn.execute("""
                SELECT s.id,s.name,s.kind,s.enabled,s.config,s.last_status,s.last_success_at,s.interval_minutes,
                       EXISTS(SELECT 1 FROM observations o JOIN vacancy_observations vo ON vo.observation_id=o.id
                              WHERE o.source_id=s.id) AS job_count
                FROM sources s
                WHERE COALESCE(json_extract(s.config,'$.retired'),0)=0
                  AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=s.employer_id AND e.coverage_status='excluded_hcm')
            """)]
            for source in rows:
                source["config"] = json.loads(source["config"])
                source["enabled"] = bool(source["enabled"])
                position = scan_manager.queue_position(source["id"])
                source["scan_state"] = (
                    "paused" if source["kind"] == "linkedin" and linkedin_paused else
                    "scanning" if source["id"] in scan_manager.active else
                    "queued" if position is not None else
                    "auto_off" if not source["enabled"] else
                    source["last_status"] or "not_scanned"
                )
                recent_runs = [dict(run) for run in conn.execute(
                    "SELECT status,observed_count,new_count,detail,started_at,finished_at FROM scan_runs "
                    "WHERE source_id=? ORDER BY started_at DESC,rowid DESC LIMIT 5", (source["id"],)
                )]
                source["coverage"] = source_coverage(source, recent_runs)
        return summarize_discovery(rows)

    @app.get("/api/setup")
    def setup_status():
        profile = db.get_setting("profile", {})
        mail = smtp_config(settings)
        smtp_test = db.get_setting("smtp_test", {})
        if smtp_test.get("fingerprint") != smtp_config_fingerprint(mail):
            smtp_test = {}
        telegram = telegram_config(settings)
        approved_projects = db.one("SELECT COUNT(*) AS count FROM evidence WHERE approved=1 AND kind='project'")["count"]
        smtp_ready = bool(mail.get("host") and mail.get("from") and
                          (mail.get("host", "").lower() != "smtp.gmail.com" or
                           (mail.get("user") and mail.get("password"))))
        telegram_ready = bool(telegram.get("token") and telegram.get("chat_id"))
        provider = str(profile.get("drafting_provider") or "")
        discovery = setup_discovery()
        capabilities = capability_readiness(
            profile=profile,
            discovery=discovery,
            provider_is_available=bool(provider and provider_available(provider)),
            approved_projects=approved_projects,
            matching_model=str(db.get_setting("matching_model", "")),
            telegram_configured=telegram_ready,
            smtp_configured=smtp_ready,
        )
        availability = {name: bool(shutil.which(name)) for name in ("codex", "agy", "claude", "ollama")}
        availability["chatgpt_web"] = provider_available("chatgpt_web")
        chatgpt_status = chatgpt_input.status()
        availability["chatgpt_web_logged_in"] = chatgpt_status["logged_in"]
        return {
            "profile_complete": bool(profile.get("name") and profile.get("email")),
            "selected_provider": provider,
            "approved_evidence": approved_projects,
            "facebook_groups": db.one("SELECT COUNT(*) AS count FROM sources WHERE kind='facebook' AND enabled=1")["count"],
            "linkedin_searches": db.one("SELECT COUNT(*) AS count FROM sources WHERE kind='linkedin' AND enabled=1")["count"],
            "linkedin_automation_paused": bool(db.get_setting("linkedin_automation_paused", False)),
            "browser": login_manager.status(),
            "chatgpt": chatgpt_status,
            "chatgpt_logged_in": chatgpt_status["logged_in"],
            "smtp_configured": smtp_ready,
            "smtp_host": mail.get("host", ""),
            "smtp_port": mail.get("port", 587),
            "smtp_user": mail.get("user", ""),
            "smtp_from": mail.get("from", ""),
            "smtp_test": {key: smtp_test[key] for key in ("status", "recipient", "checked_at", "detail") if key in smtp_test},
            "telegram_configured": telegram_ready,
            "telegram_chat_id": telegram.get("chat_id", ""),
            "telegram_notifications": {
                "modes": telegram.get("modes", {}),
                "digest_time": telegram.get("digest_time", "18:00"),
                "quiet_start": telegram.get("quiet_start", ""),
                "quiet_end": telegram.get("quiet_end", ""),
            },
            "matching": match_manager.status(),
            "service_installed": service_path().exists(),
            "providers": availability,
            "provider_processing": {name: provider_processing(name) for name in ("codex_local", "codex", "agy", "claude", "chatgpt_web")},
            "capabilities": capabilities,
        }

    @app.post("/api/setup/browser/start")
    async def start_browser_login(payload: BrowserLoginInput):
        try:
            return login_manager.start(payload.site)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/setup/smtp")
    def configure_mail(payload: SmtpInput):
        previous = smtp_config(settings)
        host = payload.host.strip()
        password = payload.password or (previous.get("password", "") if host.lower() == previous.get("host", "").lower() else "")
        if host.lower() == "smtp.gmail.com" and (not payload.user.strip() or not password):
            raise HTTPException(422, "Gmail needs your full email address and a Google app password")
        save_smtp(settings, {"host": host, "port": payload.port, "user": payload.user.strip(),
                             "password": password, "from": payload.from_address.strip()})
        db.set_setting("smtp_test", {})
        return {"configured": True}

    @app.post("/api/setup/smtp/test")
    async def test_mail():
        config = smtp_config(settings)
        if not config.get("host") or not config.get("from"):
            raise HTTPException(409, "Save your email settings before sending a test")
        fingerprint = smtp_config_fingerprint(config)
        checked_at = now()
        try:
            recipient = await asyncio.to_thread(send_test_email, settings)
        except Exception as error:
            if isinstance(error, smtplib.SMTPAuthenticationError):
                detail = "SMTP rejected the sign-in. For Gmail, check your full address and Google app password."
            elif isinstance(error, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused)):
                detail = "SMTP rejected the From address. Check the address saved in Email applications."
            elif isinstance(error, (ssl.SSLError, smtplib.SMTPNotSupportedError)):
                detail = "Secure SMTP connection failed. Check the server and port."
            elif isinstance(error, (TimeoutError, OSError)):
                detail = "Could not reach the SMTP server. Check the server, port, and network."
            elif isinstance(error, ValueError):
                detail = str(error)
            else:
                detail = "The SMTP server did not accept the test email. Check your email settings."
            db.set_setting("smtp_test", {"status": "failed", "recipient": config.get("from", ""),
                                         "checked_at": checked_at, "detail": detail, "fingerprint": fingerprint})
            raise HTTPException(502, detail) from error
        result = {"status": "accepted", "recipient": recipient, "checked_at": checked_at,
                  "detail": "SMTP accepted the test email. Check your inbox or spam folder."}
        db.set_setting("smtp_test", {**result, "fingerprint": fingerprint})
        return result

    @app.delete("/api/setup/smtp")
    def remove_mail():
        (settings.data_dir / "smtp.json").unlink(missing_ok=True)
        db.set_setting("smtp_test", {})
        return {"configured": False}

    @app.post("/api/setup/telegram")
    def configure_alerts(payload: TelegramInput):
        previous = telegram_config(settings)
        token = payload.token.strip() or previous.get("token", "")
        if not token:
            raise HTTPException(422, "Enter a bot token to configure Telegram notifications")
        previous_modes = previous.get("modes", {})
        config = {
            "token": token,
            "chat_id": payload.chat_id,
            "modes": {
                "application_reviews": (
                    previous_modes.get("application_reviews", True)
                    if payload.application_reviews is None else payload.application_reviews
                ),
                "strong_job_alerts": (
                    previous_modes.get("strong_job_alerts", False)
                    if payload.strong_job_alerts is None else payload.strong_job_alerts
                ),
                "daily_digest": (
                    previous_modes.get("daily_digest", False)
                    if payload.daily_digest is None else payload.daily_digest
                ),
            },
            "digest_time": (
                previous.get("digest_time", "18:00")
                if payload.digest_time is None else payload.digest_time
            ),
            "quiet_start": (
                previous.get("quiet_start", "")
                if payload.quiet_start is None else payload.quiet_start
            ),
            "quiet_end": (
                previous.get("quiet_end", "")
                if payload.quiet_end is None else payload.quiet_end
            ),
        }
        try:
            save_telegram(settings, config)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        destination_changed = (
            token != previous.get("token")
            or str(payload.chat_id) != str(previous.get("chat_id", ""))
        )
        review_mode_changed = (
            bool(previous.get("modes", {}).get("application_reviews", True))
            != bool(config["modes"]["application_reviews"])
        )
        if destination_changed:
            db.set_setting("telegram_review_offset", 0)
            db.set_setting("telegram_digest_last_date", "")
        if destination_changed or review_mode_changed:
            review_status = "pending" if config["modes"]["application_reviews"] else "disabled"
            db.execute(
                "UPDATE auto_application_attempts SET telegram_status=?,telegram_error=NULL,"
                "telegram_message_id=CASE WHEN ? THEN NULL ELSE telegram_message_id END "
                "WHERE status IN ('awaiting_review','needs_review') AND draft_id IS NOT NULL",
                (review_status, int(destination_changed)),
            )
        saved = telegram_config(settings)
        return {
            "configured": True,
            "notifications": {
                "modes": saved.get("modes", {}),
                "digest_time": saved.get("digest_time", "18:00"),
                "quiet_start": saved.get("quiet_start", ""),
                "quiet_end": saved.get("quiet_end", ""),
            },
        }

    @app.post("/api/setup/telegram/chats")
    async def find_telegram_chat(payload: TelegramLookupInput):
        token = payload.token.strip() or telegram_config(settings).get("token", "")
        try:
            return {"chats": await discover_telegram_chats(token)}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.delete("/api/setup/telegram")
    def remove_alerts():
        (settings.data_dir / "telegram.json").unlink(missing_ok=True)
        return {"configured": False}

    @app.get("/api/matching/models")
    def local_models():
        try:
            models = list_local_models()
            available = []
            for item in models:
                try:
                    validate_local_model(item["name"])
                    available.append(item)
                except (ValueError, RuntimeError):
                    continue
            return {"models": available, "matching": match_manager.status(), "error": None}
        except RuntimeError as error:
            return {"models": [], "matching": match_manager.status(), "error": str(error)}

    @app.get("/api/matching/status")
    def matching_status():
        return match_manager.status()

    @app.put("/api/matching/model")
    async def choose_matching_model(payload: MatchingModelInput):
        try:
            await asyncio.to_thread(match_manager.select_model, payload.model)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error
        return match_manager.status()

    @app.post("/api/matching/model/download", status_code=202)
    async def download_matching_model():
        try:
            return match_manager.download_recommended()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.delete("/api/matching/model/download")
    async def cancel_matching_model_download():
        return match_manager.cancel_download()

    @app.get("/api/matching/failures")
    def matching_failures():
        return match_manager.failures()

    @app.post("/api/matching/retry-failed", status_code=202)
    def retry_failed_matching():
        try:
            return {"queued": match_manager.retry_failed()}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/jobs/{job_id}/dismiss-analysis")
    def dismiss_job_analysis(job_id: str):
        try:
            match_manager.dismiss_failure(job_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"dismissed": True}

    @app.get("/api/profile")
    def get_profile():
        return db.get_setting("profile", {})

    @app.get("/api/search-intent")
    def get_search_intent():
        profile = db.get_setting("profile", {})
        preferences = apply_auto_search_intent(db.get_setting("search_intent", {}), profile)
        if preferences != db.get_setting("search_intent", {}):
            db.set_setting("search_intent", preferences)
        return preferences

    @app.put("/api/search-intent")
    def put_search_intent(payload: SearchIntentInput):
        profile = db.get_setting("profile", {})
        preferences = apply_auto_search_intent(payload.model_dump(), profile)
        db.set_setting("search_intent", preferences)
        rescore_vacancies(db, profile, preferences)
        match_manager.wake()
        auto_apply_manager.wake()
        return preferences

    @app.post("/api/search-intent/reset/{preference}")
    def reset_search_intent_preference(preference: str):
        profile = db.get_setting("profile", {})
        try:
            preferences = reset_search_preference(db.get_setting("search_intent", {}), profile, preference)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        db.set_setting("search_intent", preferences)
        rescore_vacancies(db, profile, preferences)
        match_manager.wake()
        auto_apply_manager.wake()
        return preferences

    @app.get("/api/preferences/suggestions")
    def get_preference_suggestions():
        return {"items": feedback_suggestions(db)}

    @app.post("/api/preferences/suggestions/{suggestion_id}/apply")
    def apply_preference_suggestion(suggestion_id: str):
        try:
            preferences = apply_feedback_suggestion(db, suggestion_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        profile = db.get_setting("profile", {})
        rescore_vacancies(db, profile, preferences)
        match_manager.wake()
        auto_apply_manager.wake()
        return {"preferences": preferences, "items": feedback_suggestions(db)}

    @app.post("/api/preferences/suggestions/{suggestion_id}/dismiss")
    def dismiss_preference_suggestion(suggestion_id: str):
        try:
            dismiss_feedback_suggestion(db, suggestion_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        return {"dismissed": True, "items": feedback_suggestions(db)}


    @app.put("/api/profile")
    def put_profile(profile: dict[str, Any] = Body(...)):
        if not isinstance(profile.get("name", ""), str) or not isinstance(profile.get("skills", []), list):
            raise HTTPException(422, "Profile must include a name and skills list")
        if profile.get("drafting_provider") and profile["drafting_provider"] not in {"codex_local", "codex", "agy", "claude", "chatgpt_web"}:
            raise HTTPException(422, "Unsupported drafting provider")
        save_profile(profile)
        return profile

    @app.put("/api/profile/provider")
    def set_provider(payload: ProviderInput):
        if not provider_available(payload.provider):
            raise HTTPException(422, f"{payload.provider} is not available on this Mac")
        profile = db.get_setting("profile", {})
        profile["drafting_provider"] = payload.provider
        save_profile(profile)
        return {"provider": payload.provider, "mode": PROVIDERS[payload.provider],
                "automatic_drafts_paused": False}

    @app.post("/api/profile/resume/pdf")
    async def import_pdf_resume(file: UploadFile = File(...), provider: str = Form("")):
        provider = provider.strip() or str(db.get_setting("profile", {}).get("drafting_provider") or "")
        if provider not in {"codex_local", "codex", "agy", "claude"}:
            raise HTTPException(422, "Choose how this resume should be processed before importing it")
        if not provider_available(provider):
            raise HTTPException(422, f"{provider} is not available on this Mac")
        data = await file.read(10_000_001)
        try:
            extracted = await asyncio.to_thread(extract_resume, data, provider)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error
        profile = {**db.get_setting("profile", {}), **extracted}
        save_profile(profile)
        return {"positions": len(profile["experience"]), "education": len(profile["education"]),
                "achievements": len(profile["achievements"]), "provider": provider,
                "processing": provider_processing(provider),
                "review": "Review the extracted fields and positions before preparing an application"}

    @app.post("/api/profile/import-latex")
    def import_latex(payload: ResumeImportInput):
        try:
            imported = parse_resume_template(payload.latex)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        profile = {**db.get_setting("profile", {}), **imported}
        save_profile(profile)
        return {"positions": len(imported["experience"]), "education": len(imported["education"]),
                "achievements": len(imported["achievements"]), "skill_groups": len(imported["skill_groups"])}

    @app.get("/api/positions")
    def positions():
        return db.get_setting("profile", {}).get("experience", [])

    @app.post("/api/positions", status_code=201)
    def add_position(payload: PositionInput):
        profile = db.get_setting("profile", {})
        item = {"id": new_id(), **payload.model_dump()}
        profile.setdefault("experience", []).append(item)
        save_profile(profile)
        return item

    @app.put("/api/positions/{position_id}")
    def edit_position(position_id: str, payload: PositionInput):
        profile = db.get_setting("profile", {})
        for item in profile.get("experience", []):
            if item.get("id") == position_id:
                item.update(payload.model_dump())
                save_profile(profile)
                return item
        raise HTTPException(404, "Position not found")

    @app.delete("/api/positions/{position_id}")
    def delete_position(position_id: str):
        profile = db.get_setting("profile", {})
        items = profile.get("experience", [])
        kept = [item for item in items if item.get("id") != position_id]
        if len(kept) == len(items):
            raise HTTPException(404, "Position not found")
        profile["experience"] = kept
        save_profile(profile)
        return {"deleted": True}

    @app.get("/api/sources")
    def sources(kind: str | None = None):
        linkedin_paused = bool(db.get_setting("linkedin_automation_paused", False))
        rows = db.all("""
            WITH latest_scan AS (
                SELECT source_id, started_at, finished_at, observed_count, new_count FROM (
                    SELECT source_id, started_at, finished_at, observed_count, new_count,
                           ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY started_at DESC, rowid DESC) AS rank
                    FROM scan_runs WHERE status IN ('success', 'empty')
                ) WHERE rank=1
            ), first_source AS (
                SELECT vo.vacancy_id,
                       COALESCE(json_extract(original.config,'$.merged_into'),o.source_id) AS source_id,
                       ROW_NUMBER() OVER (PARTITION BY vo.vacancy_id ORDER BY o.first_seen_at, o.id) AS rank
                FROM observations o
                JOIN sources original ON original.id=o.source_id
                JOIN vacancy_observations vo ON vo.observation_id=o.id
            ), job_counts AS (
                SELECT source_id, COUNT(*) AS job_count FROM first_source WHERE rank=1 GROUP BY source_id
            )
            SELECT s.*, COALESCE(c.job_count,0) AS job_count,
                   CASE WHEN s.last_success_at IS NOT NULL THEN COALESCE(l.new_count,0) ELSE 0 END AS new_job_count,
                   CASE WHEN s.last_success_at IS NOT NULL THEN l.observed_count END AS latest_observed_count
            FROM sources s LEFT JOIN job_counts c ON c.source_id=s.id
            LEFT JOIN latest_scan l ON l.source_id=s.id
            WHERE (? IS NULL OR s.kind=?) AND COALESCE(json_extract(s.config,'$.retired'),0)=0 AND NOT EXISTS(
                SELECT 1 FROM employers e WHERE e.id=s.employer_id AND e.coverage_status='excluded_hcm')
            ORDER BY s.kind,s.name
        """, (kind, kind))
        for row in rows:
            row["config"] = json.loads(row["config"])
            row["enabled"] = bool(row["enabled"])
            row["queue_position"] = scan_manager.queue_position(row["id"])
            row["scan_state"] = (
                "paused" if row["kind"] == "linkedin" and linkedin_paused else
                "scanning" if row["id"] in scan_manager.active else
                "queued" if row["queue_position"] is not None else
                "auto_off" if not row["enabled"] else
                row["last_status"] or "not_scanned"
            )
            recent_runs = db.all(
                "SELECT status,observed_count,new_count,detail,started_at,finished_at FROM scan_runs "
                "WHERE source_id=? ORDER BY started_at DESC,rowid DESC LIMIT 5",
                (row["id"],),
            )
            row["coverage"] = source_coverage(row, recent_runs)
        return rows

    def coverage_summary(snapshot: list[dict]) -> dict:
        summary = summarize_discovery(snapshot)
        intent = db.get_setting("search_intent", {})
        summary["intent"] = {
            key: intent.get(key) for key in ("roles", "locations", "work_modes")
            if intent.get(key)
        }
        summary["sources"] = [
            {"id": source["id"], "name": source["name"], "kind": source["kind"],
             "coverage": source["coverage"]}
            for source in snapshot if source["enabled"]
        ]
        return summary

    @app.get("/api/discovery/coverage")
    def discovery_coverage():
        return coverage_summary(sources())

    @app.get("/api/sources/overview")
    def sources_overview():
        snapshot = sources()
        return {"sources": snapshot, "coverage": coverage_summary(snapshot)}

    @app.post("/api/sources", status_code=201)
    async def add_source(source: SourceInput):
        if source.employer_id and not db.one("SELECT id FROM employers WHERE id=?", (source.employer_id,)):
            raise HTTPException(404, "Employer not found")
        name = source.name.strip()
        url = str(source.url)
        if source.kind == "facebook":
            try:
                url, name = group_from_url(url)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            existing = db.one("SELECT id,name,enabled,url FROM sources WHERE kind='facebook' AND url=?", (url,))
            if not existing:
                for saved in db.all("SELECT id,name,enabled,url FROM sources WHERE kind='facebook'"):
                    try:
                        saved_url, _ = group_from_url(saved["url"])
                    except ValueError:
                        continue
                    if saved_url == url:
                        existing = saved
                        break
            if existing:
                db.execute("UPDATE sources SET enabled=1,url=? WHERE id=?", (url, existing["id"]))
                return {"id": existing["id"], "name": existing["name"], "existing": True}
            if (social_login_at(db, "facebook") and settings.browser_profile.is_dir()
                    and not db.get_setting("social_reauth_required_facebook")):
                async with scan_manager.browser_lock:
                    try:
                        name = await lookup_facebook_group_name(settings, url) or name
                    except Exception:
                        pass
        elif source.kind == "linkedin":
            try:
                url, name = search_from_url(url)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            existing = None
            for saved in db.all("SELECT id,name,enabled,url FROM sources WHERE kind='linkedin' "
                                "AND COALESCE(json_extract(config,'$.retired'),0)=0"):
                try:
                    saved_url, _ = search_from_url(saved["url"])
                except ValueError:
                    continue
                if saved_url == url:
                    existing = saved
                    break
            if existing:
                db.execute("UPDATE sources SET enabled=1,url=?,interval_minutes=? WHERE id=?",
                           (url, LINKEDIN_SCAN_INTERVAL_MINUTES, existing["id"]))
                return {"id": existing["id"], "name": existing["name"], "existing": True}
        elif len(name) < 2:
            raise HTTPException(422, "Enter a source name")
        identifier = new_id()
        db.execute(
            "INSERT INTO sources(id,kind,name,url,employer_id,enabled,interval_minutes,config,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (identifier, source.kind, name, url, source.employer_id,
             int(source.enabled), LINKEDIN_SCAN_INTERVAL_MINUTES if source.kind == "linkedin" else
             CAREER_SCAN_INTERVAL_MINUTES if source.kind == "career" else source.interval_minutes,
             json.dumps(source.config), now()),
        )
        return {"id": identifier, "name": name}

    @app.patch("/api/sources/{source_id}")
    def edit_source(source_id: str, updates: dict[str, Any] = Body(...)):
        row = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
        if not row:
            raise HTTPException(404, "Source not found")
        if json.loads(row["config"]).get("retired"):
            raise HTTPException(404, "Source not found")
        allowed = {"name", "url", "enabled", "interval_minutes", "config"}
        if not updates or set(updates) - allowed:
            raise HTTPException(422, "Unsupported source fields")
        merged = {**row, "config": json.loads(row["config"]), **updates}
        validated = SourceInput(**merged)
        if len(validated.name.strip()) < 2:
            raise HTTPException(422, "Enter a source name")
        url = str(validated.url)
        interval_minutes = validated.interval_minutes
        if validated.kind == "linkedin":
            try:
                url, _ = search_from_url(url)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            interval_minutes = LINKEDIN_SCAN_INTERVAL_MINUTES
        elif validated.kind == "career":
            interval_minutes = CAREER_SCAN_INTERVAL_MINUTES
        db.execute(
            "UPDATE sources SET name=?,url=?,enabled=?,interval_minutes=?,config=? WHERE id=?",
            (validated.name, url, int(validated.enabled),
             interval_minutes, json.dumps(validated.config), source_id),
        )
        return {"id": source_id}

    @app.post("/api/sources/{source_id}/scan")
    async def scan_one(source_id: str):
        source = db.one("SELECT id,kind FROM sources WHERE id=? AND COALESCE(json_extract(config,'$.retired'),0)=0", (source_id,))
        if not source:
            raise HTTPException(404, "Source not found")
        if source["kind"] == "linkedin" and db.get_setting("linkedin_automation_paused", False):
            raise HTTPException(409, "LinkedIn checks are paused after an account activity warning. Open LinkedIn manually.")
        queued = scan_manager.queue_sources([source_id], manual=True)
        if not queued and source_id not in scan_manager.active and scan_manager.queue_position(source_id) is None:
            raise HTTPException(409, "Sign in to this source in My profile before scanning")
        return {"status": "queued" if queued else "already_queued",
                "position": scan_manager.queue_position(source_id)}

    @app.post("/api/scan/now")
    async def scan_now():
        rows = db.all(
            "SELECT id,kind FROM sources WHERE enabled=1 "
            "AND COALESCE(json_extract(config,'$.retired'),0)=0 "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=sources.employer_id AND e.coverage_status='excluded_hcm')"
        )
        source_ids = [row["id"] for row in rows]
        queued = scan_manager.queue_sources(source_ids, manual=True)
        blocked = [
            row["kind"] for row in rows if row["kind"] in ("linkedin", "facebook") and (
                not social_login_at(db, row["kind"]) or db.get_setting(f"social_reauth_required_{row['kind']}")
            )
        ]
        return {
            "queued": queued,
            "already_running_or_queued": sum(
                1 for source_id in source_ids
                if source_id in scan_manager.active or scan_manager.queue_position(source_id) is not None
            ) - queued,
            "sign_in_needed": sorted(set(blocked)),
            "total_sources": len(source_ids),
        }

    @app.post("/api/scan/due")
    async def scan_due():
        return {"queued": scan_manager.queue_due()}

    @app.post("/api/scan/unscanned")
    async def scan_unscanned():
        return {**scan_manager.queue_unscanned(),
                "waiting": len(scan_manager.pending), "scanning": len(scan_manager.active)}

    @app.get("/api/employers")
    def employers(q: str = "", category: str = "", limit: int = Query(300, ge=1, le=2000)):
        return db.all(
            "SELECT e.*,CASE "
            "WHEN EXISTS(SELECT 1 FROM sources s WHERE s.employer_id=e.id AND s.enabled=1 AND s.last_status IN ('failed','auth_required')) THEN 'temporarily_unavailable' "
            "WHEN EXISTS(SELECT 1 FROM sources s WHERE s.employer_id=e.id AND s.enabled=1) THEN 'watching' "
            "WHEN e.career_url IS NULL OR e.career_url='' THEN 'career_page_needed' "
            "ELSE 'manual_only' END AS live_coverage "
            "FROM employers e WHERE e.coverage_status!='excluded_hcm' AND name LIKE ? AND (?='' OR category=?) ORDER BY name LIMIT ?",
            (f"%{q}%", category, category, limit),
        )

    @app.get("/api/employers/page")
    def employers_page(q: str = "", category: str = "", coverage: str = "all", page: int = Query(1, ge=1),
                       page_size: int = Query(48, ge=1, le=100)):
        if coverage not in {"all", "watching"}:
            raise HTTPException(422, "Unknown employer coverage filter")
        where = "e.coverage_status!='excluded_hcm' AND e.name LIKE ? AND (?='' OR e.category=?)"
        if coverage == "watching":
            where += " AND EXISTS(SELECT 1 FROM sources active WHERE active.employer_id=e.id AND active.enabled=1)"
        values = (f"%{q}%", category, category)
        total = db.one(f"SELECT COUNT(*) AS count FROM employers e WHERE {where}", values)["count"]
        rows = db.all(
            "SELECT e.*,CASE "
            "WHEN EXISTS(SELECT 1 FROM sources s WHERE s.employer_id=e.id AND s.enabled=1 AND s.last_status IN ('failed','auth_required')) THEN 'temporarily_unavailable' "
            "WHEN EXISTS(SELECT 1 FROM sources s WHERE s.employer_id=e.id AND s.enabled=1) THEN 'watching' "
            "WHEN e.career_url IS NULL OR e.career_url='' THEN 'career_page_needed' "
            "ELSE 'manual_only' END AS live_coverage "
            f"FROM employers e WHERE {where} ORDER BY e.name LIMIT ? OFFSET ?",
            (*values, page_size, (page - 1) * page_size),
        )
        return {"items": rows, "page": page, "page_size": page_size, "total": total,
                "pages": max(1, (total + page_size - 1) // page_size)}

    @app.post("/api/employers", status_code=201)
    def add_employer(employer: EmployerInput):
        if db.one("SELECT id FROM employers WHERE lower(name)=lower(?)", (employer.name,)):
            raise HTTPException(409, "Employer already exists")
        identifier = new_id()
        if employer.career_url:
            parts = urlsplit(employer.career_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise HTTPException(422, "Career page must be an HTTP or HTTPS URL")
        db.execute(
            "INSERT INTO employers(id,name,category,aliases,career_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (identifier, employer.name, employer.category,
             json.dumps(employer.aliases, ensure_ascii=False), employer.career_url, now(), now()),
        )
        if employer.career_url:
            attach_career_source(identifier, employer.name, employer.career_url)
        return {"id": identifier}

    @app.patch("/api/employers/{employer_id}")
    def edit_employer(employer_id: str, updates: dict[str, Any] = Body(...)):
        employer = db.one("SELECT * FROM employers WHERE id=?", (employer_id,))
        if not employer:
            raise HTTPException(404, "Employer not found")
        if set(updates) != {"career_url"} or not isinstance(updates["career_url"], str):
            raise HTTPException(422, "Provide a career_url")
        url = updates["career_url"].strip()
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HTTPException(422, "Career page must be an HTTP or HTTPS URL")
        db.execute("UPDATE employers SET career_url=?,updated_at=? WHERE id=?", (url, now(), employer_id))
        attach_career_source(employer_id, employer["name"], url)
        return {"id": employer_id, "career_url": url}

    def attach_job_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return rows
        placeholders = ",".join("?" for _ in rows)
        origins = db.all(
            "SELECT vo.vacancy_id,o.url,o.last_seen_at,s.kind,s.name FROM vacancy_observations vo "
            "JOIN observations o ON o.id=vo.observation_id JOIN sources s ON s.id=o.source_id "
            f"WHERE vo.vacancy_id IN ({placeholders}) ORDER BY o.last_seen_at DESC",
            tuple(row["id"] for row in rows),
        )
        by_id: dict[str, dict[str, Any]] = {}
        for origin in origins:
            by_id.setdefault(origin["vacancy_id"], {
                key: origin[key] for key in ("url", "last_seen_at", "kind", "name")
            })
        for row in rows:
            row["source"] = by_id.get(row["id"])
        return rows

    def legacy_job_state_filter(state: str) -> tuple[str, str]:
        return {
            "new": ("decision", "undecided"),
            "interesting": ("decision", "shortlisted"),
            "ignored": ("decision", "ignored"),
            "prepare": ("application", "draft_ready"),
            "ready": ("application", "draft_ready"),
            "applied": ("application", "applied"),
            "interview": ("outcome", "interview"),
            "rejected": ("outcome", "rejected"),
            "offer": ("outcome", "offer"),
        }.get(state, ("", ""))

    @app.post("/api/jobs/visit")
    def visit_jobs():
        release_due_snoozes(db)
        previous = db.get_setting("jobs_last_visit_at")
        current = now()
        db.set_setting("jobs_last_visit_at", current)
        base = (
            "decision_state='undecided' AND snoozed_until IS NULL "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=vacancies.employer_id "
            "AND e.coverage_status='excluded_hcm')"
        )
        unseen = db.one(
            f"SELECT COUNT(*) AS count FROM vacancies WHERE {base} AND seen_at IS NULL"
        )["count"]
        since = unseen if not previous else db.one(
            f"SELECT COUNT(*) AS count FROM vacancies WHERE {base} AND datetime(first_seen_at)>datetime(?)",
            (previous,),
        )["count"]
        return {"previous": previous, "current": current, "unseen": unseen, "since_last_visit": since}

    @app.get("/api/jobs/views")
    def list_job_views():
        return saved_job_views()

    @app.post("/api/jobs/views", status_code=201)
    def save_job_view(payload: SavedJobViewInput):
        views = saved_job_views()
        if len(views) >= 20:
            raise HTTPException(409, "Delete an old saved view before adding another")
        identifier = new_id()
        view = {
            "id": identifier,
            "name": payload.name.strip(),
            "filters": sanitize_job_view_filters(payload.filters),
            "default": bool(payload.set_default),
            "created_at": now(),
        }
        if view["default"]:
            for existing in views:
                existing["default"] = False
        views.append(view)
        db.set_setting("job_saved_views", views)
        return view

    @app.delete("/api/jobs/views/{view_id}")
    def delete_job_view(view_id: str):
        views = saved_job_views()
        remaining = [view for view in views if view.get("id") != view_id]
        if len(remaining) == len(views):
            raise HTTPException(404, "Saved view not found")
        db.set_setting("job_saved_views", remaining)
        return {"deleted": True}

    @app.get("/api/jobs")
    def jobs(q: str = "", state: str = "", limit: int = Query(100, ge=1, le=500)):
        release_due_snoozes(db)
        conditions = [
            "NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm')"
        ]
        values: list[Any] = []
        dimension, value = legacy_job_state_filter(state)
        if dimension == "decision":
            conditions.append("v.decision_state=?")
            values.append(value)
        elif dimension == "outcome":
            conditions.append("v.recruiting_outcome=?")
            values.append(value)
        elif dimension == "application":
            conditions.append(application_filter_sql(value))
        for term in q.strip().split():
            conditions.append("(v.title LIKE ? ESCAPE '\\' OR v.company LIKE ? ESCAPE '\\' OR v.description LIKE ? ESCAPE '\\')")
            pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            values.extend([pattern] * 3)
        rows = db.all(
            f"SELECT v.* FROM vacancies v WHERE {' AND '.join(conditions)} ORDER BY {JOBS_ORDER} LIMIT ?",
            (*values, limit),
        )
        return attach_job_sources(enrich_jobs(db, rows))

    @app.get("/api/jobs/page")
    def jobs_page(q: str = "", decision: str = "", application: str = "", outcome: str = "",
                  inbox: str = "", since: str = "", state: str = "",
                  focus_id: str = "",
                  min_score: int | None = Query(None, ge=0, le=100),
                  freshness: int | None = Query(None, ge=1, le=3650), work_mode: str = "",
                  location: str = "", source: str = "", seniority: str = "", fit: str = "", sort: str = "best",
                  page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50)):
        release_due_snoozes(db)
        if state and not any((decision, application, outcome)):
            dimension, legacy_value = legacy_job_state_filter(state)
            if dimension == "decision":
                decision = legacy_value
            elif dimension == "application":
                application = legacy_value
            elif dimension == "outcome":
                outcome = legacy_value
        if decision and decision not in {"undecided", "shortlisted", "ignored", "later"}:
            raise HTTPException(422, "Unsupported job decision filter")
        if outcome and outcome not in {"none", "interview", "rejected", "offer"}:
            raise HTTPException(422, "Unsupported recruiting outcome filter")
        if inbox and inbox not in {"since_last_visit", "unseen", "all"}:
            raise HTTPException(422, "Unsupported inbox filter")
        if fit and fit not in {"eligible", "outside", "all"}:
            raise HTTPException(422, "Unsupported search-scope filter")

        terms = q.strip().split()
        conditions = [
            "NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm')",
        ]
        values: list[Any] = []
        if decision:
            conditions.append("v.decision_state=?")
            values.append(decision)
        if outcome:
            conditions.append("v.recruiting_outcome=?")
            values.append(outcome)
        if application:
            try:
                conditions.append(application_filter_sql(application))
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
        if fit == "eligible":
            conditions.append("COALESCE(json_array_length(json_extract(v.score_detail,'$.hard_exclusions')),0)=0")
        elif fit == "outside":
            conditions.append("COALESCE(json_array_length(json_extract(v.score_detail,'$.hard_exclusions')),0)>0")
        if inbox == "unseen":
            conditions.extend(["v.seen_at IS NULL", "v.decision_state='undecided'", "v.snoozed_until IS NULL"])
        elif inbox == "since_last_visit":
            conditions.extend(["v.decision_state='undecided'", "v.snoozed_until IS NULL"])
            if since:
                conditions.append("datetime(v.first_seen_at)>datetime(?)")
                values.append(since)
        for term in terms:
            conditions.append("(v.title LIKE ? ESCAPE '\\' OR v.company LIKE ? ESCAPE '\\' OR v.description LIKE ? ESCAPE '\\')")
            pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            values.extend([pattern] * 3)
        if min_score is not None:
            conditions.append("v.analysis_status='done' AND v.score>=?")
            values.append(min_score)
        if freshness is not None:
            conditions.append("datetime(COALESCE(v.published_at,v.first_seen_at)) >= datetime('now', ?)")
            values.append(f"-{freshness} days")
        if work_mode:
            conditions.append("replace(replace(lower(COALESCE(NULLIF(v.work_mode,''), json_extract(v.score_detail,'$.facts.work_mode'), '')),'-',''),' ','')=replace(replace(lower(?),'-',''),' ','')")
            values.append(work_mode)
        if location:
            conditions.append("lower(COALESCE(v.location,'')) LIKE lower(?)")
            values.append(f"%{location}%")
        if seniority:
            patterns = {
                "intern": ("%intern%", "%trainee%"),
                "entry": ("%junior%", "%entry%", "%graduate%", "%fresher%"),
                "mid": ("%mid%", "%middle%"),
                "senior": ("%senior%", "%sr.%"),
                "lead_plus": ("%lead%", "%principal%", "%staff%", "%manager%", "%director%", "%head%"),
            }.get(seniority, ())
            if patterns:
                seniority_text = "lower(COALESCE(NULLIF(json_extract(v.score_detail,'$.facts.seniority'),''),v.title,''))"
                conditions.append("(" + " OR ".join(f"{seniority_text} LIKE ?" for _ in patterns) + ")")
                values.extend(patterns)
        if source:
            conditions.append(
                "EXISTS(SELECT 1 FROM vacancy_observations vf JOIN observations o ON o.id=vf.observation_id "
                "JOIN sources s ON s.id=o.source_id WHERE vf.vacancy_id=v.id AND s.kind=?)"
            )
            values.append(source)
        order = {
            "best": JOBS_ORDER,
            "posted": "COALESCE(v.published_at,v.first_seen_at) DESC,v.id",
            "found": "v.first_seen_at DESC,v.id",
            "company": "lower(v.company),lower(v.title),v.id",
        }.get(sort, JOBS_ORDER)
        where = " AND ".join(conditions)
        if focus_id:
            where = f"(({where}) OR v.id=?)"
            values.append(focus_id)
        total = db.one(f"SELECT COUNT(*) AS count FROM vacancies v WHERE {where}", tuple(values))["count"]
        rows = db.all(
            "SELECT v.id,v.company,v.title,v.location,v.work_mode,v.published_at,v.first_seen_at,"
            "v.state,v.decision_state,v.seen_at,v.snoozed_until,v.recruiting_outcome,"
            "v.manual_applied_at,v.manual_applied_source,v.score,v.score_detail,v.analysis_status "
            f"FROM vacancies v WHERE {where} ORDER BY "
            f"{'CASE WHEN v.id=? THEN 0 ELSE 1 END,' if focus_id else ''}{order} LIMIT ? OFFSET ?",
            (*values, *((focus_id,) if focus_id else ()), page_size, (page - 1) * page_size),
        )
        enrich_jobs(db, rows)
        for row in rows:
            detail = json.loads(row.get("score_detail") or "{}")
            facts = detail.get("facts") or {}
            row["work_mode"] = row.get("work_mode") or facts.get("work_mode") or ""
            row["seniority"] = facts.get("seniority") or ""
            row["salary_range"] = facts.get("salary_range") or ""
            criteria = detail.get("criteria") or {}
            row["match_signals"] = [
                {"label": key.replace("_", " ").title(), "score": item.get("score"), "reason": item.get("reason", "")}
                for key, item in criteria.items() if isinstance(item, dict) and isinstance(item.get("score"), (int, float))
            ]
            row.update(fit_summary(row.get("score"), detail, db.get_setting("search_intent", {})))
            row["seniority_key"] = seniority_key(f"{facts.get('seniority') or ''} {row.get('title') or ''}")
            row.pop("score_detail", None)
        attach_job_sources(rows)

        inbox_base = (
            "v.decision_state='undecided' AND v.snoozed_until IS NULL "
            "AND COALESCE(json_array_length(json_extract(v.score_detail,'$.hard_exclusions')),0)=0 "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm')"
        )
        unseen_count = db.one(
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {inbox_base} AND v.seen_at IS NULL"
        )["count"]
        since_count = unseen_count if not since else db.one(
            f"SELECT COUNT(*) AS count FROM vacancies v WHERE {inbox_base} AND datetime(v.first_seen_at)>datetime(?)",
            (since,),
        )["count"]
        outside_count = db.one(
            "SELECT COUNT(*) AS count FROM vacancies v "
            "WHERE COALESCE(json_array_length(json_extract(v.score_detail,'$.hard_exclusions')),0)>0 "
            "AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm')"
        )["count"]
        return {
            "items": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
            "inbox": {"unseen": unseen_count, "since_last_visit": since_count, "outside": outside_count, "since": since or None},
        }

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        release_due_snoozes(db)
        row = db.one("SELECT * FROM vacancies WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "Job not found")
        enrich_jobs(db, [row])
        row["observations"] = db.all(
            "SELECT o.id,o.url,o.first_seen_at,o.last_seen_at,o.published_at,s.id AS source_id,s.kind,s.name,vo.merge_reason "
            "FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id "
            "JOIN sources s ON s.id=o.source_id WHERE vo.vacancy_id=? ORDER BY o.first_seen_at,o.id",
            (job_id,),
        )
        try:
            detail = json.loads(row.get("score_detail") or "{}")
        except (TypeError, ValueError):
            detail = {}
        row.update(fit_summary(row.get("score"), detail, db.get_setting("search_intent", {})))
        feedback = db.one(
            "SELECT reason FROM feedback WHERE vacancy_id=? AND state=? AND reason IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (job_id, row["decision_state"]),
        )
        row["decision_reason"] = feedback["reason"] if feedback else None
        for observation in row["observations"]:
            observation["merge_reason_label"] = merge_reason_label(observation["merge_reason"])
        row["sighting_count"] = len(row["observations"])
        return row

    @app.post("/api/jobs/{job_id}/seen")
    def see_job(job_id: str):
        try:
            return mark_seen(db, job_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/api/jobs/{job_id}/decision")
    def decide_job(job_id: str, payload: DecisionInput):
        previous = db.one("SELECT decision_state FROM vacancies WHERE id=?", (job_id,))
        try:
            result = set_decision(
                db, job_id, payload.decision, reason=payload.reason, snoozed_until=payload.snoozed_until
            )
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if payload.decision == "shortlisted" and previous and previous["decision_state"] != "shortlisted":
            provider = db.get_setting("profile", {}).get("drafting_provider", "")
            try:
                result["application_preparation"] = auto_apply_manager.queue_manual(job_id, provider, True)
            except ValueError as error:
                result["application_preparation"] = {"status": "needs_review", "detail": str(error)}
        auto_apply_manager.wake()
        return result

    @app.post("/api/jobs/{job_id}/outcome")
    def set_job_outcome(job_id: str, payload: RecruitingOutcomeInput):
        try:
            result = set_recruiting_outcome(db, job_id, payload.outcome)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        auto_apply_manager.wake()
        return result

    @app.post("/api/jobs/{job_id}/manual-applied")
    def manual_applied(job_id: str, payload: ManualAppliedInput):
        try:
            result = set_manual_applied(db, job_id, payload.applied)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        auto_apply_manager.wake()
        return result

    @app.post("/api/jobs/{job_id}/observations/{observation_id}/split", status_code=201)
    def split_job_sighting(job_id: str, observation_id: str):
        try:
            new_job_id = split_observation(db, job_id, observation_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        match_manager.wake()
        return {"id": new_job_id, "split_from": job_id, "observation_id": observation_id}

    @app.post("/api/jobs/import", status_code=201)
    def import_job(payload: JobInput):
        identifier = new_id()
        timestamp = now()
        employer = db.one("SELECT id FROM employers WHERE lower(name)=lower(?)", (payload.company,))
        score, detail = score_job({"company": payload.company, "title": payload.title, "description": payload.description,
                                   "location": payload.location, "first_seen_at": timestamp}, db.get_setting("profile", {}),
                                  db.get_setting("search_intent", {}))
        matching_model = db.get_setting("matching_model", "")
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO vacancies(id,employer_id,company,title,location,description,apply_url,first_seen_at,last_seen_at,score,score_detail,analysis_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, employer["id"] if employer else None, payload.company, payload.title,
                 payload.location, payload.description, str(payload.apply_url) if payload.apply_url else None,
                 timestamp, timestamp, score, json.dumps(detail, ensure_ascii=False),
                 "pending" if matching_model else "not_configured", timestamp, timestamp),
            )
            conn.execute("INSERT INTO vacancy_fts(vacancy_id,title,company,description) VALUES(?,?,?,?)",
                         (identifier, payload.title, payload.company, payload.description))
        match_manager.wake()
        return {"id": identifier}

    @app.post("/api/jobs/{job_id}/analyze", status_code=202)
    def analyze_again(job_id: str):
        try:
            match_manager.retry(job_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"queued": True}

    @app.get("/api/auto-apply")
    def auto_apply_status(summary_only: bool = False):
        return auto_apply_manager.status(include_preview=not summary_only)

    def failed_project_briefs() -> list[dict]:
        cards = db.all("SELECT id,details FROM evidence WHERE approved=0 AND repository_id IS NOT NULL")
        failed = []
        for card in cards:
            try:
                details = json.loads(card["details"] or "{}")
            except (TypeError, ValueError):
                continue
            if details.get("generation_status") == "failed":
                failed.append({"id": card["id"], "provider": details.get("generation_provider")})
        return failed

    def failed_ai_work() -> dict:
        return {
            "preparations": len(auto_apply_manager.failed_preparations()),
            "project_briefs": len(failed_project_briefs()),
            "draft_projects": db.one("SELECT COUNT(*) AS count FROM application_drafts WHERE project_refresh_error IS NOT NULL")["count"],
            "regenerations": db.one("SELECT COUNT(*) AS count FROM auto_application_attempts WHERE retry_payload IS NOT NULL AND status='needs_review'")["count"],
            "running": bool(ai_retry_task and not ai_retry_task.done()),
        }

    @app.get("/api/ai/failures")
    def ai_failures():
        return failed_ai_work()

    async def retry_ai_work() -> None:
        for card in failed_project_briefs():
            try:
                provider = card["provider"] or db.get_setting("profile", {}).get("drafting_provider", "codex")
                if provider == "chatgpt_web":
                    provider = "template"
                await asyncio.to_thread(generate_project_content, db, card["id"], provider)
            except Exception:
                log.exception("Project brief retry failed for %s", card["id"])
        for draft in db.all("SELECT id FROM application_drafts WHERE project_refresh_error IS NOT NULL ORDER BY updated_at,id"):
            try:
                await asyncio.to_thread(refresh_draft_projects, db, settings, draft["id"])
            except Exception:
                log.exception("Resume project retry failed for %s", draft["id"])
        for attempt in db.all("SELECT draft_id,retry_payload FROM auto_application_attempts "
                              "WHERE retry_payload IS NOT NULL AND status='needs_review' ORDER BY updated_at"):
            try:
                payload = json.loads(attempt["retry_payload"])
                await auto_apply_manager.regenerate(attempt["draft_id"], payload["prompt"], payload["section"],
                                                    deliver_telegram=False)
            except Exception:
                log.exception("Draft revision retry failed for %s", attempt["draft_id"])
        auto_apply_manager.retry_failed_preparations()

    @app.post("/api/ai/retry-failed", status_code=202)
    async def retry_failed_ai():
        nonlocal ai_retry_task
        state = failed_ai_work()
        if state["running"]:
            raise HTTPException(409, "AI retry is already running")
        total = sum(state[key] for key in ("preparations", "project_briefs", "draft_projects", "regenerations"))
        if total:
            ai_retry_task = asyncio.create_task(retry_ai_work())
        return {"queued": total, **state}

    @app.put("/api/auto-apply")
    def configure_auto_apply(payload: AutoApplyInput):
        if payload.enabled:
            profile = db.get_setting("profile", {})
            if not db.get_setting("matching_model", ""):
                raise HTTPException(409, "Choose a local matching model in My profile first")
            if not profile.get("drafting_provider") or not provider_available(profile["drafting_provider"]):
                raise HTTPException(409, "Choose an available application drafting provider in My profile first")
            if not profile.get("name") or not profile.get("email"):
                raise HTTPException(409, "Add your name and email in My profile first")
            if not profile.get("experience") and not db.one("SELECT id FROM evidence WHERE approved=1 AND kind='project' LIMIT 1"):
                raise HTTPException(409, "Add work history or approve a GitHub project first")
        policy = {
            key: value
            for key, value in payload.model_dump().items()
            if key not in {"enabled", "threshold"} and value is not None
        }
        return auto_apply_manager.configure(
            payload.enabled,
            payload.threshold,
            policy,
        )

    @app.post("/api/auto-apply/queue-existing")
    def queue_existing_auto_apply():
        try:
            return auto_apply_manager.queue_existing()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/jobs/{job_id}/state")
    def update_state(job_id: str, payload: StateInput):
        # Backward-compatible adapter for older clients. New UI uses independent
        # decision/outcome/application endpoints.
        try:
            if payload.state == "new":
                result = set_decision(db, job_id, "undecided", reason=payload.reason)
            elif payload.state == "interesting":
                result = set_decision(db, job_id, "shortlisted", reason=payload.reason)
            elif payload.state == "ignored":
                result = set_decision(db, job_id, "ignored", reason=payload.reason)
            elif payload.state in {"interview", "rejected", "offer"}:
                result = set_recruiting_outcome(db, job_id, payload.state)
            elif payload.state in {"prepare", "ready", "applied"}:
                raise HTTPException(
                    409,
                    "Application progress is derived from drafts and submissions. "
                    "Use the explicit external-applied action when you applied outside Job Radar.",
                )
            else:
                raise HTTPException(422, "Unsupported legacy job state")
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"state": payload.state}

    @app.get("/api/evidence")
    def evidence_list():
        return db.all("SELECT evidence.*,repository_snapshots.url AS repository_url,repository_snapshots.commit_sha FROM evidence LEFT JOIN repository_snapshots ON repository_snapshots.id=evidence.repository_id ORDER BY evidence.created_at DESC")

    @app.post("/api/evidence", status_code=201)
    def add_evidence(payload: EvidenceInput):
        identifier = new_id()
        db.execute(
            "INSERT INTO evidence(id,kind,title,claim,support,approved,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (identifier, payload.kind, payload.title, payload.claim, json.dumps(payload.support), int(payload.approved), now(), now()),
        )
        return {"id": identifier}

    @app.patch("/api/evidence/{evidence_id}")
    def edit_evidence(evidence_id: str, updates: dict[str, Any] = Body(...)):
        row = db.one("SELECT * FROM evidence WHERE id=?", (evidence_id,))
        if not row:
            raise HTTPException(404, "Evidence not found")
        if not updates or set(updates) - {"kind", "title", "claim", "support", "approved", "details"}:
            raise HTTPException(422, "Unsupported evidence fields")
        validated = EvidenceInput(**{**row, "support": json.loads(row["support"]), **updates})
        details = updates.get("details", json.loads(row["details"]))
        if not isinstance(details, dict) or not isinstance(details.get("bullets", [validated.claim]), list) or not isinstance(details.get("tech_stack", []), list):
            raise HTTPException(422, "Project details must contain bullet and technology lists")
        structured = row["repository_id"] and details.get("schema_version") == 2
        if structured:
            results = details.get("results")
            if not isinstance(results, list) or len(results) > 8 or any(not isinstance(item, dict) for item in results):
                raise HTTPException(422, "Add up to eight project results")
            normalized = [{key: str(item.get(key) or "").strip() for key in ("id", "area", "outcome", "source")}
                          for item in results]
            identifiers = [item["id"] for item in normalized]
            if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
                raise HTTPException(422, "Each project result needs a unique identifier")
            details = {**details, "results": normalized,
                       "summary": str(details.get("what") or "").strip(),
                       "bullets": [item["outcome"] for item in normalized if item["outcome"]]}
            if details["bullets"]:
                validated = validated.model_copy(update={"claim": details["bullets"][0]})
        if validated.approved and row["repository_id"]:
            if structured:
                fields = [str(details.get(key) or "").strip() for key in ("what", "why", "how")]
                complete_results = all(len(item["area"]) >= 2 and len(item["outcome"]) >= 20 and len(item["source"]) >= 2
                                       for item in details["results"])
                if any(len(value) < 10 for value in fields) or not details["results"] or not complete_results:
                    raise HTTPException(422, "Complete What, Why, How, and sourced results before including this project in resumes")
            else:
                bullets = [str(item).strip() for item in details.get("bullets", []) if str(item).strip()]
                if (not bullets or len(bullets[0]) < 20 or
                        re.search(r"<[^>]+>|^(?:project:|repository summary:|describe your contribution|repository available)", bullets[0], re.I) or
                        (not details.get("generated_by") and validated.claim == row["claim"])):
                    raise HTTPException(422, "Replace the repository placeholder with a specific reviewed project bullet before approval")
            details = {**details, "generation_status": "reviewed"}
            details.pop("generation_error", None)
        db.execute("UPDATE evidence SET kind=?,title=?,claim=?,details=?,support=?,approved=?,updated_at=? WHERE id=?",
                   (validated.kind, validated.title, validated.claim, json.dumps(details, ensure_ascii=False), json.dumps(validated.support), int(validated.approved), now(), evidence_id))
        return {"id": evidence_id}

    @app.delete("/api/evidence/{evidence_id}")
    def delete_evidence(evidence_id: str):
        row = db.one("SELECT kind,approved FROM evidence WHERE id=?", (evidence_id,))
        if not row:
            raise HTTPException(404, "Evidence not found")
        db.execute("DELETE FROM evidence WHERE id=?", (evidence_id,))
        return {"deleted": True}

    @app.post("/api/repositories/inspect")
    def inspect_repo(payload: RepositoryInput):
        try:
            provider = payload.provider or configured_project_provider()
            if provider not in PROVIDERS:
                raise ValueError("Unsupported provider")
            result = inspect_repository(db, settings, str(payload.url))
            result["processing"] = provider_processing(provider)
            try:
                result["project_content"] = generate_project_content(db, result["evidence_id"], provider)
            except (ValueError, RuntimeError) as error:
                result["generation_warning"] = str(error)
            return result
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/evidence/{evidence_id}/generate")
    def generate_project(evidence_id: str, payload: ProjectGenerationInput):
        try:
            provider = payload.provider or configured_project_provider()
            result = generate_project_content(db, evidence_id, provider)
            result["processing"] = provider_processing(provider)
            return result
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/repositories")
    def repositories():
        return db.all("SELECT id,url,commit_sha,owner_context,summary,inspected_at FROM repository_snapshots ORDER BY inspected_at DESC")

    @app.get("/api/github/{username}/repositories")
    def github_repositories(username: str):
        try:
            return list_public_repositories(username)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except httpx.HTTPError as error:
            raise HTTPException(502, f"GitHub could not be reached: {error}") from error

    @app.get("/api/providers")
    def providers():
        return PROVIDERS

    @app.get("/api/jobs/{job_id}/prepare/preflight")
    def prepare_preflight(job_id: str, provider: str = ""):
        try:
            result = preparation_preflight(db, job_id)
            chosen_provider = provider or configured_provider()
            if provider and not provider_available(provider):
                raise HTTPException(422, f"{provider} is not available on this Mac")
            result["processing"] = provider_processing(chosen_provider) if chosen_provider else None
            return result
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/api/jobs/{job_id}/prepare", status_code=202)
    def prepare(job_id: str, payload: PrepareInput):
        provider = payload.provider or configured_provider()
        if payload.provider and not provider_available(payload.provider):
            raise HTTPException(422, f"{payload.provider} is not available on this Mac")
        try:
            result = auto_apply_manager.queue_manual(job_id, provider, payload.prepare_anyway)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        processing = provider_processing(provider)
        if result["status"] == "confirmation_required":
            raise HTTPException(409, detail={
                "code": "prepare_confirmation_required",
                "message": result["preflight"]["reason"],
                "preflight": result["preflight"],
                "processing": processing,
            })
        return {**result, "processing": processing}

    def application_page_data(page: int, page_size: int, q: str = "", review: str = "",
                              company: str = "", delivery: str = "", full: bool = False) -> dict:
        clauses = []
        params: list[Any] = []
        query = q.strip().casefold()
        if query:
            clauses.append("LOWER(v.title || ' ' || v.company || ' ' || d.provider || ' ' || d.provider_mode) LIKE ?")
            params.append(f"%{query}%")
        if company:
            clauses.append("v.company=?")
            params.append(company)
        review_expr = (
            "CASE "
            "WHEN EXISTS(SELECT 1 FROM submissions su WHERE su.draft_id=d.id AND su.status IN ('submitted_unconfirmed','sending')) THEN 'submission_uncertain' "
            "WHEN EXISTS(SELECT 1 FROM submissions ss WHERE ss.draft_id=d.id AND ss.status IN ('sent_confirmed','submitted_confirmed')) THEN 'sent' "
            "ELSE COALESCE(a.status,CASE WHEN d.status='sent' THEN 'sent' "
            "WHEN d.status='submission_uncertain' THEN 'submission_uncertain' ELSE 'draft' END) END"
        )
        sort_priority_expr = (
            "CASE "
            "WHEN d.project_refresh_error IS NOT NULL "
            "     OR d.status = 'failed' "
            "     OR a.status = 'failed' "
            "     OR (a.status = 'needs_review' AND (a.detail LIKE '%failed%' OR a.detail LIKE '%stopped%')) THEN 1 "
            "WHEN " + review_expr + " IN ('needs_review', 'needs_confirmation', 'regenerating') THEN 2 "
            "WHEN " + review_expr + " IN ('awaiting_review', 'draft', 'preparing', 'queued') THEN 3 "
            "WHEN " + review_expr + " IN ('sent', 'submission_uncertain', 'sending') THEN 4 "
            "ELSE 3 END"
        )
        if review:
            if review == "preparation_failed":
                clauses.append(f"({sort_priority_expr}=1)")
            else:
                clauses.append(f"{review_expr}=?")
                params.append(review)
        if delivery == "sent":
            clauses.append("EXISTS(SELECT 1 FROM submissions s WHERE s.draft_id=d.id AND s.status IN ('sent_confirmed','submitted_confirmed'))")
        elif delivery == "uncertain":
            clauses.append("EXISTS(SELECT 1 FROM submissions s WHERE s.draft_id=d.id AND s.status IN ('submitted_unconfirmed','sending'))")
        elif delivery == "unsent":
            clauses.append("NOT EXISTS(SELECT 1 FROM submissions s WHERE s.draft_id=d.id AND s.status IN ('sent_confirmed','submitted_confirmed','submitted_unconfirmed','sending'))")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        base = (" FROM application_drafts d JOIN vacancies v ON v.id=d.vacancy_id "
                "LEFT JOIN auto_application_attempts a ON a.vacancy_id=d.vacancy_id")
        total = db.one("SELECT COUNT(*) AS count" + base + where, tuple(params))["count"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = db.all(
            f"SELECT d.id,d.vacancy_id,d.status,d.provider,d.provider_mode,d.created_at,d.updated_at,"
            f"v.title AS job_title,v.company,({review_expr}) AS review_status,a.telegram_status,"
            f"a.detail AS attempt_detail,d.project_refresh_error" + base + where +
            f" ORDER BY {sort_priority_expr} ASC, COALESCE(d.updated_at, d.created_at) DESC, d.created_at DESC, d.id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        )
        items = []
        for row in rows:
            latest = db.one("SELECT * FROM submissions WHERE draft_id=? ORDER BY sent_at DESC,id DESC LIMIT 1",
                            (row["id"],))
            items.append({
                **(get_draft(db, row["id"]) if full else row),
                "latest_submission": submission_record(latest) if latest else None,
            })
        companies = [row["company"] for row in db.all(
            "SELECT DISTINCT v.company FROM application_drafts d JOIN vacancies v ON v.id=d.vacancy_id "
            "ORDER BY LOWER(v.company),v.company")]
        return {"items": items, "page": page, "page_size": page_size, "total": total,
                "pages": pages, "companies": companies}

    @app.get("/api/applications/page")
    def applications_page(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        q: str = "", review: str = "", company: str = "", delivery: str = "",
    ):
        return application_page_data(page, page_size, q, review, company, delivery)

    @app.get("/api/application-preparations")
    def application_preparations():
        threshold = normalize_search_intent(db.get_setting("search_intent", {}))["strong_match_threshold"]
        rows = db.all(
            "SELECT a.vacancy_id,a.status,a.detail,a.requested_provider,a.updated_at,"
            "v.title AS job_title,v.company,v.score FROM auto_application_attempts a "
            "JOIN vacancies v ON v.id=a.vacancy_id "
            "WHERE a.draft_id IS NULL AND a.status IN ('queued','preparing','needs_review','needs_confirmation') "
            "AND v.analysis_status='done' AND v.score>=? "
            "AND NOT EXISTS(SELECT 1 FROM application_drafts d WHERE d.vacancy_id=a.vacancy_id) "
            "AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.vacancy_id=a.vacancy_id) "
            "ORDER BY a.updated_at DESC LIMIT 100",
            (threshold,),
        )
        provider = db.get_setting("profile", {}).get("drafting_provider", "")
        issues = []
        for row in rows:
            detail = (row["detail"] or "").casefold()
            if row["status"] == "queued":
                if "retrying" in detail:
                    category, label, reason = "queued", "Retrying", row["detail"]
                else:
                    category, label, reason = "queued", "Queued", "Application preparation is queued in the background."
            elif row["status"] == "preparing":
                category, label, reason = "preparing", "Preparing", (row["detail"] or "The application draft is being created.")
            elif row["status"] == "needs_confirmation":
                category, label, reason = "method", "Application method needed", "Confirm how to apply before creating a draft."
            elif re.search(r"out of credits|insufficient credits|credit balance", detail):
                category, label, reason = "credits", "Out of credits", "The AI provider ran out of credits before creating a draft. Retry when credits are available."
            elif re.search(r"quota|rate limit|usage limit", detail):
                category, label, reason = "quota", "AI limit reached", "The AI provider hit a usage limit before creating a draft. Retry when the limit resets."
            else:
                category, label, reason = "failed", "Draft failed", (row["detail"] or "Application preparation stopped before a draft was created. Retry to try again.")
            issues.append({
                "vacancy_id": row["vacancy_id"], "job_title": row["job_title"],
                "company": row["company"], "score": row["score"], "status": row["status"],
                "category": category, "label": label, "reason": reason,
                "provider": row["requested_provider"] or provider,
                "updated_at": row["updated_at"],
                "retryable": row["status"] in ("needs_review", "needs_confirmation"),
            })
        return issues

    @app.get("/api/applications")
    def applications():
        # Backward-compatible first page for integrations that still expect a list.
        return application_page_data(1, 100, full=True)["items"]

    @app.get("/api/applications/{draft_id}")
    def application(draft_id: str):
        try:
            draft = get_draft(db, draft_id)
            reasons = send_readiness(db, settings, draft)
            review = db.one("SELECT status,review_hash,telegram_status,telegram_error,requested_by,detail,prepare_anyway,updated_at,created_at FROM auto_application_attempts WHERE vacancy_id=?", (draft["vacancy_id"],))
            latest = db.one("SELECT * FROM submissions WHERE draft_id=? ORDER BY sent_at DESC,id DESC LIMIT 1", (draft_id,))
            job_data = None
            try:
                job_data = job(draft["vacancy_id"])
            except Exception as error:
                log.warning("Could not attach job data for draft %s: %s", draft_id, error)

            activities = []
            if latest:
                activities.append({
                    "kind": "submission",
                    "label": "Application sent",
                    "detail": f"{latest['status']} · {latest.get('receipt') or 'Submitted'}",
                    "created_at": latest["sent_at"],
                    "tone": "success" if latest["status"] in CONFIRMED_SUBMISSION_STATUSES else "warning",
                })
            if review and review.get("detail"):
                activities.append({
                    "kind": "status",
                    "label": f"Status: {review['status'].replace('_', ' ').capitalize()}",
                    "detail": review["detail"],
                    "created_at": review.get("updated_at") or draft["updated_at"],
                    "tone": "info",
                })
            notif_rows = db.all(
                "SELECT channel, status, error, created_at FROM notification_events WHERE vacancy_id=? ORDER BY created_at DESC LIMIT 5",
                (draft["vacancy_id"],),
            )
            for ev in notif_rows:
                activities.append({
                    "kind": "notification",
                    "label": f"Notification ({ev['channel'].replace('_', ' ')})",
                    "detail": f"Status: {ev['status']}" + (f" · {ev['error']}" if ev.get("error") else ""),
                    "created_at": ev["created_at"],
                    "tone": "success" if ev["status"] == "sent" else "warning",
                })
            activities.append({
                "kind": "creation",
                "label": "Draft created",
                "detail": f"Prepared with {draft.get('provider') or 'model'}",
                "created_at": draft["created_at"],
                "tone": "info",
            })
            activities.sort(key=lambda a: a.get("created_at") or "", reverse=True)

            return {**draft, "resume_bullet_source": editable_bullet_lines(draft["resume_data"]),
                    "send_ready": not reasons, "send_blockers": reasons,
                    "linkedin_automation_paused": bool(db.get_setting("linkedin_automation_paused", False)) if draft["job_source_kind"] == "linkedin" else False,
                    "review_status": review["status"] if review else None,
                    "review_hash": review["review_hash"] if review else None,
                    "telegram_status": review["telegram_status"] if review else None,
                    "telegram_error": review["telegram_error"] if review else None,
                    "preparation_requested_by": review["requested_by"] if review else None,
                    "preparation_detail": review["detail"] if review else None,
                    "preparation_approved_without_destination": bool(review["prepare_anyway"]) if review else False,
                    "latest_submission": submission_record(latest, include_package=True) if latest else None,
                    "job": job_data,
                    "activities": activities}
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    def require_editable_application(draft_id: str) -> None:
        review = db.one("SELECT status FROM auto_application_attempts WHERE draft_id=?", (draft_id,))
        if review and review["status"] in ("sending", "regenerating"):
            raise HTTPException(409, "This application is being processed. Try again when it finishes.")
        if review and review["status"] == "submission_uncertain":
            raise HTTPException(409, "Submission status uncertain. Verify on the employer site before changing or sending this application again.")
        prior = db.one(
            "SELECT status FROM submissions WHERE draft_id=? AND status IN "
            "('sent_confirmed','submitted_confirmed','submitted_unconfirmed','sending') LIMIT 1", (draft_id,))
        if prior:
            raise HTTPException(409, "This application already has a send attempt. Review its submission outcome before changing the reviewed package.")

    @app.patch("/api/applications/{draft_id}")
    async def edit_application(draft_id: str, updates: dict[str, Any] = Body(...)):
        require_editable_application(draft_id)
        try:
            await asyncio.to_thread(update_draft, db, settings, draft_id, updates)
            await auto_apply_manager.notify_review(draft_id, deliver_telegram=False)
            return application(draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.delete("/api/applications/{draft_id}")
    def delete_application(draft_id: str, ignore_job: bool = Query(default=True)):
        try:
            draft = get_draft(db, draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        if draft["status"] == "sent" or db.one(
            "SELECT 1 FROM submissions WHERE draft_id=? AND status IN "
            "('sent_confirmed','submitted_confirmed','submitted_unconfirmed','sending') LIMIT 1",
            (draft_id,),
        ):
            raise HTTPException(422, "Sent applications cannot be deleted")
        vacancy_id = draft["vacancy_id"]
        resume_path = draft.get("resume_path")
        if resume_path:
            try:
                Path(resume_path).unlink(missing_ok=True)
            except OSError:
                pass
        db.execute(
            "UPDATE auto_application_attempts SET status='skipped', draft_id=NULL, "
            "detail='Application draft removed by you', updated_at=? WHERE draft_id=? OR vacancy_id=?",
            (now(), draft_id, vacancy_id),
        )
        db.execute("DELETE FROM telegram_review_prompts WHERE draft_id=?", (draft_id,))
        db.execute("DELETE FROM application_drafts WHERE id=?", (draft_id,))
        if ignore_job:
            set_decision(db, vacancy_id, "ignored", reason="Draft discarded by you")
        auto_apply_manager.wake()
        return {"deleted": True, "draft_id": draft_id, "vacancy_id": vacancy_id, "job_ignored": ignore_job}

    def draft_resume_path(draft_id: str) -> Path:
        row = db.one("SELECT resume_path FROM application_drafts WHERE id=?", (draft_id,))
        if not row:
            raise HTTPException(404, "Draft not found")
        path = Path(row["resume_path"])
        if not path.is_file():
            raise HTTPException(404, "Resume file not found")
        return path

    @app.get("/api/applications/{draft_id}/resume")
    def resume_file(draft_id: str):
        path = draft_resume_path(draft_id)
        return FileResponse(path, media_type="application/pdf", filename=f"resume-{draft_id[:8]}.pdf",
                            content_disposition_type="inline")

    @app.get("/api/applications/{draft_id}/resume/preview/pages")
    def resume_preview_pages(draft_id: str):
        pdf = pdfium.PdfDocument(str(draft_resume_path(draft_id)))
        try:
            return {"pages": len(pdf)}
        finally:
            pdf.close()

    @app.get("/api/applications/{draft_id}/resume/preview")
    def resume_preview(draft_id: str, page: int = Query(default=1, ge=1)):
        pdf = pdfium.PdfDocument(str(draft_resume_path(draft_id)))
        try:
            if page > len(pdf):
                raise HTTPException(404, "Resume page not found")
            pdf_page = pdf[page - 1]
            try:
                bitmap = pdf_page.render(scale=1.7)
                try:
                    output = BytesIO()
                    bitmap.to_pil().save(output, format="PNG")
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
            return Response(output.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=60"})
        finally:
            pdf.close()

    @app.post("/api/applications/{draft_id}/attachments")
    async def upload_application_attachment(draft_id: str, file: UploadFile = File(...)):
        require_editable_application(draft_id)
        try:
            draft = get_draft(db, draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        if draft["status"] == "sent":
            raise HTTPException(422, "Sent applications cannot be changed")
        data = await file.read(10_000_001)
        if len(data) > 10_000_000 or not data.startswith(b"%PDF-"):
            raise HTTPException(422, "Choose a PDF smaller than 10 MB")
        directory = settings.artifact_dir / draft_id / "attachments"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{new_id()}.pdf"
        path.write_bytes(data)
        return {"kind": "uploaded", "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                "name": Path(file.filename or "attachment.pdf").name}

    @app.post("/api/applications/{draft_id}/inspect")
    async def inspect_application(draft_id: str):
        require_editable_application(draft_id)
        try:
            async with scan_manager.browser_lock:
                draft = await inspect_form(db, settings, draft_id)
            if draft["destination"].get("kind") == "linkedin_easy_apply":
                db.set_setting("linkedin_automation_paused", False)
            await auto_apply_manager.notify_review(draft_id, deliver_telegram=False)
            return draft
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/applications/{draft_id}/discover-apply")
    async def discover_application_apply(draft_id: str):
        require_editable_application(draft_id)
        try:
            draft = get_draft(db, draft_id)
            if draft["job_source_kind"] != "linkedin" or not draft["job_posting_url"]:
                raise ValueError("This application has no LinkedIn posting to check")
            if draft["destination"].get("kind") in {"web", "email"}:
                raise ValueError("This application already has a destination; review it before replacing it")
            if db.get_setting("social_reauth_required_linkedin"):
                return {
                    "action": {"kind": "sign_in_required", "detail": "LinkedIn needs a new sign-in. Use the Sign in again button, then check Apply again."},
                    "draft": draft,
                    "inspection_error": None,
                }
            async with scan_manager.browser_lock:
                action = await discover_linkedin_apply(settings, draft["job_posting_url"])
                inspection_error = None
                if action["kind"] == "web":
                    draft = set_discovered_web_destination(db, draft_id, action["url"])
                    try:
                        draft = await inspect_form(db, settings, draft_id)
                    except (ValueError, RuntimeError, PlaywrightError) as error:
                        inspection_error = str(error)
                elif action["kind"] == "linkedin_easy_apply":
                    draft = set_discovered_linkedin_destination(db, draft_id)
                    try:
                        draft = await inspect_form(db, settings, draft_id)
                    except (ValueError, RuntimeError, PlaywrightError) as error:
                        inspection_error = str(error)
                elif action["kind"] in {"closed", "already_applied"}:
                    draft = set_unavailable_linkedin_destination(db, draft_id, action["detail"])
                    if action["kind"] == "closed":
                        set_decision(db, draft["vacancy_id"], "ignored", reason="Posting closed on LinkedIn")
                        db.execute(
                            "UPDATE auto_application_attempts SET status='skipped', detail=?, updated_at=? WHERE vacancy_id=?",
                            (f"Job posting closed on LinkedIn: {action['detail']}", now(), draft["vacancy_id"]),
                        )
            if action["kind"] in {"web", "linkedin_easy_apply", "closed", "already_applied"}:
                db.set_setting("linkedin_automation_paused", False)
                await auto_apply_manager.notify_review(draft_id, deliver_telegram=False)
            return {"action": action, "draft": draft, "inspection_error": inspection_error}
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError, PlaywrightError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/applications/{draft_id}/send")
    async def send(draft_id: str, payload: SendInput):
        try:
            async with scan_manager.browser_lock:
                result = await send_application(db, settings, draft_id, payload.package_hash)
            attempt = db.one("SELECT vacancy_id FROM auto_application_attempts WHERE draft_id=?", (draft_id,))
            if attempt:
                outcome = result.get("outcome", {})
                next_status = ("sent" if result["status"] in ("sent_confirmed", "submitted_confirmed")
                               else "submission_uncertain" if outcome.get("key") == "submission_uncertain"
                               else "awaiting_review" if outcome.get("key") == "send_failed"
                               else "needs_review")
                auto_apply_manager._set_status(
                    attempt["vacancy_id"], next_status,
                    result.get("receipt") or result.get("error") or outcome.get("guidance") or outcome.get("label") or result["status"],
                    draft_id,
                )
            return result
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/applications/{draft_id}/approve")
    async def approve_application(draft_id: str, payload: SendInput):
        try:
            return await auto_apply_manager.approve(draft_id, payload.package_hash)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/applications/{draft_id}/regenerate")
    async def regenerate_application(draft_id: str, payload: RegenerateInput):
        try:
            return await auto_apply_manager.regenerate(draft_id, payload.prompt, payload.section,
                                                       deliver_telegram=False)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/applications/{draft_id}/chatgpt-input")
    async def enter_chatgpt_prompt(draft_id: str, payload: ChatGPTInput):
        require_editable_application(draft_id)
        try:
            prompt = application_prompt(db, draft_id, payload.section, payload.instruction)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        result = await chatgpt_input.enter(prompt)
        if result["status"] == "failed":
            raise HTTPException(503, result["detail"])
        if result.get("reply"):
            try:
                apply_chatgpt_reply(db, settings, draft_id, payload.section, result["reply"], payload.instruction)
                await auto_apply_manager.notify_review(draft_id, deliver_telegram=False)
                result["detail"] = "Answer received from ChatGPT and applied to this application."
            except Exception as error:
                log.warning("Could not apply ChatGPT reply to %s: %s", draft_id, error)
                result["detail"] = f"Answer received from ChatGPT. Review or copy into the application: {result['reply'][:120]}"
        return result

    @app.get("/api/chatgpt/status")
    def get_chatgpt_status():
        return chatgpt_input.status()

    @app.post("/api/chatgpt/login")
    async def open_chatgpt_login():
        result = await chatgpt_input.open_login()
        if result["status"] == "failed":
            raise HTTPException(503, result["detail"])
        return result

    @app.get("/api/submissions/page")
    def submissions_page(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        q: str = "",
    ):
        query = q.strip().casefold()
        params: list[Any] = []
        where = ""
        if query:
            where = " WHERE LOWER(v.title || ' ' || v.company) LIKE ?"
            params.append(f"%{query}%")
        base = " FROM submissions s JOIN vacancies v ON v.id=s.vacancy_id"
        total = db.one("SELECT COUNT(*) AS count" + base + where, tuple(params))["count"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = db.all(
            "SELECT s.*,v.title AS job_title,v.company" + base + where +
            " ORDER BY s.sent_at DESC,s.id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        )
        return {"items": [submission_record(row) for row in rows], "page": page,
                "page_size": page_size, "total": total, "pages": pages}

    @app.get("/api/submissions/{submission_id}")
    def submission_detail(submission_id: str):
        row = db.one(
            "SELECT s.*,v.title AS job_title,v.company FROM submissions s "
            "JOIN vacancies v ON v.id=s.vacancy_id WHERE s.id=?", (submission_id,))
        if not row:
            raise HTTPException(404, "Submission not found")
        return submission_record(row, include_package=True)

    @app.get("/api/submissions/{submission_id}/resume")
    def submission_resume(submission_id: str):
        row = db.one("SELECT * FROM submissions WHERE id=?", (submission_id,))
        if not row:
            raise HTTPException(404, "Submission not found")
        path = submission_resume_path(row)
        if not path:
            raise HTTPException(404, "Exact submitted resume is not available")
        return FileResponse(path, media_type="application/pdf", filename=f"submitted-resume-{submission_id[:8]}.pdf",
                            content_disposition_type="inline")

    @app.get("/api/submissions/{submission_id}/attachments/{field_index}")
    def submission_attachment_file(submission_id: str, field_index: str):
        row = db.one("SELECT * FROM submissions WHERE id=?", (submission_id,))
        if not row:
            raise HTTPException(404, "Submission not found")
        attachment = submission_attachment(row, field_index)
        if not attachment:
            raise HTTPException(404, "Exact submitted attachment is not available")
        path, filename = attachment
        return FileResponse(path, media_type="application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream",
                            filename=filename)

    @app.get("/api/submissions")
    def submissions():
        # Legacy raw feed retained for compatibility; the product UI uses the paginated normalized history.
        return db.all("SELECT * FROM submissions ORDER BY sent_at DESC LIMIT 100")

    return app
