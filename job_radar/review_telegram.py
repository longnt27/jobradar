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


def _review_signals(draft: dict) -> dict:
    detail = draft.get("job_score_detail") or {}
    facts = detail.get("facts") or {}
    criteria = detail.get("criteria") or {}
    requirements = []
    required_skills = facts.get("required_skills") or []
    if required_skills:
        requirements.append("Skills: " + ", ".join(str(item) for item in required_skills[:8]))
    if facts.get("years_required") not in (None, ""):
        requirements.append(f"Experience: {facts['years_required']} years")
    if facts.get("location"):
        requirements.append("Location: " + str(facts["location"]))
    if facts.get("work_mode"):
        requirements.append("Work mode: " + str(facts["work_mode"]))
    weakest = None
    for name, item in criteria.items():
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError, AttributeError):
            continue
        reason = str(item.get("reason") or "").strip()
        if reason and (weakest is None or score < weakest[0]):
            weakest = (score, name, reason)
    return {
        "requirements": requirements,
        "gap": weakest[2] if weakest else "",
    }


def format_review_details(draft: dict, blockers: list[str]) -> str:
    """Human-first review cover. The exact tailored CV follows as PDF pages."""
    message = draft["message_data"]
    form = draft["form_data"]
    destination = draft["destination"]
    review = draft.get("review_context") or {}
    signals = _review_signals(draft)
    destination_text = destination.get("email") or destination.get("url") or "Missing"
    location_bits = [str(draft.get("job_location") or "").strip(), str(draft.get("job_work_mode") or "").strip()]
    location_line = " · ".join(item for item in location_bits if item)
    lines = [
        f"Application review · {draft['job_title']} at {draft['company']}",
        f"Match score: {draft['job_score']}/100" if draft.get("job_score") is not None else "Match score: unavailable",
        f"Destination: {destination_text}",
    ]
    if location_line:
        lines.append(f"Role context: {location_line}")
    if draft.get("job_apply_url"):
        lines.append(f"Source: {draft['job_apply_url']}")
    lines.extend(["", "QUICK REVIEW"])
    if signals["requirements"]:
        lines.extend(signals["requirements"])
    if signals["gap"]:
        lines.append("Main gap to check: " + signals["gap"])
    selected = review.get("selected_evidence") or []
    if selected:
        lines.append("Evidence used: " + "; ".join(
            f"{item.get('title', 'Project')} ({item.get('reason', 'approved evidence')})"
            for item in selected[:3]
        ))
    risky = review.get("risky_claims") or []
    if risky:
        lines.append("Claims to verify: " + " | ".join(str(item.get("text") or "") for item in risky[:3]))

    lines.extend(["", "WHAT WILL BE SENT"])
    kind = destination.get("kind", "unknown")
    if kind == "email":
        lines.extend([
            "Channel: Email",
            f"Subject: {message.get('subject', '')}",
            str(message.get("body", "")),
            "Attachment: tailored resume PDF",
        ])
    elif kind == "web":
        lines.append("Channel: Web application form")
    else:
        lines.append("Channel: Manual handoff")

    if form.get("fields") or form.get("action"):
        lines.append("")
        lines.append("FORM ANSWERS AND ATTACHMENTS")
        for field in form.get("fields", []):
            index = str(field["index"])
            value = form.get("attachments", {}).get(index) if field["type"] == "file" else form.get("answers", {}).get(index, "")
            label = field.get("label") or field.get("name") or index
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if rendered not in ("", "null", "{}"):
                lines.append(f"{label}: {rendered}")

    notes = [str(item) for item in draft.get("warnings", [])] + [str(item) for item in blockers]
    if notes:
        lines.extend(["", "NEEDS ATTENTION", *notes])
    lines.extend(["", "TAILORED CV", "The following pages are the exact resume attached to this application."])
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
    headings = {"QUICK REVIEW", "WHAT WILL BE SENT", "FORM ANSWERS AND ATTACHMENTS", "NEEDS ATTENTION", "TAILORED CV"}
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
                             client: httpx.AsyncClient | None = None, *, updated: bool = False) -> int:
    config = telegram_config(settings)
    if not config.get("token") or not config.get("chat_id"):
        raise ValueError("Configure a Telegram bot and private chat in My profile first")
    if client is None:
        async with httpx.AsyncClient(timeout=30) as owned:
            return await send_review_packet(settings, draft, blockers, owned, updated=updated)
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
    review = draft.get("review_context") or {}
    risky_count = len(review.get("risky_claims") or [])
    warning_count = len(draft.get("warnings") or []) + len(blockers)
    summary = (
        f"{'Updated review · ' if updated else ''}{draft['job_title'][:180]} at {draft['company'][:180]}\n"
        f"Match: {score}\n"
        f"Action: {action}\n"
        f"{state}\n"
        f"Review focus: {warning_count} warning{'s' if warning_count != 1 else ''}"
        f"{f' · {risky_count} claim{\'s\' if risky_count != 1 else \'\'} to verify' if risky_count else ''}"
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
                "caption": "Review packet · quick review + exact resume",
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
