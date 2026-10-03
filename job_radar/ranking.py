from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .db import Database, now
import json


ROLE_WORDS = ("ai", "machine learning", "research", "llm", "language model", "computer vision", "data scientist", "robotics", "perception", "generative")
NEGATIVE_WORDS = ("sales", "recruiter", "accountant", "driver", "customer service", "telesales")


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    title = (job.get("title") or "").casefold()
    description = (job.get("description") or "").casefold()
    location = (job.get("location") or "").casefold()
    role_matches = [word for word in ROLE_WORDS if word in title]
    role = 25 if role_matches else (10 if any(word in description for word in ROLE_WORDS) else 0)
    if any(word in title for word in NEGATIVE_WORDS):
        role = 0

    skills = [str(skill).strip() for skill in profile.get("skills", []) if str(skill).strip()]
    matched_skills = [skill for skill in skills if skill.casefold() in f"{title} {description}"]
    skill = min(25, round(25 * len(matched_skills) / max(1, min(len(skills), 5)))) if skills else 12

    years = re.search(r"(?:at least|minimum|min\.?|tối thiểu)?\s*(\d{1,2})\s*\+?\s*(?:years?|năm)", description)
    years_required = int(years.group(1)) if years else None
    experience = 15 if years_required is None else max(8, 20 - max(0, years_required - 2) * 2)

    research = 15 if any(word in f"{title} {description}" for word in ("research", "nghiên cứu", "agent", "model development")) else 8
    preferred = str(profile.get("location") or "Hanoi").casefold()
    local = 10 if any(word in location for word in ("hanoi", "hà nội", "remote", "vietnam", "việt nam")) else 5 if not location else 2
    if "hanoi" not in preferred and "hà nội" not in preferred:
        local = 8 if location else 5

    published = job.get("published_at") or job.get("first_seen_at")
    freshness = 3
    if published:
        try:
            published_time = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if published_time.tzinfo is None:
                published_time = published_time.replace(tzinfo=timezone.utc)
            age_hours = max(0, (datetime.now(timezone.utc) - published_time).total_seconds() / 3600)
            freshness = 5 if age_hours <= 24 else 4 if age_hours <= 72 else 2 if age_hours <= 336 else 0
        except ValueError:
            pass
    components = {"role": role, "skills": skill, "experience": experience, "research": research, "location": local, "freshness": freshness}
    explanation = (
        f"Role terms: {', '.join(role_matches) if role_matches else 'no direct title match'}. "
        f"Matched profile skills: {', '.join(matched_skills[:6]) if matched_skills else 'not yet identified'}. "
        f"Experience: {years_required if years_required is not None else 'not stated'} years stated; not treated as an automatic rejection."
    )
    return min(100, sum(components.values())), {"components": components, "matched_skills": matched_skills, "years_required": years_required, "explanation": explanation}


def rescore_vacancies(db: Database, profile: dict[str, Any]) -> None:
    with db.connection() as conn:
        rows = conn.execute("SELECT id,title,description,location,published_at,first_seen_at FROM vacancies").fetchall()
        for row in rows:
            score, detail = score_job(dict(row), profile)
            conn.execute("UPDATE vacancies SET score=?,score_detail=?,updated_at=? WHERE id=?",
                         (score, json.dumps(detail, ensure_ascii=False), now(), row["id"]))
