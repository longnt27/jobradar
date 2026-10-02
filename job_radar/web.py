from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

from .db import Database, new_id, now
from .apply import inspect_form, send_application
from .drafting import PROVIDERS, get_draft, prepare_draft, update_draft
from .evidence import inspect_repository
from .seeds import seed
from .settings import Settings
from .scanner import ScanManager


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
    apply_url: str | None = None


class StateInput(BaseModel):
    state: Literal["new", "interesting", "ignored", "prepare", "ready", "applied", "interview", "rejected", "offer"]
    reason: str | None = None


class EvidenceInput(BaseModel):
    kind: Literal["experience", "project", "education", "achievement", "certification"]
    title: str = Field(min_length=2)
    claim: str = Field(min_length=5)
    approved: bool = False
    support: list[str] = Field(default_factory=list)


class RepositoryInput(BaseModel):
    url: HttpUrl


class PrepareInput(BaseModel):
    provider: Literal["template", "codex_local", "codex", "agy", "claude"] = "template"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    db = Database(settings.database_path)
    seed(db)
    scan_manager = ScanManager(db, settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await scan_manager.start()
        try:
            yield
        finally:
            await scan_manager.stop()

    app = FastAPI(title="Job Radar", version="0.1.0", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.scan_manager = scan_manager

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
            for table in ("employers", "sources", "vacancies", "evidence", "application_drafts", "submissions"):
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts["active_sources"] = conn.execute("SELECT COUNT(*) FROM sources WHERE enabled=1").fetchone()[0]
        recent = db.all("SELECT scan_runs.*, sources.name AS source_name FROM scan_runs JOIN sources ON sources.id=scan_runs.source_id ORDER BY started_at DESC LIMIT 10")
        return {"counts": counts, "recent_runs": recent, "data_dir": str(settings.data_dir)}

    @app.get("/api/profile")
    def get_profile():
        return db.get_setting("profile", {})

    @app.put("/api/profile")
    def put_profile(profile: dict[str, Any] = Body(...)):
        if not isinstance(profile.get("name", ""), str) or not isinstance(profile.get("skills", []), list):
            raise HTTPException(422, "Profile must include a name and skills list")
        db.set_setting("profile", profile)
        return profile

    @app.get("/api/sources")
    def sources(kind: str | None = None):
        rows = db.all("SELECT * FROM sources WHERE (? IS NULL OR kind=?) ORDER BY kind,name", (kind, kind))
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
            "SELECT * FROM employers WHERE name LIKE ? AND (?='' OR category=?) ORDER BY name LIMIT ?",
            (f"%{q}%", category, category, limit),
        )

    @app.post("/api/employers", status_code=201)
    def add_employer(employer: EmployerInput):
        if db.one("SELECT id FROM employers WHERE lower(name)=lower(?)", (employer.name,)):
            raise HTTPException(409, "Employer already exists")
        identifier = new_id()
        db.execute(
            "INSERT INTO employers(id,name,category,aliases,career_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (identifier, employer.name, employer.category,
             json.dumps(employer.aliases, ensure_ascii=False), employer.career_url, now(), now()),
        )
        return {"id": identifier}

    @app.get("/api/jobs")
    def jobs(q: str = "", state: str = "", limit: int = Query(100, ge=1, le=500)):
        if q.strip():
            try:
                return db.all(
                    "SELECT v.* FROM vacancy_fts f JOIN vacancies v ON v.id=f.vacancy_id WHERE vacancy_fts MATCH ? AND (?='' OR v.state=?) ORDER BY v.score DESC,v.first_seen_at DESC LIMIT ?",
                    (q.strip(), state, state, limit),
                )
            except Exception as error:
                raise HTTPException(422, f"Invalid search: {error}") from error
        return db.all(
            "SELECT * FROM vacancies WHERE (?='' OR state=?) ORDER BY score DESC,first_seen_at DESC LIMIT ?",
            (state, state, limit),
        )

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
                 payload.location, payload.description, payload.apply_url, timestamp, timestamp, timestamp, timestamp),
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
        if not updates or set(updates) - {"kind", "title", "claim", "support", "approved"}:
            raise HTTPException(422, "Unsupported evidence fields")
        validated = EvidenceInput(**{**row, "support": json.loads(row["support"]), **updates})
        db.execute("UPDATE evidence SET kind=?,title=?,claim=?,support=?,approved=?,updated_at=? WHERE id=?",
                   (validated.kind, validated.title, validated.claim, json.dumps(validated.support), int(validated.approved), now(), evidence_id))
        return {"id": evidence_id}

    @app.post("/api/repositories/inspect")
    def inspect_repo(payload: RepositoryInput):
        try:
            return inspect_repository(db, settings, str(payload.url))
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/repositories")
    def repositories():
        return db.all("SELECT id,url,commit_sha,owner_context,summary,inspected_at FROM repository_snapshots ORDER BY inspected_at DESC")

    @app.get("/api/providers")
    def providers():
        return PROVIDERS

    @app.post("/api/jobs/{job_id}/prepare")
    def prepare(job_id: str, payload: PrepareInput):
        try:
            return prepare_draft(db, settings, job_id, payload.provider)
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
    async def send(draft_id: str):
        try:
            async with scan_manager.browser_lock:
                return await send_application(db, settings, draft_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/submissions")
    def submissions():
        return db.all("SELECT * FROM submissions ORDER BY sent_at DESC LIMIT 100")

    return app
