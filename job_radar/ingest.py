from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .db import Database, new_id, now
from .ranking import score_job


@dataclass
class ObservedJob:
    url: str
    title: str
    company: str
    description: str
    location: str = ""
    work_mode: str = ""
    apply_url: str | None = None
    external_id: str | None = None
    published_at: str | None = None
    raw_text: str | None = None


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                           if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "trk", "trackingid", "refid"}))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _employer_id(conn, company: str) -> str | None:
    row = conn.execute("SELECT id FROM employers WHERE lower(name)=lower(?)", (company,)).fetchone()
    if row:
        return row[0]
    for row in conn.execute("SELECT id,aliases FROM employers"):
        if company.casefold() in {alias.casefold() for alias in json.loads(row[1])}:
            return row[0]
    return None


def ingest(db: Database, source_id: str, job: ObservedJob) -> tuple[str, bool]:
    url = normalize_url(job.url)
    raw = job.raw_text or job.description
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    timestamp = now()
    profile = db.get_setting("profile", {})
    matching_model = db.get_setting("matching_model", "")
    with db.connection() as conn:
        existing_observation = conn.execute("SELECT id,content_hash FROM observations WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        if existing_observation:
            observation_id = existing_observation[0]
            conn.execute(
                "UPDATE observations SET last_seen_at=?,raw_text=?,content_hash=?,payload=?,published_at=COALESCE(?,published_at) WHERE id=?",
                (timestamp, raw, digest, json.dumps(job.__dict__, ensure_ascii=False), job.published_at, observation_id),
            )
            linked = conn.execute(
                "SELECT v.id,v.title,v.description,v.location,v.analysis_status,v.analysis_model "
                "FROM vacancy_observations vo JOIN vacancies v ON v.id=vo.vacancy_id WHERE vo.observation_id=?",
                (observation_id,),
            ).fetchone()
            if linked:
                score, detail = score_job({**job.__dict__, "first_seen_at": timestamp}, profile)
                unchanged = (linked["title"] == job.title and linked["description"] == job.description
                             and (linked["location"] or "") == job.location)
                keep_model_score = (unchanged and matching_model and linked["analysis_status"] == "done"
                                    and linked["analysis_model"] == matching_model)
                conn.execute(
                    "UPDATE vacancies SET company=?,title=?,description=?,location=?,work_mode=?,apply_url=?,"
                    "published_at=COALESCE(?,published_at),last_seen_at=?,updated_at=? WHERE id=?",
                    (job.company, job.title, job.description, job.location, job.work_mode,
                     normalize_url(job.apply_url) if job.apply_url else None, job.published_at,
                     timestamp, timestamp, linked["id"]),
                )
                if not keep_model_score:
                    conn.execute(
                        "UPDATE vacancies SET score=?,score_detail=?,analysis_status=?,analysis_error=NULL WHERE id=?",
                        (score, json.dumps(detail, ensure_ascii=False),
                         "pending" if matching_model else "not_configured", linked["id"]),
                    )
                conn.execute("UPDATE vacancy_fts SET title=?,company=?,description=? WHERE vacancy_id=?",
                             (job.title, job.company, job.description, linked["id"]))
                return linked["id"], False
        else:
            observation_id = new_id()
            conn.execute(
                "INSERT INTO observations(id,source_id,external_id,url,content_hash,raw_text,payload,published_at,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (observation_id, source_id, job.external_id, url, digest, raw,
                 json.dumps(job.__dict__, ensure_ascii=False), job.published_at, timestamp, timestamp),
            )

        vacancy_id = new_id()
        employer_id = _employer_id(conn, job.company)
        if not employer_id and job.company and job.company != "Facebook post":
            employer_id = new_id()
            conn.execute(
                "INSERT INTO employers(id,name,category,created_at,updated_at) VALUES(?,?,?,?,?)",
                (employer_id, job.company, "discovered", timestamp, timestamp),
            )
        score, detail = score_job({**job.__dict__, "first_seen_at": timestamp}, profile)
        conn.execute(
            "INSERT INTO vacancies(id,employer_id,company,title,location,work_mode,description,apply_url,published_at,first_seen_at,last_seen_at,score,score_detail,analysis_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vacancy_id, employer_id, job.company, job.title, job.location, job.work_mode,
             job.description, normalize_url(job.apply_url) if job.apply_url else None,
             job.published_at, timestamp, timestamp, score, json.dumps(detail, ensure_ascii=False),
             "pending" if matching_model else "not_configured", timestamp, timestamp),
        )
        conn.execute("INSERT INTO vacancy_fts(vacancy_id,title,company,description) VALUES(?,?,?,?)",
                     (vacancy_id, job.title, job.company, job.description))
        conn.execute(
            "INSERT OR IGNORE INTO vacancy_observations(vacancy_id,observation_id,merge_reason) VALUES(?,?,?)",
            (vacancy_id, observation_id, "new_vacancy"),
        )
        return vacancy_id, True
