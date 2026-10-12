"""Facebook contact evidence and deterministic first-contact messages."""
from __future__ import annotations

import json
import re
from string import Formatter

from .db import Database

DEFAULT_ZALO_TEMPLATE = (
    "Em chào anh/chị, em thấy bài đăng tuyển vị trí {position} trong nhóm Facebook {group}.\n"
    "Anh/chị có thể chia sẻ JD chi tiết cho em được không ạ?\n"
    "Em có thể gửi CV qua Zalo cho anh/chị được không ạ?"
)
_SEPARATOR = r"[ .()-]*"
_PHONE = re.compile(
    rf"(?<![\w+])(?:\+?84{_SEPARATOR}|0)[35789](?:{_SEPARATOR}\d){{8}}(?!\d)"
    rf"|(?<![\w+])\+[1-9](?:{_SEPARATOR}\d){{7,14}}(?!\d)"
)
_ZALO = re.compile(r"\bzalo\b", re.I)


def normalize_zalo_phone(value: str) -> str:
    value = str(value or "").strip()
    if not value or not re.fullmatch(r"[+\d .()-]+", value):
        raise ValueError("Enter a valid Zalo phone number")
    digits = re.sub(r"\D", "", value)
    if re.fullmatch(r"84[35789]\d{8}", digits):
        return "0" + digits[2:]
    if re.fullmatch(r"0[35789]\d{8}", digits) and not value.startswith("+"):
        return digits
    if value.startswith("+") and re.fullmatch(r"[1-9]\d{7,14}", digits):
        return "+" + digits
    raise ValueError("Enter a valid Zalo phone number")


def validate_zalo_template(template: str) -> str:
    template = str(template or "").strip()
    if not template:
        raise ValueError("Enter a Zalo message template")
    try:
        for _literal, field, spec, conversion in Formatter().parse(template):
            if field is not None and (field not in {"position", "group"} or spec or conversion):
                raise ValueError("Use only {position} and {group} in the Zalo template")
    except ValueError as error:
        raise ValueError("Use only {position} and {group} in the Zalo template") from error
    return template


def render_zalo_message(template: str, position: str, group: str) -> str:
    if not str(position or "").strip():
        raise ValueError("Enter the position as it should appear in your message")
    return validate_zalo_template(template).format(position=position.strip(), group=str(group or "").strip())


def facebook_observations(db: Database, vacancy_id: str) -> list[dict]:
    if not db.one("SELECT id FROM vacancies WHERE id=?", (vacancy_id,)):
        raise KeyError("Job not found")
    rows = db.all(
        "SELECT o.id AS observation_id,o.raw_text,o.payload,o.url AS post_url,s.name AS group_name,v.title "
        "FROM vacancy_observations vo JOIN observations o ON o.id=vo.observation_id "
        "JOIN sources s ON s.id=o.source_id JOIN vacancies v ON v.id=vo.vacancy_id "
        "WHERE vo.vacancy_id=? AND s.kind='facebook' ORDER BY o.first_seen_at,o.id", (vacancy_id,),
    )
    for row in rows:
        try:
            payload = json.loads(row.pop("payload") or "{}")
        except (ValueError, TypeError):
            payload = {}
        row["position"] = str(payload.get("title") or row.pop("title")).strip()
        row["group"] = row.pop("group_name")
    return rows


def contact_candidates(db: Database, vacancy_id: str) -> list[dict]:
    candidates = []
    for observation in facebook_observations(db, vacancy_id):
        lines = observation["raw_text"].splitlines()
        seen = set()
        for index, line in enumerate(lines):
            context = "\n".join(lines[max(0, index - 1):index + 2])
            if not _ZALO.search(context):
                continue
            for match in _PHONE.finditer(line):
                try:
                    phone = normalize_zalo_phone(match.group())
                except ValueError:
                    continue
                if phone in seen:
                    continue
                seen.add(phone)
                candidates.append({key: observation[key] for key in ("observation_id", "post_url", "group", "position")} | {
                    "phone": phone, "raw_phone": match.group(), "evidence": context.strip(),
                })
    return candidates
