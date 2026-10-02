from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, async_playwright

from .db import Database, new_id, now
from .drafting import get_draft, package_hash
from .settings import Settings


def _field_signature(fields: list[dict]) -> str:
    stable = [{key: field.get(key) for key in ("index", "name", "id", "type", "required", "label", "options", "accept", "max_length")}
              for field in fields]
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def _form_structure(page: Page) -> dict:
    forms = page.locator("form")
    count = await forms.count()
    candidates = []
    for form_index in range(count):
        form = forms.nth(form_index)
        fields = await form.locator("input,select,textarea").evaluate_all("""nodes => nodes.map((node, index) => {
          const type = (node.getAttribute('type') || node.tagName.toLowerCase()).toLowerCase();
          if (['hidden','submit','button','reset','image'].includes(type)) return null;
          const id = node.id || '';
          const label = (node.labels && node.labels.length ? node.labels[0].innerText : '') ||
            node.getAttribute('aria-label') || node.getAttribute('placeholder') || node.getAttribute('name') || '';
          return {index, name:node.getAttribute('name') || '', id, type, required:node.required,
            label:label.trim().slice(0,250), options:node.tagName.toLowerCase() === 'select' ?
              [...node.options].map(option => ({value:option.value,text:option.text.trim()})) : [],
            accept:node.getAttribute('accept') || '', max_length:node.maxLength > 0 ? node.maxLength : null};
        }).filter(Boolean)""")
        if fields:
            candidates.append((form_index, fields))
    if not candidates:
        raise ValueError("No application form was found at this URL")
    form_index, fields = max(candidates, key=lambda item: sum(3 if field["type"] == "file" else 2 if field["required"] else 1 for field in item[1]))
    return {"form_index": form_index, "fields": fields, "signature": _field_signature(fields), "final_url": page.url}


def _default_answer(field: dict, profile: dict) -> str:
    text = f"{field['name']} {field['id']} {field['label']}".casefold()
    if field["type"] in ("file", "checkbox", "radio"):
        return ""
    patterns = [
        (r"e.?mail", "email"), (r"phone|mobile|telephone", "phone"),
        (r"first.?name|given.?name", "first_name"), (r"last.?name|sur.?name|family.?name", "last_name"),
        (r"full.?name|your.?name|name", "name"), (r"city|location", "location"),
        (r"linkedin", "linkedin"), (r"github", "github"),
    ]
    name = profile.get("name", "")
    for pattern, key in patterns:
        if re.search(pattern, text):
            if key == "first_name":
                return name.split()[0] if name else ""
            if key == "last_name":
                return " ".join(name.split()[1:]) if name else ""
            if key in ("linkedin", "github"):
                return next((link for link in profile.get("links", []) if key in link.casefold()), "")
            return str(profile.get(key, ""))
    return ""


async def inspect_form(db: Database, settings: Settings, draft_id: str) -> dict:
    draft = get_draft(db, draft_id)
    destination = draft["destination"]
    if destination.get("kind") != "web" or not destination.get("url"):
        raise ValueError("Set a web application URL before inspecting a form")
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(settings.browser_profile), headless=True)
        try:
            page = await context.new_page()
            await page.goto(destination["url"], wait_until="domcontentloaded", timeout=45000)
            structure = await _form_structure(page)
            profile = db.get_setting("profile", {})
            existing = draft["form_data"].get("answers", {})
            structure["answers"] = {str(field["index"]): existing.get(str(field["index"]), _default_answer(field, profile)) for field in structure["fields"] if field["type"] != "file"}
            structure["destination_url"] = destination["url"]
            db.execute("UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
                       (json.dumps(structure, ensure_ascii=False), now(), draft_id))
            return get_draft(db, draft_id)
        finally:
            await context.close()


def _validate_destination(destination: dict) -> None:
    kind = destination.get("kind")
    if kind == "email":
        address = destination.get("email", "")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
            raise ValueError("Enter a valid application email address")
    elif kind == "web":
        url = destination.get("url", "")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("Enter a valid application URL")
    else:
        raise ValueError("Choose an email or web application destination")


def _send_email(draft: dict) -> str:
    host = os.environ.get("JOB_RADAR_SMTP_HOST", "")
    user = os.environ.get("JOB_RADAR_SMTP_USER", "")
    password = os.environ.get("JOB_RADAR_SMTP_PASSWORD", "")
    sender = os.environ.get("JOB_RADAR_SMTP_FROM", user)
    port = int(os.environ.get("JOB_RADAR_SMTP_PORT", "587"))
    if not host or not sender:
        raise ValueError("Set JOB_RADAR_SMTP_HOST and JOB_RADAR_SMTP_FROM before sending email applications")
    if port not in (465, 587):
        raise ValueError("SMTP port must be 465 or 587")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = draft["destination"]["email"]
    message["Subject"] = draft["message_data"]["subject"]
    message.set_content(draft["message_data"]["body"])
    resume_path = Path(draft["resume_path"])
    message.add_attachment(resume_path.read_bytes(), maintype="application", subtype="pdf", filename="resume.pdf")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as connection:
            if user:
                connection.login(user, password)
            connection.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as connection:
            connection.starttls(context=ssl.create_default_context())
            if user:
                connection.login(user, password)
            connection.send_message(message)
    return str(message["Message-ID"] or "SMTP accepted message")


