from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

from .db import Database, new_id, now
from .apply import inspect_form, send_application
from .browser_login import BrowserLoginManager
from .drafting import PROVIDERS, get_draft, prepare_draft, update_draft
from .evidence import generate_project_content, inspect_repository
from .github import list_public_repositories
from .mail_config import save_smtp, smtp_config
from .notifications import save_telegram, telegram_config
from .ranking import rescore_vacancies
from .resume_import import parse_resume_template
from .resume_extract import extract_resume
from .seeds import seed
from .settings import Settings
from .scanner import ScanManager
from .service import service_path


class SourceInput(BaseModel):
    kind: Literal["linkedin", "facebook", "career"]
    name: str = Field(min_length=2)
    url: HttpUrl
    employer_id: str | None = None
    enabled: bool = True
    interval_minutes: int = Field(default=240, ge=15, le=10080)
    config: dict[str, Any] = Field(default_factory=dict)


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
    provider: Literal["codex_local", "codex", "agy", "claude"]


class ProjectGenerationInput(BaseModel):
    provider: Literal["template", "codex_local", "codex", "agy", "claude"] | None = None


class RepositoryInput(BaseModel):
    url: HttpUrl
    provider: Literal["template", "codex_local", "codex", "agy", "claude"] | None = None


class PrepareInput(BaseModel):
    provider: Literal["template", "codex_local", "codex", "agy", "claude"] | None = None


class SendInput(BaseModel):
    package_hash: str = Field(min_length=64, max_length=64)


class SmtpInput(BaseModel):
    host: str = Field(min_length=2)
    port: Literal[465, 587] = 587
    user: str = ""
    password: str = ""
    from_address: str = Field(min_length=3)


