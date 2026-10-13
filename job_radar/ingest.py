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
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _employer_id(conn, company: str) -> str | None:
    from .dedup import company_key, employer_keys
    return employer_keys(conn).get(company_key(company))


def ingest(db: Database, source_id: str, job: ObservedJob) -> tuple[str, bool]:
    from .dedup import assess, career_requisition, employer_keys, find_match, reconcile_vacancy, refresh_vacancy, remember_match, vacancy_records

    url = normalize_url(job.url)
    raw = job.raw_text or job.description
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    timestamp = now()
    profile = db.get_setting("profile", {})
    matching_model = db.get_setting("matching_model", "")
    with db.connection() as conn:
        existing = conn.execute("SELECT id FROM observations WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        if existing:
            observation_id = existing["id"]
            conn.execute(
                "UPDATE observations SET last_seen_at=?,raw_text=?,content_hash=?,payload=?,published_at=COALESCE(?,published_at) WHERE id=?",
                (timestamp, raw, digest, json.dumps(job.__dict__, ensure_ascii=False), job.published_at, observation_id),
            )
            linked = conn.execute("SELECT vacancy_id FROM vacancy_observations WHERE observation_id=?", (observation_id,)).fetchone()
            if linked:
                refresh_vacancy(conn, linked["vacancy_id"], profile, matching_model)
                return reconcile_vacancy(conn, linked["vacancy_id"], profile, matching_model), False
        else:
            observation_id = new_id()
            conn.execute(
                "INSERT INTO observations(id,source_id,external_id,url,content_hash,raw_text,payload,published_at,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (observation_id, source_id, job.external_id, url, digest, raw,
                 json.dumps(job.__dict__, ensure_ascii=False), job.published_at, timestamp, timestamp),
            )
        source = conn.execute("SELECT kind FROM sources WHERE id=?", (source_id,)).fetchone()
        vacancy_id = new_id()
        incoming = {**job.__dict__, "id": vacancy_id, "urls": [url], "kinds": {source["kind"]},
                    "first_seen_at": timestamp,
                    "requisitions": [career_requisition(url, job.external_id)] if source["kind"] == "career" and job.external_id else []}
        exact = conn.execute(
            "SELECT vo.vacancy_id FROM observations o JOIN vacancy_observations vo ON vo.observation_id=o.id "
            "WHERE o.url=? AND o.id<>? AND NOT EXISTS(SELECT 1 FROM vacancy_observations p WHERE p.vacancy_id=vo.vacancy_id AND p.merge_reason='user_split') LIMIT 1",
            (url, observation_id),
        ).fetchone()
        match = find_match(conn, incoming) if not exact else None
        if exact or match:
            from .dedup import Match
            vacancy_id, evidence = (exact["vacancy_id"], Match(True, "same_url", 1.0)) if exact else match
            conn.execute("INSERT INTO vacancy_observations VALUES(?,?,?)", (vacancy_id, observation_id, evidence.reason))
            refresh_vacancy(conn, vacancy_id, profile, matching_model)
            return vacancy_id, False
        employer_id = _employer_id(conn, job.company)
        if not employer_id and job.company and job.company != "Facebook post":
            employer_id = new_id()
            conn.execute("INSERT INTO employers(id,name,category,created_at,updated_at) VALUES(?,?,?,?,?)",
                         (employer_id, job.company, "discovered", timestamp, timestamp))
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
        conn.execute("INSERT INTO vacancy_observations VALUES(?,?,'new_vacancy')", (vacancy_id, observation_id))
        keys = employer_keys(conn)
        for candidate in vacancy_records(conn, incoming):
            evidence = assess(incoming, candidate, keys)
            if evidence:
                remember_match(conn, vacancy_id, candidate["id"], evidence)
        return vacancy_id, True
