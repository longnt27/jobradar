"""Extract structured candidate history from a user supplied, text based PDF."""

from __future__ import annotations

import hashlib
from io import BytesIO

from pydantic import BaseModel
from pypdf import PdfReader

from .writing_preferences import render_prompt
from .db import new_id, now
from .drafting import _provider_json, provider_model_override


class ExtractedPosition(BaseModel):
    company: str
    role: str
    dates: str
    bullets: list[str]


class ExtractedEducation(BaseModel):
    school: str
    degree: str
    dates: str


class ExtractedSkillGroup(BaseModel):
    category: str
    skills: list[str]


class ExtractedResume(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    summary: str
    links: list[str]
    experience: list[ExtractedPosition]
    education: list[ExtractedEducation]
    achievements: list[str]
    skill_groups: list[ExtractedSkillGroup]


def read_resume_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValueError("Upload a PDF file")
    if len(data) > 10_000_000:
        raise ValueError("PDF must be smaller than 10 MB")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Remove the PDF password before importing")
        if not 1 <= len(reader.pages) <= 20:
            raise ValueError("PDF must contain 1 to 20 pages")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("Could not read this PDF") from error
    if len(text.strip()) < 100:
        raise ValueError("This PDF has little selectable text. Export a text based PDF and try again")
    return text[:50_000]


def extract_resume(data: bytes, provider: str, writing_preferences: dict | None = None, model: str = "") -> dict:
    text = read_resume_pdf(data)
    prompt = (
        render_prompt(writing_preferences, 'resume_import')
        + text
    )
    token = provider_model_override.set(model)
    try:
        result = _provider_json(provider, prompt, ExtractedResume)
    finally:
        provider_model_override.reset(token)
    profile = {
        "name": result.name.strip(), "email": result.email.strip(), "phone": result.phone.strip(),
        "location": result.location.strip(), "summary": result.summary.strip(),
        "links": [link.strip() for link in result.links if link.strip()],
        "experience": [{"id": new_id(), "company": item.company.strip(), "role": item.role.strip(),
                        "dates": item.dates.strip(), "bullets": [bullet.strip() for bullet in item.bullets if bullet.strip()]}
                       for item in result.experience if item.company.strip() and item.role.strip()],
        "education": [{"school": item.school.strip(), "degree": item.degree.strip(), "dates": item.dates.strip()}
                      for item in result.education if item.school.strip()],
        "achievements": [item.strip() for item in result.achievements if item.strip()],
        "skill_groups": {item.category.strip(): ", ".join(skill.strip() for skill in item.skills if skill.strip())
                         for item in result.skill_groups if item.category.strip()},
    }
    profile["skills"] = [skill.strip() for item in result.skill_groups if item.category.casefold() != "languages"
                         for skill in item.skills if skill.strip()]
    profile["resume_import"] = {"provider": provider, "model": model, "imported_at": now(), "sha256": hashlib.sha256(data).hexdigest()}
    return profile
