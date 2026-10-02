"""Import the fields in the supplied LaTeX resume layout into the local profile."""

from __future__ import annotations

import re

from .db import new_id


def _plain(value: str) -> str:
    value = re.sub(r"\\href\{[^{}]+\}\{([^{}]+)\}", r"\1", value)
    for _ in range(4):
        value = re.sub(r"\\(?:textbf|textit|texttt|small|normalfont)\{([^{}]*)\}", r"\1", value)
    value = value.replace(r"\textperiodcentered", "·").replace(r"\rightarrow", "→").replace("$", "")
    value = value.replace(r"\--", "–").replace("--", "–")
    value = re.sub(r"\\([#$%&_{}'])", r"\1", value)
    value = re.sub(r"\\(?:small|normalfont|fa[A-Za-z]+)\b", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _section(source: str, title: str) -> str:
    match = re.search(r"\\section\{" + re.escape(title) + r"\}(.*?)(?=\\section\{|\\end\{document\})", source, re.S)
    return match.group(1) if match else ""


def _bullets(source: str) -> list[str]:
    return [_plain(value) for value in re.findall(r"\\item\s+(.*?)(?=\\item\s+|\\end\{itemize\})", source, re.S)]


def parse_resume_template(source: str) -> dict:
    if "\\begin{document}" not in source or "\\section{Experience}" not in source:
        raise ValueError("Expected the supplied LaTeX resume layout with an Experience section")
    profile: dict = {}
    name = re.search(r"\\name\{([^{}]+)\}", source)
    email = re.search(r"mailto:([^{}]+)", source)
    phone = re.search(r"\\contactitem\{\\faPhone\}\{([^{}]+)\}", source)
    summary = re.search(r"\\end\{center\}.*?\\noindent\s*\\textit\{([^{}]+)\}", source, re.S)
    if name:
        profile["name"] = _plain(name.group(1))
    if email:
        profile["email"] = email.group(1)
    if phone:
        profile["phone"] = _plain(phone.group(1))
    if summary:
        profile["summary"] = _plain(summary.group(1))
    profile["links"] = [url for url in re.findall(r"\\href\{(https?://[^{}]+)\}", source.split("\\end{center}")[0])]

    experience = _section(source, "Experience")
    profile["experience"] = [
        {"id": new_id(), "company": _plain(company), "dates": _plain(dates), "role": _plain(role), "bullets": _bullets(body)}
        for company, dates, role, body in re.findall(
            r"\\begin\{expentry\}\{([^{}]+)\}\{([^{}]+)\}\{([^{}]+)\}(.*?)\\end\{expentry\}", experience, re.S
        )
    ]
    education = _section(source, "Education")
    profile["education"] = [
        {"school": _plain(school), "dates": _plain(dates), "degree": _plain(degree)}
        for school, dates, degree in re.findall(r"\\textbf\{([^{}]+)\}\s*&\s*(.*?)\\\\\s*(.*?)\s*&", education, re.S)
    ]
    profile["achievements"] = _bullets(_section(source, "Achievements"))
    groups: dict[str, str] = {}
    for raw in _section(source, "Skills").splitlines():
        if "&" not in raw or raw.lstrip().startswith("%"):
            continue
        label, values = raw.split("&", 1)
        if "begin{" not in label:
            groups[_plain(label)] = _plain(re.split(r"\\\\(?:\[\d+pt\])?", values, maxsplit=1)[0])
    profile["skill_groups"] = groups
    profile["skills"] = [value.strip() for label, values in groups.items() if label != "Languages" for value in values.split(",") if value.strip()]
    return profile
