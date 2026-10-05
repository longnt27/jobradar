from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _text_overlap(left: str, right: str) -> float:
    first, second = set(normalize_text(left).split()), set(normalize_text(right).split())
    return len(first & second) / len(first | second) if first and second else 0.0


def _contacts(value: str) -> set[str]:
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", value.casefold())
    phones = [re.sub(r"\D", "", phone) for phone in re.findall(r"(?:\+?\d[\d .()-]{7,}\d)", value)]
    return set(emails) | {phone for phone in phones if len(phone) >= 9}


def _same_facebook_job(job: ObservedJob, candidate: dict) -> bool:
    if (job.company != "Facebook post" and candidate["company"] != "Facebook post"
            and normalize_text(job.company) != normalize_text(candidate["company"])):
        return False
    if _text_overlap(job.title, candidate["title"]) < 0.3:
        return False
    if job.apply_url and candidate["apply_url"] and normalize_url(job.apply_url) == candidate["apply_url"]:
        return True
    if min(len(normalize_text(job.description)), len(normalize_text(candidate["description"]))) < 100:
        return False
    overlap = _text_overlap(job.description[:5000], candidate["description"][:5000])
    return overlap >= 0.88 or (overlap >= 0.68 and bool(_contacts(job.description) & _contacts(candidate["description"])))


def _employer_id(conn, company: str) -> str | None:
    row = conn.execute("SELECT id FROM employers WHERE lower(name)=lower(?)", (company,)).fetchone()
    if row:
        return row[0]
    for row in conn.execute("SELECT id,aliases FROM employers"):
        if company.casefold() in {alias.casefold() for alias in json.loads(row[1])}:
            return row[0]
    return None


def _posting_date_changed(previous: str | None, current: str | None) -> bool:
    if not current or current == previous:
        return False
    if not previous:
        return True
    try:
        old = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        new = datetime.fromisoformat(current.replace("Z", "+00:00"))
        old = old.replace(tzinfo=old.tzinfo or timezone.utc)
        new = new.replace(tzinfo=new.tzinfo or timezone.utc)
        return abs(new - old) >= timedelta(hours=12)
    except ValueError:
        return True


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
                "SELECT v.id,v.title,v.description,v.location,v.published_at,v.analysis_status,v.analysis_model "
                "FROM vacancy_observations vo JOIN vacancies v ON v.id=vo.vacancy_id WHERE vo.observation_id=?",
                (observation_id,),
            ).fetchone()
            if linked:
                score, detail = score_job({**job.__dict__, "first_seen_at": timestamp}, profile)
                unchanged = (linked["title"] == job.title and linked["description"] == job.description
                             and (linked["location"] or "") == job.location)
                keep_model_score = (unchanged and not _posting_date_changed(linked["published_at"], job.published_at)
                                    and matching_model and linked["analysis_status"] == "done"
                                    and linked["analysis_model"] == matching_model)
                conn.execute(
                    "UPDATE vacancies SET company=?,title=?,description=?,location=?,work_mode=?,apply_url=?,"
                    "published_at=COALESCE(?,published_at),last_seen_at=?,updated_at=? WHERE id=?",
                    (job.company, job.title, job.description, job.location, job.work_mode,
                     normalize_url(job.apply_url) if job.apply_url else None, job.published_at,
                     timestamp, timestamp, linked["id"]),
                )
                if linked["analysis_status"] != "dismissed" and not keep_model_score:
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

        # One vacancy can be observed by multiple search feeds or reposted in groups.
        duplicate = conn.execute(
            "SELECT vo.vacancy_id FROM observations o JOIN vacancy_observations vo ON vo.observation_id=o.id "
            "WHERE o.url=? AND o.id<>? LIMIT 1", (url, observation_id),
        ).fetchone()
        merge_reason = "same_url" if duplicate else None
        if not duplicate and urlsplit(url).hostname in {"facebook.com", "www.facebook.com"}:
            candidates = conn.execute(
                "SELECT DISTINCT v.id,v.title,v.company,v.description,v.apply_url FROM vacancies v "
                "JOIN vacancy_observations vo ON vo.vacancy_id=v.id "
                "JOIN observations o ON o.id=vo.observation_id "
                "JOIN sources s ON s.id=o.source_id WHERE s.kind='facebook' "
                "ORDER BY v.first_seen_at DESC LIMIT 1000"
            ).fetchall()
            duplicate = next((candidate for candidate in candidates if _same_facebook_job(job, dict(candidate))), None)
            if duplicate:
                merge_reason = "facebook_content"
        if duplicate:
            vacancy_id = duplicate["vacancy_id"] if "vacancy_id" in duplicate.keys() else duplicate["id"]
            conn.execute("INSERT OR IGNORE INTO vacancy_observations(vacancy_id,observation_id,merge_reason) VALUES(?,?,?)",
                         (vacancy_id, observation_id, merge_reason))
            linked = conn.execute("SELECT published_at,analysis_status FROM vacancies WHERE id=?", (vacancy_id,)).fetchone()
            if job.published_at and not linked["published_at"]:
                conn.execute("UPDATE vacancies SET published_at=?,last_seen_at=?,analysis_status=?,"
                             "analysis_error=NULL,updated_at=? WHERE id=?",
                             (job.published_at, timestamp, "pending" if matching_model and linked["analysis_status"] != "dismissed" else linked["analysis_status"],
                              timestamp, vacancy_id))
            else:
                conn.execute("UPDATE vacancies SET last_seen_at=? WHERE id=?", (timestamp, vacancy_id))
            return vacancy_id, False

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
