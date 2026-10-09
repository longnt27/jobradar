"""Render tailored resumes with the user's one-page LaTeX template."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .settings import Settings


_TEMPLATE = Path(__file__).with_name("templates") / "resume.tex"
_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}
_ITEM_COMMANDS = {"textbf", "textit", "emph", "underline", "textbackslash",
                  "textasciitilde", "textasciicircum", "&", "%", "$", "#", "_", "{", "}"}


def _fonts() -> tuple[str, str, str]:
    """The Telegram review cover still uses ReportLab and these fonts."""
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


def _tex(value: object) -> str:
    return "".join(_ESCAPES.get(c, c) for c in str(value or "").replace("\x00", ""))


def _url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return raw if not any(c in raw for c in "{}\\\n\r%") else None


def _link(url: object, label: object) -> str:
    target = _url(url)
    return (r"\href{\detokenize{" + target + "}}{" + _tex(label) + "}") if target else _tex(label)


def _bullet(value: object, phrases: list[str]) -> str:
    text = str(value or "")
    if text.lstrip().startswith(r"\item"):
        source = text.strip()
        if not source.startswith(r"\item ") or "\n" in source or len(source) > 5000:
            raise ValueError("Each LaTeX bullet must be one \\item line under 5,000 characters")
        content = source[len(r"\item "):]
        for command in re.findall(r"\\([A-Za-z]+|.)", content):
            if command not in _ITEM_COMMANDS:
                raise ValueError(f"Unsupported LaTeX command in bullet: \\{command}")
        if re.search(r"(?<!\\)[%&#$_]", content):
            raise ValueError("Escape LaTeX special characters in bullets, for example 95\\%")
        depth = 0
        for index, character in enumerate(content):
            if index and content[index - 1] == "\\":
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
            if depth < 0:
                break
        if depth != 0:
            raise ValueError("LaTeX bullet braces must be balanced")
        return source
    phrase = next((p for p in phrases if p and p in text and p != text.strip()), None)
    if not phrase:
        return r"\item " + _tex(text)
    before, after = text.split(phrase, 1)
    return r"\item " + _tex(before) + r"\textbf{" + _tex(phrase) + "}" + _tex(after)


def _items(values: list[str], phrases: list[str] | None = None) -> str:
    lines = [_bullet(v, phrases or []) for v in values if str(v).strip()]
    return "\n".join([r"\begin{itemize}", *lines, r"\end{itemize}"]) if lines else ""


def editable_bullet_lines(resume: dict) -> dict[str, list]:
    """Return the exact LaTeX item lines shown in the resume detail editor."""
    phrases = [str(p) for p in resume.get("bold_phrases") or [] if p]
    return {
        "experience": [[_bullet(value, phrases) for value in item.get("bullets") or []]
                       for item in resume.get("experience") or []],
        "projects": [[_bullet(value, [] if index == 0 else phrases)
                      for index, value in enumerate(item.get("bullets") or [])]
                     for item in resume.get("projects") or []],
        "achievements": [_bullet(value, []) for value in resume.get("achievements") or []],
    }


def _body(resume: dict) -> str:
    pieces = [r"\begin{center}", r"\name{" + _tex(resume.get("name")) + r"}\\[4pt]", r"\small"]
    contacts = []
    for field, icon in (("email", r"\faEnvelope"), ("phone", r"\faPhone")):
        if resume.get(field):
            contacts.append(r"\contactitem{" + icon + "}{" + _tex(resume[field]) + "}")
    for value in resume.get("links") or []:
        target = _url(value)
        if not target:
            continue
        host = urlsplit(target).hostname or ""
        icon = r"\faLinkedin" if "linkedin.com" in host else r"\faGithub" if "github.com" in host else r"\faGlobe"
        label = target.removeprefix("https://").removeprefix("http://").rstrip("/")
        contacts.append(r"\contactitem{" + icon + "}{" + _link(target, label) + "}")
    pieces += ["\n\\quad\n".join(contacts), r"\end{center}", r"\vspace{1pt}"]
    if resume.get("summary"):
        pieces += [r"\noindent", r"\textit{" + _tex(resume["summary"]) + "}", r"\vspace{-5pt}"]

    phrases = [str(p) for p in resume.get("bold_phrases") or [] if p]
    if resume.get("experience"):
        pieces.append(r"\section{Experience}")
        for item in resume["experience"]:
            pieces.append(r"\begin{expentry}{" + _tex(item.get("company") or item.get("title")) +
                          "}{" + _tex(item.get("dates")) + "}{" + _tex(item.get("role")) + "}")
            pieces.append(_items(item.get("bullets") or [], phrases))
            pieces.append(r"\end{expentry}")

    if resume.get("projects"):
        pieces.append(r"\section{Selected Projects}")
        for project in resume["projects"]:
            title = _link(project.get("repository_url"), project.get("title"))
            if target := _url(project.get("repository_url")):
                parsed = urlsplit(target)
                label = parsed.path.strip("/") if parsed.hostname == "github.com" else (
                    parsed.hostname + parsed.path.rstrip("/"))
                icon = r"\faGithub" if parsed.hostname == "github.com" else r"\faGlobe"
                title += (r" \hfill \normalfont\small\href{\detokenize{" + target +
                          "}}{" + icon + r"\ " + _tex(label) + "}")
            stack = r" \textperiodcentered\ ".join(_tex(v) for v in (project.get("tech_stack") or [])[:5])
            pieces.append(r"\begin{projentry}{" + title + "}{" + stack + "}")
            bullets = project.get("bullets") or []
            if bullets:
                pieces.append("\n".join([r"\begin{itemize}", _bullet(bullets[0], []),
                                         *(_bullet(value, phrases) for value in bullets[1:]),
                                         r"\end{itemize}"]))
            pieces.append(r"\end{projentry}")

    if resume.get("education"):
        pieces.append(r"\section{Education}")
        for item in resume["education"]:
            if isinstance(item, str):
                pieces.append(r"\noindent\textbf{" + _tex(item) + "}")
                continue
            pieces += [r"\noindent\begin{tabularx}{\linewidth}{@{}X r@{}}",
                       r"\textbf{" + _tex(item.get("school")) + "} & " + _tex(item.get("dates")) + r" \\",
                       _tex(item.get("degree")) + " &", r"\end{tabularx}"]

    if resume.get("achievements"):
        pieces += [r"\section{Achievements}", _items(resume["achievements"])]

    groups = resume.get("skill_groups") or {}
    if not groups and resume.get("skills"):
        groups = {"Skills": resume["skills"]}
    if groups:
        pieces += [r"\section{Skills}",
                   r"\noindent\begin{tabularx}{\linewidth}{@{}>{\bfseries\small}l @{\hspace{0.75em}} X@{}}"]
        for label, values in groups.items():
            value = ", ".join(values) if isinstance(values, list) else str(values)
            pieces.append(_tex(label) + " & " + _tex(value) + r" \\[1pt]")
        pieces.append(r"\end{tabularx}")
    return "\n".join(pieces)


def render_resume(settings: Settings, draft_id: str, resume: dict) -> tuple[str, str]:
    settings.ensure_dirs()
    template = _TEMPLATE.read_text(encoding="utf-8")
    revision = hashlib.sha256((template + json.dumps(resume, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:16]
    path = settings.artifact_dir / f"resume-{draft_id}-{revision}.pdf"
    if path.exists():
        return str(path), hashlib.sha256(path.read_bytes()).hexdigest()
    compiler = shutil.which("tectonic") or "/opt/homebrew/bin/tectonic"
    if not Path(compiler).is_file():
        raise RuntimeError("The LaTeX resume renderer (tectonic) is unavailable on this Mac")
    source = template.replace("__PDF_TITLE__", _tex(f"{resume.get('name', '')} — Resume"))
    source = source.replace("__PDF_AUTHOR__", _tex(resume.get("name")))
    source = source.replace("__BODY__", _body(resume))
    with tempfile.TemporaryDirectory(prefix="job-radar-resume-") as directory:
        tex_path = Path(directory) / "resume.tex"
        tex_path.write_text(source, encoding="utf-8")
        result = subprocess.run([compiler, "--outdir", directory, str(tex_path)],
                                capture_output=True, text=True, timeout=90, check=False)
        output = Path(directory) / "resume.pdf"
        if result.returncode or not output.exists():
            raise ValueError("Resume template could not be rendered: " + (result.stderr or result.stdout)[-1200:])
        reader = PdfReader(output)
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        name = str(resume.get("name") or "")
        if len(reader.pages) != 1:
            raise ValueError(f"Resume template filled {len(reader.pages)} pages; shorten the draft content")
        if not name or name not in extracted or len(extracted.strip()) < 40:
            raise ValueError("Resume PDF failed text validation")
        shutil.copy2(output, path)
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()
