from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, async_playwright

from .db import Database, new_id, now
from .drafting import draft_custom_answers, get_draft, package_hash
from .mail_config import send_smtp_message, validated_smtp_config
from .settings import Settings
from .social_browser import chrome_context_options


def _field_signature(fields: list[dict], action: str, method: str, enctype: str) -> str:
    stable = [{key: field.get(key) for key in ("index", "name", "id", "type", "required", "label", "options", "accept", "max_length")}
              for field in fields]
    return hashlib.sha256(json.dumps({"fields": stable, "action": action, "method": method, "enctype": enctype}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def _form_structure(page: Page) -> dict:
    forms = page.locator("form")
    count = await forms.count()
    candidates = []
    for form_index in range(count):
        form = forms.nth(form_index)
        metadata = await form.evaluate("node => ({action:node.action,method:node.method,enctype:node.enctype,submit:(node.querySelector('button:not([type]),button[type=submit],input[type=submit]')?.innerText || node.querySelector('input[type=submit]')?.value || '').trim()})")
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
        if fields and not any(field["type"] == "password" for field in fields):
            description = " ".join([metadata["submit"], *(field["label"] for field in fields)]).casefold()
            if not any(field["type"] == "file" for field in fields) and not re.search(r"apply|application|resume|curriculum vitae|cover letter|ứng tuyển|nộp hồ sơ", description):
                continue
            candidates.append((form_index, fields, metadata))
    if not candidates:
        raise ValueError("No recognizable application form was found at this URL")
    form_index, fields, metadata = max(candidates, key=lambda item: sum(3 if field["type"] == "file" else 2 if field["required"] else 1 for field in item[1]))
    return {"form_index": form_index, "fields": fields, "action": metadata["action"], "method": metadata["method"],
            "enctype": metadata["enctype"],
            "signature": _field_signature(fields, metadata["action"], metadata["method"], metadata["enctype"]), "final_url": page.url}


def _default_answer(field: dict, profile: dict, message: dict) -> str:
    text = f"{field['name']} {field['id']} {field['label']}".casefold()
    if field["type"] in ("file", "checkbox", "radio"):
        return ""
    if re.search(r"cover.?letter|motivation|why (this|you|us)|message to|additional information", text):
        return message.get("body", "")
    for pattern, key in ((r"salary|compensation", "salary_expectation"), (r"visa|work.?authori", "work_authorization"),
                         (r"notice.?period|start.?date", "notice_period"), (r"relocat", "relocation")):
        if re.search(pattern, text):
            return str(profile.get(key, ""))
    patterns = [
        (r"e.?mail", "email"), (r"phone|mobile|telephone", "phone"),
        (r"first.?name|given.?name", "first_name"), (r"last.?name|sur.?name|family.?name", "last_name"),
        (r"full.?name|your.?name|name", "name"), (r"city|location", "location"),
        (r"linkedin", "linkedin"), (r"github", "github"),
    ]
    for pattern, key in patterns:
        if re.search(pattern, text):
            if key == "first_name":
                return str(profile.get("given_name", ""))
            if key == "last_name":
                return str(profile.get("family_name", ""))
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
        context = await playwright.chromium.launch_persistent_context(str(settings.browser_profile), headless=True, **chrome_context_options())
        try:
            page = await context.new_page()
            await page.goto(destination["url"], wait_until="domcontentloaded", timeout=45000)
            structure = await _form_structure(page)
        finally:
            await context.close()
    profile = db.get_setting("profile", {})
    existing = draft["form_data"].get("answers", {})
    answers = {str(field["index"]): existing.get(str(field["index"]), _default_answer(field, profile, draft["message_data"]))
               for field in structure["fields"] if field["type"] != "file"}
    if draft["provider"] != "template":
        cards = [card for card in draft["resume_data"].get("evidence", []) if card.get("id") in draft["evidence_ids"]]
        job = db.one("SELECT title,company,description FROM vacancies WHERE id=?", (draft["vacancy_id"],))
        try:
            generated = await asyncio.to_thread(draft_custom_answers, draft["provider"], job, profile, cards, structure["fields"])
            for key, value in generated.items():
                if key not in existing and value:
                    answers[key] = value
        except Exception as error:
            warnings = [*draft["warnings"], f"Form answer drafting failed: {str(error)[:200]}"]
            db.execute("UPDATE application_drafts SET warnings=? WHERE id=?", (json.dumps(warnings), draft_id))
    structure["answers"] = answers
    structure["attachments"] = draft["form_data"].get("attachments", {}) if draft["form_data"].get("signature") == structure["signature"] else {}
    structure["destination_url"] = destination["url"]
    db.execute("UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
               (json.dumps(structure, ensure_ascii=False), now(), draft_id))
    return get_draft(db, draft_id)


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


def _validated_smtp_config(settings: Settings) -> dict:
    return validated_smtp_config(settings)


CONFIRMED_SUBMISSION_STATUSES = ("sent_confirmed", "submitted_confirmed")
UNCERTAIN_SUBMISSION_STATUSES = ("submitted_unconfirmed",)
BLOCKING_SUBMISSION_STATUSES = (*CONFIRMED_SUBMISSION_STATUSES, *UNCERTAIN_SUBMISSION_STATUSES, "sending")


def submission_outcome(status: str, destination: dict | None = None) -> dict:
    destination = destination or {}
    channel = "Email" if destination.get("kind") == "email" else "Web form" if destination.get("kind") == "web" else "Application"
    outcomes = {
        "sent_confirmed": {
            "key": "email_sent", "label": "Email sent", "tone": "success", "confirmed": True,
            "retry_blocked": True, "guidance": "Job Radar received confirmation from the mail server.",
        },
        "submitted_confirmed": {
            "key": "application_submitted", "label": "Application submitted", "tone": "success", "confirmed": True,
            "retry_blocked": True, "guidance": "The employer site showed a submission confirmation.",
        },
        "submitted_unconfirmed": {
            "key": "submission_uncertain", "label": "Submission status uncertain", "tone": "warning", "confirmed": False,
            "retry_blocked": True,
            "guidance": "Job Radar may have submitted this application. Verify on the employer site before taking another send action.",
        },
        "sending": {
            "key": "sending", "label": "Sending application", "tone": "info", "confirmed": False,
            "retry_blocked": True, "guidance": "A send is already in progress.",
        },
        "needs_user_attention": {
            "key": "needs_help", "label": "Needs your help", "tone": "warning", "confirmed": False,
            "retry_blocked": False, "guidance": "The reviewed application could not be submitted safely without your input.",
        },
    }
    outcome = dict(outcomes.get(status, {
        "key": "recorded", "label": "Submission recorded", "tone": "neutral", "confirmed": False,
        "retry_blocked": False, "guidance": "Review the submission details before taking another action.",
    }))
    outcome["channel"] = channel
    return outcome


def submission_record(row: dict, *, include_package: bool = False) -> dict:
    destination = row.get("destination") or {}
    if isinstance(destination, str):
        try:
            destination = json.loads(destination)
        except json.JSONDecodeError:
            destination = {}
    package = row.get("package_data") or {}
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except json.JSONDecodeError:
            package = {}
    outcome = submission_outcome(str(row.get("status") or ""), destination)
    result = {
        "id": row["id"], "draft_id": row["draft_id"], "vacancy_id": row["vacancy_id"],
        "package_hash": row.get("package_hash"), "status": row.get("status"),
        "sent_at": row.get("sent_at"), "updated_at": row.get("updated_at"),
        "destination": destination, "receipt": row.get("receipt"), "error": row.get("error"),
        "outcome": outcome,
        "resume_available": bool(package.get("resume_path") and Path(str(package.get("resume_path"))).is_file()),
    }
    for key in ("job_title", "company"):
        if key in row:
            result[key] = row.get(key)
    if include_package:
        result["package"] = package
    return result


def submission_resume_path(row: dict) -> Path | None:
    package = row.get("package_data") or {}
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except json.JSONDecodeError:
            return None
    path = Path(str(package.get("resume_path") or ""))
    digest = str(package.get("resume_hash") or "")
    if not path.is_file() or not digest:
        return None
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return None
    except OSError:
        return None
    return path


def _snapshot_submission_package(settings: Settings, identifier: str, draft: dict) -> dict:
    snapshot = json.loads(json.dumps(
        {key: draft[key] for key in (
            "vacancy_id", "provider", "provider_mode", "evidence_ids", "resume_data",
            "message_data", "form_data", "destination", "resume_path", "resume_hash",
        )}, ensure_ascii=False))
    directory = settings.artifact_dir / "submissions" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    resume_source = Path(draft["resume_path"])
    resume_target = directory / "resume.pdf"
    resume_target.write_bytes(resume_source.read_bytes())
    snapshot["resume_path"] = str(resume_target)

    attachments = snapshot.get("form_data", {}).get("attachments", {})
    attachment_dir = directory / "attachments"
    for key, assignment in list(attachments.items()):
        if not isinstance(assignment, dict) or assignment.get("kind") != "uploaded":
            continue
        source = Path(str(assignment.get("path") or ""))
        if not source.is_file():
            continue
        attachment_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix if source.suffix else ".bin"
        target = attachment_dir / f"{key}{suffix}"
        target.write_bytes(source.read_bytes())
        assignment["path"] = str(target)
    return snapshot


def _reviewed_attachment(settings: Settings, draft: dict, field: dict) -> str | None:
    assignment = draft["form_data"].get("attachments", {}).get(str(field["index"]))
    if not isinstance(assignment, dict):
        raise ValueError(f"Choose an attachment for {field['label'] or field['name']}")
    kind = assignment.get("kind")
    if kind == "none" and not field["required"]:
        return None
    if kind == "resume":
        path = Path(draft["resume_path"])
        digest = draft["resume_hash"]
    elif kind == "uploaded":
        path = Path(str(assignment.get("path", ""))).resolve()
        allowed = (settings.artifact_dir / draft["id"] / "attachments").resolve()
        if not path.is_relative_to(allowed):
            raise ValueError("Attachment is outside this application's files")
        digest = assignment.get("sha256", "")
    else:
        raise ValueError(f"Choose an attachment for {field['label'] or field['name']}")
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Reviewed attachment has changed or is missing: {field['label'] or field['name']}")
    accept = field["accept"].casefold()
    if accept and ".pdf" not in accept and "application/pdf" not in accept:
        raise ValueError(f"File field {field['label']} does not accept the reviewed PDF")
    return str(path)


def send_readiness(db: Database, settings: Settings, draft: dict) -> list[str]:
    reasons = []
    if draft["status"] == "sent":
        reasons.append("This application has already been sent")
    try:
        _validate_destination(draft["destination"])
    except ValueError as error:
        reasons.append(str(error))
    if not draft["message_data"].get("body") or not draft["resume_data"].get("name"):
        reasons.append("Complete the message and resume")
    path = Path(draft["resume_path"])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != draft["resume_hash"]:
        reasons.append("Save and review the current resume PDF")
    if draft["destination"].get("kind") == "email":
        try:
            _validated_smtp_config(settings)
        except ValueError as error:
            reasons.append(str(error))
    elif draft["destination"].get("kind") == "web":
        form = draft["form_data"]
        if not form.get("signature") or form.get("destination_url") != draft["destination"].get("url"):
            reasons.append("Inspect this application form before sending")
        else:
            if any(field["type"] == "file" for field in form.get("fields", [])) and form.get("enctype") != "multipart/form-data":
                reasons.append("This form cannot upload files; check the application page before sending")
            required_radios = {}
            for field in form.get("fields", []):
                if field["type"] == "file":
                    try:
                        _reviewed_attachment(settings, draft, field)
                    except ValueError as error:
                        reasons.append(str(error))
                elif field["type"] == "radio" and field["required"]:
                    required_radios.setdefault(field["name"] or str(field["index"]), []).append(field)
                elif field["required"]:
                    answer = str(form.get("answers", {}).get(str(field["index"]), "")).strip()
                    if not answer or (field["type"] == "checkbox" and answer.casefold() not in ("yes", "true", "checked")):
                        reasons.append(f"Answer required: {field['label'] or field['name']}")
            for group in required_radios.values():
                if not any(str(form.get("answers", {}).get(str(field["index"]), "")).casefold() in ("yes", "true", "checked") for field in group):
                    reasons.append(f"Choose an option: {group[0]['label'] or group[0]['name']}")
    prior = db.one(
        "SELECT status,destination FROM submissions WHERE vacancy_id=? "
        "AND status IN ('sent_confirmed','submitted_confirmed','submitted_unconfirmed','sending') "
        "ORDER BY sent_at DESC LIMIT 1", (draft["vacancy_id"],))
    if prior:
        outcome = submission_outcome(prior["status"], json.loads(prior["destination"] or "{}"))
        reasons.append(f"{outcome['label']}. {outcome['guidance']}")
    uncertain_attempt = db.one(
        "SELECT status FROM auto_application_attempts WHERE draft_id=? AND status='submission_uncertain' LIMIT 1",
        (draft["id"],),
    )
    if uncertain_attempt and not prior:
        reasons.append("Submission status uncertain. Verify on the employer site before taking another send action.")
    return reasons


def _send_email(draft: dict, settings: Settings) -> str:
    config = _validated_smtp_config(settings)
    message = EmailMessage()
    message["From"] = config.get("from", config.get("user", ""))
    message["To"] = draft["destination"]["email"]
    message["Subject"] = draft["message_data"]["subject"]
    message.set_content(draft["message_data"]["body"])
    resume_path = Path(draft["resume_path"])
    message.add_attachment(resume_path.read_bytes(), maintype="application", subtype="pdf", filename="resume.pdf")
    send_smtp_message(config, message)
    return str(message["Message-ID"] or "SMTP accepted message")


async def _send_web(settings: Settings, draft: dict) -> tuple[str, str]:
    form_data = draft["form_data"]
    if not form_data.get("signature") or form_data.get("destination_url") != draft["destination"].get("url"):
        return "needs_user_attention", "Inspect this exact application form before sending"
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(settings.browser_profile), headless=True, accept_downloads=False, **chrome_context_options())
        try:
            page = await context.new_page()
            await page.goto(draft["destination"]["url"], wait_until="domcontentloaded", timeout=45000)
            current = await _form_structure(page)
            if current["signature"] != form_data["signature"] or current["final_url"] != form_data["final_url"]:
                return "needs_user_attention", "Application destination or form fields changed after review"
            form = page.locator("form").nth(form_data["form_index"])
            fields = form.locator("input,select,textarea")
            answers = form_data.get("answers", {})
            required_radio_groups = {}
            for field in current["fields"]:
                if field["type"] == "radio" and field["required"]:
                    required_radio_groups.setdefault(field["name"] or str(field["index"]), []).append(field)
            for group in required_radio_groups.values():
                if not any(str(answers.get(str(field["index"]), "")).casefold() in ("yes", "true", "checked") for field in group):
                    return "needs_user_attention", f"Review required choice: {group[0]['label']}"
            for field in current["fields"]:
                locator = fields.nth(field["index"])
                kind = field["type"]
                answer = str(answers.get(str(field["index"]), ""))
                if field["max_length"] and len(answer) > field["max_length"]:
                    return "needs_user_attention", f"Answer exceeds character limit: {field['label']}"
                if kind == "file":
                    attachment = _reviewed_attachment(settings, draft, field)
                    if attachment:
                        await locator.set_input_files(attachment)
                elif kind in ("checkbox", "radio"):
                    if answer.casefold() in ("yes", "true", "checked"):
                        await locator.check()
                    elif kind == "checkbox" and field["required"]:
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
            submit = form.locator('button:not([type]),button[type="submit"],input[type="submit"]').first
            if not await submit.count():
                return "needs_user_attention", "No submit control was found on the reviewed form"
            invalid = form.locator("input:invalid,select:invalid,textarea:invalid").first
            if await invalid.count():
                label = await invalid.get_attribute("name") or await invalid.get_attribute("aria-label") or "form field"
                return "needs_user_attention", f"Review invalid or missing value: {label}"
            before = page.url
            before_text = (await page.locator("body").inner_text(timeout=7000))[:3000]
            submitted_requests = []
            page.on("request", lambda request: submitted_requests.append(request.url) if request.method not in ("GET", "HEAD") else None)
            await submit.click(timeout=15000)
            await page.wait_for_timeout(2500)
            visible = (await page.locator("body").inner_text(timeout=7000))[:3000]
            if submitted_requests and (page.url != before or visible != before_text) and re.search(r"thank you|application (has been |was )?(received|submitted)|successfully applied|cảm ơn|ứng tuyển thành công", visible, re.I):
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
    if draft["destination"]["kind"] == "email":
        _validated_smtp_config(settings)
    elif draft["form_data"].get("fields"):
        if any(field["type"] == "file" for field in draft["form_data"]["fields"]) and draft["form_data"].get("enctype") != "multipart/form-data":
            raise ValueError("This form cannot upload files; check the application page before sending")
        for field in draft["form_data"]["fields"]:
            if field["type"] == "file":
                _reviewed_attachment(settings, draft, field)
    resume_path = Path(draft["resume_path"])
    if not resume_path.is_file() or hashlib.sha256(resume_path.read_bytes()).hexdigest() != draft["resume_hash"]:
        raise ValueError("Reviewed resume PDF has changed or is missing")
    prior = db.one(
        "SELECT id,status,destination FROM submissions WHERE vacancy_id=? "
        "AND status IN ('sent_confirmed','submitted_confirmed','submitted_unconfirmed','sending') "
        "ORDER BY sent_at DESC LIMIT 1", (draft["vacancy_id"],))
    if prior:
        outcome = submission_outcome(prior["status"], json.loads(prior["destination"] or "{}"))
        raise ValueError(f"{outcome['label']}. {outcome['guidance']}")
    uncertain_attempt = db.one(
        "SELECT status FROM auto_application_attempts WHERE draft_id=? AND status='submission_uncertain' LIMIT 1",
        (draft_id,),
    )
    if uncertain_attempt:
        raise ValueError("Submission status uncertain. Verify on the employer site before taking another send action.")
    identifier = new_id()
    digest = package_hash(draft)
    snapshot = _snapshot_submission_package(settings, identifier, draft)
    db.execute("INSERT INTO submissions(id,draft_id,vacancy_id,package_hash,package_data,destination,status,sent_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (identifier, draft_id, draft["vacancy_id"], digest, json.dumps(snapshot, ensure_ascii=False), json.dumps(draft["destination"]), "sending", now(), now()))
    try:
        if draft["destination"]["kind"] == "email":
            receipt = await asyncio.to_thread(_send_email, draft, settings)
            status = "sent_confirmed"
        else:
            status, receipt = await _send_web(settings, draft)
        db.execute("UPDATE submissions SET status=?,receipt=?,updated_at=? WHERE id=?", (status, receipt, now(), identifier))
        if status in CONFIRMED_SUBMISSION_STATUSES:
            db.execute("UPDATE application_drafts SET status='sent',updated_at=? WHERE id=?", (now(), draft_id))
            db.execute("UPDATE vacancies SET state='applied',updated_at=? WHERE id=?", (now(), draft["vacancy_id"]))
        elif status in UNCERTAIN_SUBMISSION_STATUSES:
            db.execute("UPDATE application_drafts SET status='submission_uncertain',updated_at=? WHERE id=?", (now(), draft_id))
        return {"id": identifier, "status": status, "receipt": receipt,
                "outcome": submission_outcome(status, draft["destination"])}
    except Exception as error:
        # The transport or browser may have completed the send before failing. Block another send.
        db.execute("UPDATE submissions SET status='submitted_unconfirmed',error=?,updated_at=? WHERE id=?", (str(error)[:1000], now(), identifier))
        db.execute("UPDATE application_drafts SET status='submission_uncertain',updated_at=? WHERE id=?", (now(), draft_id))
        return {"id": identifier, "status": "submitted_unconfirmed", "error": str(error),
                "outcome": submission_outcome("submitted_unconfirmed", draft["destination"])}