async def _send_web(settings: Settings, draft: dict) -> tuple[str, str]:
    form_data = draft["form_data"]
    if not form_data.get("signature") or form_data.get("destination_url") != draft["destination"].get("url"):
        return "needs_user_attention", "Inspect this exact application form before sending"
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(settings.browser_profile), headless=True, accept_downloads=False)
        try:
            page = await context.new_page()
            await page.goto(draft["destination"]["url"], wait_until="domcontentloaded", timeout=45000)
            current = await _form_structure(page)
            if current["signature"] != form_data["signature"] or current["final_url"] != form_data["final_url"]:
                return "needs_user_attention", "Application destination or form fields changed after review"
            form = page.locator("form").nth(form_data["form_index"])
            fields = form.locator("input,select,textarea")
            answers = form_data.get("answers", {})
            for field in current["fields"]:
                locator = fields.nth(field["index"])
                kind = field["type"]
                answer = str(answers.get(str(field["index"]), ""))
                if field["max_length"] and len(answer) > field["max_length"]:
                    return "needs_user_attention", f"Answer exceeds character limit: {field['label']}"
                if kind == "file":
                    if ".pdf" not in field["accept"].casefold() and field["accept"] and "application/pdf" not in field["accept"].casefold():
                        return "needs_user_attention", f"File field {field['label']} does not accept the reviewed PDF"
                    await locator.set_input_files(draft["resume_path"])
                elif kind in ("checkbox", "radio"):
                    if answer.casefold() in ("yes", "true", "checked"):
                        await locator.check()
                    elif field["required"]:
                        return "needs_user_attention", f"Review required choice: {field['label']}"
                elif kind == "select":
                    if answer:
                        await locator.select_option(value=answer)
                    elif field["required"]:
                        return "needs_user_attention", f"Answer required: {field['label']}"
                elif answer:
                    await locator.fill(answer)
                elif field["required"]:
                    return "needs_user_attention", f"Answer required: {field['label']}"
            submit = form.locator('button[type="submit"],input[type="submit"]').first
            if not await submit.count():
                return "needs_user_attention", "No submit control was found on the reviewed form"
            before = page.url
            await submit.click(timeout=15000)
            await page.wait_for_timeout(2500)
            visible = (await page.locator("body").inner_text(timeout=7000))[:3000]
            if re.search(r"thank you|application (has been |was )?(received|submitted)|successfully applied|cảm ơn|ứng tuyển thành công", visible, re.I):
                return "submitted_confirmed", f"{page.url}: {visible[:500]}"
            if page.url != before and not await page.locator("form").count():
                return "submitted_unconfirmed", f"Form navigated to {page.url}; confirmation not detected"
            return "submitted_unconfirmed", f"Submit clicked; confirmation not detected at {page.url}"
        finally:
            await context.close()


async def send_application(db: Database, settings: Settings, draft_id: str, expected_hash: str) -> dict:
    draft = get_draft(db, draft_id)
    if not expected_hash or expected_hash != package_hash(draft):
        raise ValueError("Application changed since review. Reload and review the package before sending")
    _validate_destination(draft["destination"])
    if not draft["message_data"].get("body") or not draft["resume_data"].get("name"):
        raise ValueError("Complete the message and resume before sending")
    resume_path = Path(draft["resume_path"])
    if not resume_path.is_file() or hashlib.sha256(resume_path.read_bytes()).hexdigest() != draft["resume_hash"]:
        raise ValueError("Reviewed resume PDF has changed or is missing")
    prior = db.one("SELECT id,status FROM submissions WHERE vacancy_id=? AND status IN ('sent_confirmed','submitted_confirmed','submitted_unconfirmed','sending') ORDER BY sent_at DESC LIMIT 1", (draft["vacancy_id"],))
    if prior:
        raise ValueError(f"This vacancy already has a {prior['status']} application; review the existing submission before retrying")
    identifier = new_id()
    digest = package_hash(draft)
    db.execute("INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,destination,status,sent_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
               (identifier, draft_id, draft["vacancy_id"], digest, json.dumps(draft["destination"]), "sending", now(), now()))
    try:
        if draft["destination"]["kind"] == "email":
            receipt = await asyncio.to_thread(_send_email, draft)
            status = "sent_confirmed"
        else:
            status, receipt = await _send_web(settings, draft)
        db.execute("UPDATE submissions SET status=?,receipt=?,updated_at=? WHERE id=?", (status, receipt, now(), identifier))
        if status in ("sent_confirmed", "submitted_confirmed"):
            db.execute("UPDATE application_drafts SET status='sent',updated_at=? WHERE id=?", (now(), draft_id))
            db.execute("UPDATE vacancies SET state='applied',updated_at=? WHERE id=?", (now(), draft["vacancy_id"]))
        return {"id": identifier, "status": status, "receipt": receipt}
    except Exception as error:
        # The transport or browser may have completed the send before failing. Block another send.
        db.execute("UPDATE submissions SET status='submitted_unconfirmed',error=?,updated_at=? WHERE id=?", (str(error)[:1000], now(), identifier))
        return {"id": identifier, "status": "submitted_unconfirmed", "error": str(error)}
