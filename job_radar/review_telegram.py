"""Deliver application packages and versioned review actions to Telegram."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from .notifications import telegram_config
from .settings import Settings


def format_review_details(draft: dict, blockers: list[str]) -> str:
    resume = draft["resume_data"]
    message = draft["message_data"]
    form = draft["form_data"]
    destination = draft["destination"]
    lines = [f"Application review · {draft['job_title']} at {draft['company']}",
             f"Destination: {destination.get('email') or destination.get('url') or 'Missing'}",
             f"Version: {draft['package_hash'][:12]}", "", "JOB DESCRIPTION",
             str(draft.get("job_description", "")), "", "RESUME",
             f"Name: {resume.get('name', '')}", f"Email: {resume.get('email', '')}",
             f"Phone: {resume.get('phone', '')}", f"Location: {resume.get('location', '')}",
             f"Links: {', '.join(resume.get('links', []))}",
             f"Summary: {resume.get('summary', '')}", "", "EXPERIENCE"]
    for item in resume.get("experience", []):
        lines.append(" · ".join(str(item.get(key, "")) for key in ("role", "company", "dates")))
        lines.extend(f"• {bullet}" for bullet in item.get("bullets", []))
    lines.extend(["", "SELECTED PROJECTS"])
    for item in resume.get("projects", []):
        lines.append(str(item.get("title", "")))
        if item.get("repository_url"):
            lines.append(str(item["repository_url"]))
        if item.get("tech_stack"):
            lines.append(f"Technologies: {', '.join(item['tech_stack'])}")
        lines.extend(f"• {bullet}" for bullet in item.get("bullets", []))
    lines.extend(["", "EDUCATION"])
    for item in resume.get("education", []):
        lines.append(" · ".join(str(item.get(key, "")) for key in ("school", "degree", "dates")) if isinstance(item, dict) else str(item))
    lines.extend(["", "ACHIEVEMENTS", *(str(item) for item in resume.get("achievements", [])),
                  "", "SKILLS", *(str(item) for item in resume.get("skills", [])),
                  *(f"{name}: {', '.join(values) if isinstance(values, list) else values}"
                    for name, values in resume.get("skill_groups", {}).items()),
                  "", "APPLICATION MESSAGE", f"Subject: {message.get('subject', '')}",
                  str(message.get("body", "")), "", "FORM ANSWERS AND ATTACHMENTS",
                  f"Form action: {form.get('action', '')}", f"Form method: {form.get('method', '')}"])
    for field in form.get("fields", []):
        index = str(field["index"])
        value = form.get("attachments", {}).get(index) if field["type"] == "file" else form.get("answers", {}).get(index, "")
        lines.append(f"{field.get('label') or field.get('name') or index}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}")
    lines.extend(["", "REVIEW NOTES", *(str(item) for item in draft.get("warnings", [])),
                  *(str(item) for item in blockers)])
    return "\n".join(lines).strip()


def _chunks(value: str, size: int = 3800) -> list[str]:
    lines = value.splitlines(keepends=True)
    chunks: list[str] = []
    current = ""
    for line in lines:
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


async def _post(client: httpx.AsyncClient, token: str, method: str, **kwargs) -> dict:
    response = await client.post(f"https://api.telegram.org/bot{token}/{method}", **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram rejected {method}")
    return payload["result"]


async def send_review_packet(settings: Settings, draft: dict, blockers: list[str],
                             client: httpx.AsyncClient | None = None) -> int:
    config = telegram_config(settings)
    if not config.get("token") or not config.get("chat_id"):
        raise ValueError("Configure a Telegram bot and private chat in My profile first")
    if client is None:
        async with httpx.AsyncClient(timeout=30) as owned:
            return await send_review_packet(settings, draft, blockers, owned)
    token, chat_id = config["token"], config["chat_id"]
    chat = await _post(client, token, "getChat", json={"chat_id": chat_id})
    if chat.get("type") != "private" or str(chat.get("id")) != str(chat_id):
        raise ValueError("Application reviews require your private Telegram chat")
    for part in _chunks(format_review_details(draft, blockers)):
        await _post(client, token, "sendMessage", json={"chat_id": chat_id, "text": part,
                                                         "disable_web_page_preview": True})
    path = Path(draft["resume_path"])
    with path.open("rb") as file:
        await _post(client, token, "sendDocument", data={"chat_id": chat_id,
                    "caption": f"Resume for {draft['job_title']} · version {draft['package_hash'][:12]}"},
                    files={"document": ("resume.pdf", file, "application/pdf")})
    short = draft["package_hash"][:12]
    buttons = [[{"text": "Approve & send", "callback_data": f"review:approve:{draft['id']}:{short}"}],
               [{"text": "Edit", "url": f"http://127.0.0.1:{settings.port}/#applications/{draft['id']}"},
                {"text": "Regenerate", "callback_data": f"review:retry:{draft['id']}:{short}"}]]
    result = await _post(client, token, "sendMessage", json={"chat_id": chat_id,
        "text": f"Review the details and PDF above. Approve only if this version is correct ({short}).",
        "reply_markup": {"inline_keyboard": buttons}})
    return result["message_id"]
