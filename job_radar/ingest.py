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
    with db.connection() as conn:
        existing_observation = conn.execute("SELECT id,content_hash FROM observations WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        if existing_observation:
            observation_id = existing_observation[0]
            conn.execute(
                "UPDATE observations SET last_seen_at=?,raw_text=?,content_hash=?,payload=?,published_at=COALESCE(?,published_at) WHERE id=?",
                (timestamp, raw, digest, json.dumps(job.__dict__, ensure_ascii=False), job.published_at, observation_id),
            )
            linked = conn.execute("SELECT vacancy_id FROM vacancy_observations WHERE observation_id=?", (observation_id,)).fetchone()
            if linked:
                if existing_observation[1] != digest:
                    score, detail = score_job({**job.__dict__, "first_seen_at": timestamp}, profile)
                    conn.execute("UPDATE vacancies SET title=?,description=?,location=?,score=?,score_detail=?,last_seen_at=?,updated_at=? WHERE id=?",
                                 (job.title, job.description, job.location, score, json.dumps(detail, ensure_ascii=False), timestamp, timestamp, linked[0]))
                    conn.execute("UPDATE vacancy_fts SET title=?,company=?,description=? WHERE vacancy_id=?",
                                 (job.title, job.company, job.description, linked[0]))
                else:
                    conn.execute("UPDATE vacancies SET last_seen_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, linked[0]))
                return linked[0], False
        else:
            observation_id = new_id()
            conn.execute(
                "INSERT INTO observations(id,source_id,external_id,url,content_hash,raw_text,payload,published_at,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (observation_id, source_id, job.external_id, url, digest, raw,
                 json.dumps(job.__dict__, ensure_ascii=False), job.published_at, timestamp, timestamp),
            )

        vacancy = None
        if job.apply_url:
            candidate = conn.execute("SELECT id,company,title FROM vacancies WHERE apply_url=?", (normalize_url(job.apply_url),)).fetchone()
            if candidate and normalize_text(candidate[1]) == normalize_text(job.company) and normalize_text(candidate[2]) == normalize_text(job.title):
                vacancy = candidate
        if not vacancy and job.company and job.title and job.company not in {"Facebook post", "Unknown employer"}:
            candidates = conn.execute(
                "SELECT id,title,location FROM vacancies WHERE lower(company)=lower(?) ORDER BY first_seen_at DESC LIMIT 40", (job.company,)
            ).fetchall()
            for candidate in candidates:
                if normalize_text(candidate[1]) == normalize_text(job.title) and (
                    not candidate[2] or not job.location or normalize_text(candidate[2]) == normalize_text(job.location)
                ):
                    vacancy = candidate
                    break
        if vacancy:
            vacancy_id = vacancy[0]
            conn.execute("UPDATE vacancies SET last_seen_at=?,updated_at=? WHERE id=?", (timestamp, timestamp, vacancy_id))
            merge_reason = "apply_url_or_company_title_location"
            is_new = False
        else:
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
                "INSERT INTO vacancies(id,employer_id,company,title,location,work_mode,description,apply_url,published_at,first_seen_at,last_seen_at,score,score_detail,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vacancy_id, employer_id, job.company, job.title, job.location, job.work_mode,
                 job.description, normalize_url(job.apply_url) if job.apply_url else None,
                 job.published_at, timestamp, timestamp, score, json.dumps(detail, ensure_ascii=False), timestamp, timestamp),
            )
            conn.execute("INSERT INTO vacancy_fts(vacancy_id,title,company,description) VALUES(?,?,?,?)",
                         (vacancy_id, job.title, job.company, job.description))
            merge_reason = "new_vacancy"
            is_new = True
        conn.execute(
            "INSERT OR IGNORE INTO vacancy_observations(vacancy_id,observation_id,merge_reason) VALUES(?,?,?)",
            (vacancy_id, observation_id, merge_reason),
        )
        return vacancy_id, is_new
