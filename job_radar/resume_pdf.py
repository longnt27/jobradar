"""Compact, selectable-text A4 resume based on the user's supplied layout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .settings import Settings


def _fonts() -> tuple[str, str, str]:
    base = Path("/System/Library/Fonts/Supplemental")
    variants = (("JobRadarRegular", "Arial.ttf"), ("JobRadarBold", "Arial Bold.ttf"),
                ("JobRadarItalic", "Arial Italic.ttf"))
    try:
        for name, filename in variants:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(base / filename)))
        return tuple(name for name, _ in variants)
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def render_resume(settings: Settings, draft_id: str, resume: dict) -> tuple[str, str]:
    settings.ensure_dirs()
    revision = hashlib.sha256(json.dumps(resume, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    path = settings.artifact_dir / f"resume-{draft_id}-{revision}.pdf"
    regular, bold, italic = _fonts()
    width, height = A4
    left = right = 36
    top = bottom = 27.36
    usable = width - left - right
    doc = canvas.Canvas(str(path), pagesize=A4, pageCompression=1)
    doc.setTitle(f"{resume.get('name', '')} — Resume")
    doc.setAuthor(resume.get("name", ""))
    y = height - top

    def check(space: float) -> None:
        nonlocal y
        if y - space < bottom:
            doc.showPage()
            y = height - top

    def line(value: str, font: str = regular, size: float = 9.4, leading: float = 11.5,
             indent: float = 0, color: str = "#1e1e1e") -> None:
        nonlocal y
        doc.setFillColor(HexColor(color))
        doc.setFont(font, size)
        for paragraph in (str(value).replace("\x00", "").splitlines() or [""]):
            for part in simpleSplit(paragraph, font, size, usable - indent) or [""]:
                check(leading)
                doc.drawString(left + indent, y, part)
                y -= leading

    def section(title: str) -> None:
        nonlocal y
        check(29)
        y -= 5
        doc.setFont(bold, 12)
        doc.setFillColor(HexColor("#1e1e1e"))
        doc.drawString(left, y, title.upper())
        y -= 3
        doc.setStrokeColor(HexColor("#b4b4b4"))
        doc.setLineWidth(.65)
        doc.line(left, y, width - right, y)
        y -= 12

    def bullet(value: str) -> None:
        nonlocal y
        size = 9.3
        leading = 11.3
        parts = simpleSplit(str(value).replace("\x00", ""), regular, size, usable - 21) or [""]
        check(leading * len(parts) + 1)
        doc.setFont(regular, size)
        doc.setFillColor(HexColor("#1e1e1e"))
        doc.drawString(left + 10, y, "•")
        for part in parts:
            doc.drawString(left + 21, y, part)
            y -= leading

    name = str(resume.get("name") or "")
    doc.setFillColor(HexColor("#1e1e1e"))
    doc.setFont(bold, 20)
    doc.drawCentredString(width / 2, y - 4, name)
    y -= 29
    contacts = [resume.get("email"), resume.get("phone")]
    contacts += [link.removeprefix("https://").removeprefix("http://") for link in resume.get("links", []) if link]
    contacts = [str(value) for value in contacts if value]
    contact_lines = []
    current = ""
    for value in contacts:
        candidate = f"{current}    ·    {value}" if current else value
        if current and pdfmetrics.stringWidth(candidate, regular, 8.4) > usable:
            contact_lines.append(current)
            current = value
        else:
            current = candidate
    if current:
        contact_lines.append(current)
    doc.setFont(regular, 8.4)
    doc.setFillColor(HexColor("#646464"))
    for value in contact_lines:
        doc.drawCentredString(width / 2, y, value)
        y -= 11
    y -= 6
    if resume.get("summary"):
        line(resume["summary"], italic, 9.3, 11.5)
        y -= 2

    positions = resume.get("experience") or []
    if positions:
        section("Experience")
        for item in positions:
            check(32)
            company = item.get("company") or item.get("title") or ""
            dates = item.get("dates", "")
            doc.setFont(bold, 9.6)
            doc.setFillColor(HexColor("#1e1e1e"))
            doc.drawString(left, y, company)
            doc.setFont(regular, 9.2)
            doc.drawRightString(width - right, y, dates)
            y -= 12
            if item.get("role"):
                line(item["role"], italic, 9.2, 11)
            for value in item.get("bullets", []):
                bullet(value)
            y -= 3

    projects = resume.get("projects") or []
    if projects:
        section("Selected Projects")
        for project in projects:
            check(31)
            title = project.get("title", "")
            url = project.get("repository_url") or ""
            short_url = url.removeprefix("https://github.com/")
            doc.setFillColor(HexColor("#1e1e1e"))
            doc.setFont(bold, 9.6)
            doc.drawString(left, y, title)
            if url and pdfmetrics.stringWidth(title, bold, 9.6) + pdfmetrics.stringWidth(short_url, regular, 8) < usable - 12:
                doc.setFont(regular, 8)
                doc.drawRightString(width - right, y, short_url)
                doc.linkURL(url, (width - right - pdfmetrics.stringWidth(short_url, regular, 8), y - 2, width - right, y + 9), relative=0)
            y -= 11
            stack = project.get("tech_stack") or []
            if stack:
                line(" · ".join(stack), italic, 8.5, 10)
            for value in project.get("bullets", []):
                bullet(value)
            y -= 3

    education = resume.get("education") or []
    if education:
        section("Education")
        for item in education:
            if isinstance(item, str):
                line(item, bold, 9.5, 12)
                continue
            check(24)
            doc.setFont(bold, 9.5)
            doc.drawString(left, y, item.get("school", ""))
            doc.setFont(regular, 9)
            doc.drawRightString(width - right, y, item.get("dates", ""))
            y -= 12
            line(item.get("degree", ""), regular, 9.2, 12)

    achievements = resume.get("achievements") or []
    if achievements:
        section("Achievements")
        for value in achievements:
            bullet(value)

    groups = resume.get("skill_groups") or {}
    if not groups and resume.get("skills"):
        groups = {"Skills": ", ".join(resume["skills"])}
    if groups:
        section("Skills")
        label_width = min(105, max(pdfmetrics.stringWidth(label, bold, 8.8) for label in groups) + 12)
        for label, values in groups.items():
            value = ", ".join(values) if isinstance(values, list) else str(values)
            parts = simpleSplit(value, regular, 9, usable - label_width) or [""]
            check(len(parts) * 11 + 2)
            doc.setFont(bold, 8.8)
            doc.drawString(left, y, label)
            doc.setFont(regular, 9)
            for part in parts:
                doc.drawString(left + label_width, y, part)
                y -= 11
            y -= 1

    doc.save()
    reader = PdfReader(str(path))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not name or name not in extracted or len(extracted.strip()) < 40:
        path.unlink(missing_ok=True)
        raise ValueError("Resume PDF failed text validation")
    if len(reader.pages) > 2:
        path.unlink(missing_ok=True)
        raise ValueError("Resume exceeds two pages; shorten the selected content")
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()
