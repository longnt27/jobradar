"""Deliver a human-readable application review before its versioned PDF packet."""

from __future__ import annotations

import html
import json
from io import BytesIO
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .application_action import describe_application_action
from .notifications import telegram_config
from .resume_pdf import _fonts
from .settings import Settings


def format_review_details(draft: dict, blockers: list[str]) -> str:
    """Text for the review cover. The actual tailored CV follows as PDF pages."""
    message = draft["message_data"]
    form = draft["form_data"]
    destination = draft["destination"]
    lines = [f"Application review · {draft['job_title']} at {draft['company']}",
             f"Match score: {draft['job_score']}/100" if draft.get("job_score") is not None else "Match score: unavailable",
             f"Version: {draft['package_hash'][:12]}",
             f"Destination: {destination.get('email') or destination.get('url') or 'Missing'}", "",
             "JOB DESCRIPTION", str(draft.get("job_description", "")), "",
             "APPLICATION ACTION", f"Channel: {destination.get('kind', 'unknown')}",
             f"Subject: {message.get('subject', '')}", str(message.get("body", ""))]
    if form.get("fields") or form.get("action"):
        lines.extend(["", "FORM ANSWERS AND ATTACHMENTS",
                      f"Form action: {form.get('action', '')}", f"Form method: {form.get('method', '')}"])
        for field in form.get("fields", []):
            index = str(field["index"])
            value = form.get("attachments", {}).get(index) if field["type"] == "file" else form.get("answers", {}).get(index, "")
            lines.append(f"{field.get('label') or field.get('name') or index}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}")
    notes = [str(item) for item in draft.get("warnings", [])] + [str(item) for item in blockers]
    if notes:
        lines.extend(["", "REVIEW NOTES", *notes])
    lines.extend(["", "TAILORED CV", "The following pages are the exact CV attached to this application draft."])
    return "\n".join(lines).strip()


def build_review_pdf(settings: Settings, draft: dict, blockers: list[str]) -> Path:
    """Build a readable review cover, then append the exact tailored CV."""
    settings.ensure_dirs()
    path = settings.artifact_dir / f"review-{draft['id']}-{draft['package_hash'][:16]}.pdf"
    regular, bold, _ = _fonts()
    body = ParagraphStyle("Review body", fontName=regular, fontSize=9, leading=13,
                          textColor=colors.HexColor("#263831"), spaceAfter=4, splitLongWords=1)
    heading = ParagraphStyle("Review heading", parent=body, fontName=bold, fontSize=11,
                             leading=15, textColor=colors.HexColor("#174d39"), spaceBefore=14, spaceAfter=6)
    title = ParagraphStyle("Review title", parent=heading, fontSize=16, leading=21, spaceBefore=0, spaceAfter=12)
    lines = format_review_details(draft, blockers).splitlines()
    headings = {"JOB DESCRIPTION", "APPLICATION ACTION", "FORM ANSWERS AND ATTACHMENTS", "REVIEW NOTES", "TAILORED CV"}
    story = []
    for index, line in enumerate(lines):
        if not line.strip():
            story.append(Spacer(1, 5))
        else:
            style = title if index == 0 else heading if line in headings else body
            story.append(Paragraph(html.escape(line), style))
    cover = BytesIO()
    SimpleDocTemplate(cover, pagesize=A4, leftMargin=42, rightMargin=42,
                      topMargin=40, bottomMargin=40, title=f"Application review - {draft['job_title']}").build(story)
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(cover.getvalue())))
    writer.append(PdfReader(str(draft["resume_path"])))
    with path.open("wb") as output:
        writer.write(output)
    return path


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
    short = draft["package_hash"][:12]
    buttons = []
    if not blockers:
        buttons.append([{"text": "Approve & send", "callback_data": f"review:approve:{draft['id']}:{short}"}])
    buttons.append([{"text": "Edit", "callback_data": f"review:edit:{draft['id']}:{short}"},
                    {"text": "Regenerate", "callback_data": f"review:retry:{draft['id']}:{short}"}])
    score = f"{draft['job_score']}/100" if draft.get("job_score") is not None else "Score unavailable"
    action = describe_application_action(draft["destination"])
    state = ("Needs changes before sending: " + "; ".join(blockers)) if blockers else "Ready to send after your approval."
    summary = (
        f"{draft['job_title'][:180]} at {draft['company'][:180]}\n"
        f"Match: {score}\n"
        f"Action: {action}\n"
        f"{state}\n"
        f"Version: {short}"
    )
    summary_result = await _post(client, token, "sendMessage", json={
        "chat_id": chat_id, "text": summary[:4000],
        "reply_markup": {"inline_keyboard": buttons},
    })
    path = build_review_pdf(settings, draft, blockers)
    try:
        with path.open("rb") as file:
            await _post(client, token, "sendDocument", data={
                "chat_id": chat_id,
                "caption": f"Full review packet · version {short}",
                "reply_parameters": json.dumps({"message_id": summary_result["message_id"]}),
            }, files={"document": ("application-review.pdf", file, "application/pdf")})
    except Exception:
        try:
            await _post(client, token, "deleteMessage", json={
                "chat_id": chat_id, "message_id": summary_result["message_id"],
            })
        except Exception:
            pass
        raise
    return summary_result["message_id"]