class TelegramInput(BaseModel):
    token: str = ""
    chat_id: str = Field(min_length=1)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    db = Database(settings.database_path)
    seed(db)
    scan_manager = ScanManager(db, settings)
    login_manager = BrowserLoginManager(db, settings, scan_manager.browser_lock)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await scan_manager.start()
        try:
            yield
        finally:
            await login_manager.stop()
            await scan_manager.stop()

    app = FastAPI(title="Job Radar", version="0.1.0", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.scan_manager = scan_manager
    app.state.login_manager = login_manager

    def provider_available(provider: str) -> bool:
        command = "codex" if provider.startswith("codex") else provider
        return bool(shutil.which(command)) and (provider != "codex_local" or bool(shutil.which("ollama")))

    def configured_provider() -> str:
        provider = db.get_setting("profile", {}).get("drafting_provider", "")
        if not provider:
            raise HTTPException(409, "Choose a drafting provider in Profile first")
        if not provider_available(provider):
            raise HTTPException(422, f"{provider} is not available on this Mac; change it in Profile")
        return provider

    def save_profile(profile: dict[str, Any]) -> None:
        db.set_setting("profile", profile)
        rescore_vacancies(db, profile)

    def attach_career_source(employer_id: str, name: str, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HTTPException(422, "Career page must be an HTTP or HTTPS URL")
        db.execute("UPDATE sources SET enabled=0 WHERE employer_id=? AND kind='career' AND url<>?", (employer_id, url))
        if db.one("SELECT id FROM sources WHERE employer_id=? AND kind='career' AND url=?", (employer_id, url)):
            db.execute("UPDATE sources SET enabled=1 WHERE employer_id=? AND kind='career' AND url=?", (employer_id, url))
        else:
            db.execute("INSERT INTO sources(id,kind,name,url,employer_id,interval_minutes,created_at) VALUES(?,?,?,?,?,?,?)",
                       (new_id(), "career", f"{name} careers", url, employer_id, 240, now()))

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/app.js")
    def javascript():
        return FileResponse(Path(__file__).parent / "static" / "app.js", media_type="application/javascript")

    @app.get("/app.css")
    def stylesheet():
        return FileResponse(Path(__file__).parent / "static" / "app.css", media_type="text/css")

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
        recent = db.all("SELECT scan_runs.*, sources.name AS source_name FROM scan_runs JOIN sources ON sources.id=scan_runs.source_id ORDER BY started_at DESC LIMIT 10")
        return {"counts": counts, "recent_runs": recent, "data_dir": str(settings.data_dir)}

    @app.get("/api/setup")
    def setup_status():
        profile = db.get_setting("profile", {})
        mail = smtp_config(settings)
        telegram = telegram_config(settings)
        return {
            "profile_complete": bool(profile.get("name") and profile.get("email")),
            "selected_provider": profile.get("drafting_provider", ""),
            "approved_evidence": db.one("SELECT COUNT(*) AS count FROM evidence WHERE approved=1 AND kind='project'")["count"],
            "facebook_groups": db.one("SELECT COUNT(*) AS count FROM sources WHERE kind='facebook' AND enabled=1")["count"],
            "linkedin_searches": db.one("SELECT COUNT(*) AS count FROM sources WHERE kind='linkedin' AND enabled=1")["count"],
            "browser": login_manager.status(),
            "smtp_configured": bool(mail.get("host") and mail.get("from")),
            "smtp_host": mail.get("host", ""),
            "smtp_port": mail.get("port", 587),
            "smtp_user": mail.get("user", ""),
            "smtp_from": mail.get("from", ""),
            "telegram_configured": bool(telegram.get("token") and telegram.get("chat_id")),
            "telegram_chat_id": telegram.get("chat_id", ""),
            "service_installed": service_path().exists(),
            "providers": {name: bool(shutil.which(name)) for name in ("codex", "agy", "claude", "ollama")},
        }

    @app.post("/api/setup/browser/start")
    async def start_browser_login():
        return login_manager.start()

    @app.post("/api/setup/browser/finish")
    async def finish_browser_login():
        try:
            result = await login_manager.finish()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        scan_manager.queue_due()
        return result

    @app.post("/api/setup/smtp")
    def configure_mail(payload: SmtpInput):
        save_smtp(settings, {"host": payload.host, "port": payload.port, "user": payload.user,
                             "password": payload.password or smtp_config(settings).get("password", ""), "from": payload.from_address})
        return {"configured": True}

    @app.delete("/api/setup/smtp")
    def remove_mail():
        (settings.data_dir / "smtp.json").unlink(missing_ok=True)
        return {"configured": False}

    @app.post("/api/setup/telegram")
    def configure_alerts(payload: TelegramInput):
        save_telegram(settings, {"token": payload.token or telegram_config(settings).get("token", ""), "chat_id": payload.chat_id})
        return {"configured": True}

    @app.delete("/api/setup/telegram")
    def remove_alerts():
        (settings.data_dir / "telegram.json").unlink(missing_ok=True)
        return {"configured": False}

    @app.get("/api/profile")
    def get_profile():
        return db.get_setting("profile", {})

    @app.put("/api/profile")
    def put_profile(profile: dict[str, Any] = Body(...)):
        if not isinstance(profile.get("name", ""), str) or not isinstance(profile.get("skills", []), list):
            raise HTTPException(422, "Profile must include a name and skills list")
        if profile.get("drafting_provider") and profile["drafting_provider"] not in {"codex_local", "codex", "agy", "claude"}:
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
        return {"provider": payload.provider, "mode": PROVIDERS[payload.provider]}

    @app.post("/api/profile/resume/pdf")
    async def import_pdf_resume(file: UploadFile = File(...)):
        provider = configured_provider()
        data = await file.read(10_000_001)
        try:
            extracted = await asyncio.to_thread(extract_resume, data, provider)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error
        profile = {**db.get_setting("profile", {}), **extracted}
        save_profile(profile)
        return {"positions": len(profile["experience"]), "education": len(profile["education"]),
                "achievements": len(profile["achievements"]), "provider": provider,
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
        rows = db.all("SELECT s.* FROM sources s WHERE (? IS NULL OR s.kind=?) AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=s.employer_id AND e.coverage_status='excluded_hcm') ORDER BY s.kind,s.name", (kind, kind))
        for row in rows:
            row["config"] = json.loads(row["config"])
            row["enabled"] = bool(row["enabled"])
        return rows

    @app.post("/api/sources", status_code=201)
    def add_source(source: SourceInput):
        if source.employer_id and not db.one("SELECT id FROM employers WHERE id=?", (source.employer_id,)):
            raise HTTPException(404, "Employer not found")
        identifier = new_id()
        db.execute(
            "INSERT INTO sources(id,kind,name,url,employer_id,enabled,interval_minutes,config,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (identifier, source.kind, source.name, str(source.url), source.employer_id,
             int(source.enabled), source.interval_minutes, json.dumps(source.config), now()),
        )
        return {"id": identifier}

    @app.patch("/api/sources/{source_id}")
    def edit_source(source_id: str, updates: dict[str, Any] = Body(...)):
        row = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
        if not row:
            raise HTTPException(404, "Source not found")
        allowed = {"name", "url", "enabled", "interval_minutes", "config"}
        if not updates or set(updates) - allowed:
            raise HTTPException(422, "Unsupported source fields")
        merged = {**row, "config": json.loads(row["config"]), **updates}
        validated = SourceInput(**merged)
        db.execute(
            "UPDATE sources SET name=?,url=?,enabled=?,interval_minutes=?,config=? WHERE id=?",
            (validated.name, str(validated.url), int(validated.enabled),
             validated.interval_minutes, json.dumps(validated.config), source_id),
        )
        return {"id": source_id}

    @app.post("/api/sources/{source_id}/scan")
    async def scan_one(source_id: str):
        if not db.one("SELECT id FROM sources WHERE id=?", (source_id,)):
            raise HTTPException(404, "Source not found")
        return await scan_manager.run_source(source_id)

    @app.post("/api/scan/due")
    def scan_due():
        return {"queued": scan_manager.queue_due()}

    @app.get("/api/employers")
    def employers(q: str = "", category: str = "", limit: int = Query(300, ge=1, le=2000)):
        return db.all(
            "SELECT e.*,CASE WHEN EXISTS(SELECT 1 FROM sources s WHERE s.employer_id=e.id AND s.enabled=1) THEN 'active_scan' ELSE 'source_discovery' END AS live_coverage FROM employers e WHERE e.coverage_status!='excluded_hcm' AND name LIKE ? AND (?='' OR category=?) ORDER BY name LIMIT ?",
            (f"%{q}%", category, category, limit),
        )

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

    @app.get("/api/jobs")
    def jobs(q: str = "", state: str = "", limit: int = Query(100, ge=1, le=500)):
        def with_sources(rows: list[dict]) -> list[dict]:
            if not rows:
                return rows
            placeholders = ",".join("?" for _ in rows)
            origins = db.all(
                "SELECT vo.vacancy_id,o.url,o.last_seen_at,s.kind,s.name FROM vacancy_observations vo "
                "JOIN observations o ON o.id=vo.observation_id JOIN sources s ON s.id=o.source_id "
                f"WHERE vo.vacancy_id IN ({placeholders}) ORDER BY o.last_seen_at DESC",
                tuple(row["id"] for row in rows),
            )
            by_id = {}
            for origin in origins:
                by_id.setdefault(origin["vacancy_id"], {key: origin[key] for key in ("url", "last_seen_at", "kind", "name")})
            for row in rows:
                row["source"] = by_id.get(row["id"])
            return rows
        if q.strip():
            try:
                return with_sources(db.all(
                    "SELECT v.* FROM vacancy_fts f JOIN vacancies v ON v.id=f.vacancy_id WHERE vacancy_fts MATCH ? AND (?='' OR v.state=?) AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm') ORDER BY v.score DESC,v.first_seen_at DESC LIMIT ?",
                    (q.strip(), state, state, limit),
                ))
            except Exception as error:
                raise HTTPException(422, f"Invalid search: {error}") from error
        return with_sources(db.all(
            "SELECT v.* FROM vacancies v WHERE (?='' OR v.state=?) AND NOT EXISTS(SELECT 1 FROM employers e WHERE e.id=v.employer_id AND e.coverage_status='excluded_hcm') ORDER BY v.score DESC,v.first_seen_at DESC LIMIT ?",
            (state, state, limit),
        ))

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        row = db.one("SELECT * FROM vacancies WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "Job not found")
        row["observations"] = db.all(
            "SELECT o.url,o.first_seen_at,o.published_at,s.kind,s.name FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id JOIN sources s ON s.id=o.source_id WHERE vo.vacancy_id=?",
            (job_id,),
        )
        return row

    @app.post("/api/jobs/import", status_code=201)
    def import_job(payload: JobInput):
        identifier = new_id()
        timestamp = now()
        employer = db.one("SELECT id FROM employers WHERE lower(name)=lower(?)", (payload.company,))
        with db.connection() as conn:
            conn.execute(
                "INSERT INTO vacancies(id,employer_id,company,title,location,description,apply_url,first_seen_at,last_seen_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, employer["id"] if employer else None, payload.company, payload.title,
                 payload.location, payload.description, str(payload.apply_url) if payload.apply_url else None, timestamp, timestamp, timestamp, timestamp),
            )
            conn.execute("INSERT INTO vacancy_fts(vacancy_id,title,company,description) VALUES(?,?,?,?)",
                         (identifier, payload.title, payload.company, payload.description))
        return {"id": identifier}

    @app.post("/api/jobs/{job_id}/state")
    def update_state(job_id: str, payload: StateInput):
        if not db.one("SELECT id FROM vacancies WHERE id=?", (job_id,)):
            raise HTTPException(404, "Job not found")
        with db.connection() as conn:
            conn.execute("UPDATE vacancies SET state=?,updated_at=? WHERE id=?", (payload.state, now(), job_id))
            conn.execute("INSERT INTO feedback(id,vacancy_id,state,reason,created_at) VALUES(?,?,?,?,?)",
                         (new_id(), job_id, payload.state, payload.reason, now()))
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
        db.execute("UPDATE evidence SET kind=?,title=?,claim=?,details=?,support=?,approved=?,updated_at=? WHERE id=?",
                   (validated.kind, validated.title, validated.claim, json.dumps(details, ensure_ascii=False), json.dumps(validated.support), int(validated.approved), now(), evidence_id))
        return {"id": evidence_id}

    @app.post("/api/repositories/inspect")
    def inspect_repo(payload: RepositoryInput):
        try:
            provider = payload.provider or configured_provider()
            result = inspect_repository(db, settings, str(payload.url))
            try:
                result["project_content"] = generate_project_content(db, result["evidence_id"], provider)
            except RuntimeError as error:
                result["generation_warning"] = str(error)
            return result
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/evidence/{evidence_id}/generate")
    def generate_project(evidence_id: str, payload: ProjectGenerationInput):
        try:
            return generate_project_content(db, evidence_id, payload.provider or configured_provider())
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

    @app.post("/api/jobs/{job_id}/prepare")
    def prepare(job_id: str, payload: PrepareInput):
        try:
            return prepare_draft(db, settings, job_id, payload.provider or configured_provider())
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/applications")
    def applications():
        rows = db.all("SELECT id FROM application_drafts ORDER BY created_at DESC LIMIT 100")
        return [get_draft(db, row["id"]) for row in rows]

    @app.get("/api/applications/{draft_id}")
    def application(draft_id: str):
        try:
            return get_draft(db, draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @app.patch("/api/applications/{draft_id}")
    def edit_application(draft_id: str, updates: dict[str, Any] = Body(...)):
        try:
            return update_draft(db, settings, draft_id, updates)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/applications/{draft_id}/resume")
    def resume_file(draft_id: str):
        try:
            draft = get_draft(db, draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        path = Path(draft["resume_path"])
        if not path.is_file():
            raise HTTPException(404, "Resume file not found")
        return FileResponse(path, media_type="application/pdf", filename=f"resume-{draft_id[:8]}.pdf")

    @app.post("/api/applications/{draft_id}/attachments")
    async def upload_application_attachment(draft_id: str, file: UploadFile = File(...)):
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
        try:
            async with scan_manager.browser_lock:
                return await inspect_form(db, settings, draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/applications/{draft_id}/send")
    async def send(draft_id: str, payload: SendInput):
        try:
            async with scan_manager.browser_lock:
                return await send_application(db, settings, draft_id, payload.package_hash)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/submissions")
    def submissions():
        return db.all("SELECT * FROM submissions ORDER BY sent_at DESC LIMIT 100")

    return app
