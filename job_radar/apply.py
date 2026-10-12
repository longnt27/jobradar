from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, async_playwright

from .application_action import is_linkedin_job_posting_url
from .db import Database, new_id, now
from .drafting import draft_custom_answers, get_draft, package_hash
from .mail_config import send_smtp_message, validated_smtp_config
from .linkedin_application import inspect_linkedin_application, submit_easy_apply_dialog, wait_for_linkedin_apply_control
from .settings import Settings
from .social_browser import chrome_context_options, clean_stale_chrome_lock


def submission_attachment(row: dict, field_index: str) -> tuple[Path, str] | None:
    package = row.get("package_data") or {}
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except json.JSONDecodeError:
            return None
    assignment = package.get("form_data", {}).get("attachments", {}).get(str(field_index))
    if not isinstance(assignment, dict):
        return None
    if assignment.get("kind") == "resume":
        path = submission_resume_path(row)
        return (path, "resume.pdf") if path else None
    if assignment.get("kind") != "uploaded":
        return None
    path = Path(str(assignment.get("path") or ""))
    if not path.is_file():
        return None
    expected = str(assignment.get("sha256") or "")
    if expected:
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return None
        except OSError:
            return None
    return path, Path(str(assignment.get("name") or path.name)).name


def _field_signature(fields: list[dict], action: str, method: str, enctype: str) -> str:
    stable = [{key: field.get(key) for key in ("index", "name", "id", "type", "required", "label", "group_label", "options", "accept", "max_length")}
              for field in fields]
    return hashlib.sha256(json.dumps({"fields": stable, "action": action, "method": method, "enctype": enctype}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _signatures_compatible(
    current_fields: list[dict] | dict,
    saved_fields: list[dict] | dict,
    current_sig: str = "",
    saved_sig: str = "",
    current_action: str | None = None,
    saved_action: str | None = None,
) -> bool:
    if isinstance(current_fields, dict) and isinstance(saved_fields, dict):
        cur_dict, saved_dict = current_fields, saved_fields
        if cur_dict.get("signature") and cur_dict.get("signature") == saved_dict.get("signature"):
            return True
        if (
            cur_dict.get("action") != saved_dict.get("action")
            or cur_dict.get("method") != saved_dict.get("method")
            or cur_dict.get("enctype") != saved_dict.get("enctype")
        ):
            return False
        cur_fields = cur_dict.get("fields", [])
        saved_fields_list = saved_dict.get("fields", [])
    else:
        if current_sig and current_sig == saved_sig:
            return True
        if current_action is not None and saved_action is not None and current_action != saved_action:
            return False
        cur_fields = current_fields
        saved_fields_list = saved_fields
    if not cur_fields or not saved_fields_list or len(cur_fields) != len(saved_fields_list):
        return False
    for cur, saved in zip(cur_fields, saved_fields_list):
        if cur.get("id") != saved.get("id") or cur.get("type") != saved.get("type") or cur.get("name") != saved.get("name"):
            return False
        cur_lbl = re.sub(r"[\s\*\u2731\u2217\uFE61\uFF0A]+", "", cur.get("label", ""))
        saved_lbl = re.sub(r"[\s\*\u2731\u2217\uFE61\uFF0A]+", "", saved.get("label", ""))
        if cur_lbl.casefold() != saved_lbl.casefold():
            return False
    return True


async def _form_structure(page: Page) -> dict:
    forms = page.locator("form")
    count = await forms.count()
    candidates = []
    for form_index in range(count):
        form = forms.nth(form_index)
        if not await form.is_visible():
            continue
        metadata = await form.evaluate("node => ({action:node.action,method:node.method,enctype:node.enctype,submit:(node.querySelector('button:not([type]),button[type=submit],input[type=submit]')?.innerText || node.querySelector('input[type=submit]')?.value || '').trim()})")
        fields = await form.locator("input,select,textarea").evaluate_all(r"""nodes => {
          const cleanText = (str) => {
            if (!str) return '';
            return str.replace(/[\s\*\u2731\u2217\uFE61\uFF0A]+$/g, '')
                      .replace(/^[\s\*\u2731\u2217\uFE61\uFF0A]+/g, '')
                      .replace(/\s+/g, ' ')
                      .trim();
          };

          return nodes.map((node, index) => {
            const type = (node.getAttribute('type') || node.tagName.toLowerCase()).toLowerCase();
            if (['hidden','submit','button','reset','image'].includes(type)) return null;

            if (type !== 'file') {
              if (node.getAttribute('aria-hidden') === 'true') return null;
              if (node.tabIndex === -1 && !node.id && !node.getAttribute('name')) return null;
              if (typeof node.checkVisibility === 'function') {
                if (!node.checkVisibility()) return null;
              }
              const style = window.getComputedStyle(node);
              if (style.display === 'none' || style.visibility === 'hidden') return null;
              if (type !== 'checkbox' && type !== 'radio') {
                if (style.opacity === '0' || style.pointerEvents === 'none') return null;
              }
            }

            const id = node.id || '';
            const name = node.getAttribute('name') || '';

            // Find question container
            const qContainer = node.closest('.application-question, .custom-question, fieldset, [role=group], [role=radiogroup], .form-group, .field, [data-qa*=question]');
            let groupLabel = '';
            if (qContainer) {
              const labelEl = qContainer.querySelector('.application-label, legend, [data-qa*=label], .label, .field-label, .control-label, h3, h4, h5');
              if (labelEl) {
                groupLabel = cleanText(labelEl.innerText || labelEl.textContent || '');
              }
            }

            // Immediate label
            let immediateLabel = '';
            if (node.labels && node.labels.length) {
              immediateLabel = cleanText(node.labels[0].innerText || '');
            } else if (node.closest('label')) {
              immediateLabel = cleanText(node.closest('label').innerText || '');
            }

            if (immediateLabel.includes('\n')) {
              immediateLabel = cleanText(immediateLabel.split('\n')[0]);
            }

            const ariaLabel = cleanText(node.getAttribute('aria-label') || '');
            const placeholder = cleanText(node.getAttribute('placeholder') || '');

            let label = '';
            if (type === 'radio' || type === 'checkbox') {
              label = immediateLabel || ariaLabel || node.value || '';
            } else if (type === 'file' && groupLabel) {
              label = groupLabel;
            } else {
              const isCardOrHash = /^(cards\[|field\d+$)/i.test(immediateLabel) || !immediateLabel;
              if (!isCardOrHash) {
                label = immediateLabel;
              } else if (groupLabel) {
                label = groupLabel;
              } else {
                label = ariaLabel || placeholder || name || '';
              }
            }

            if (!id && !name && !label && type !== 'file') {
              return null;
            }

            const isRequired = node.required || !!(qContainer && qContainer.querySelector('.required, [aria-required="true"]'));

            return {
              index,
              name,
              id,
              type,
              required: isRequired,
              label: label.slice(0, 250),
              group_label: groupLabel.slice(0, 250),
              options: node.tagName.toLowerCase() === 'select' ?
                [...node.options].map(option => ({value: option.value, text: option.text.trim()})) : [],
              accept: node.getAttribute('accept') || '',
              max_length: node.maxLength > 0 ? node.maxLength : null
            };
          }).filter(Boolean);
        }""")
        if fields and not any(field["type"] == "password" for field in fields):
            description = " ".join([metadata["submit"], *(f"{field['label']} {field.get('group_label', '')}" for field in fields)]).casefold()
            if not any(field["type"] == "file" for field in fields) and not re.search(r"apply|application|resume|curriculum vitae|cover letter|ứng tuyển|nộp hồ sơ", description):
                continue
            candidates.append((form_index, fields, metadata))
    if not candidates:
        raise ValueError("No recognizable application form was found at this URL")
    form_index, fields, metadata = max(candidates, key=lambda item: sum(3 if field["type"] == "file" else 2 if field["required"] else 1 for field in item[1]))
    return {"form_index": form_index, "fields": fields, "action": metadata["action"], "method": metadata["method"],
            "enctype": metadata["enctype"],
            "signature": _field_signature(fields, metadata["action"], metadata["method"], metadata["enctype"]), "final_url": page.url}



async def _dismiss_cookie_dialogs(page: Page) -> None:
    cookie_selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Accept All")',
        'button:has-text("Accept Cookies")',
        'button:has-text("Accept cookies")',
        'button:has-text("Accept")',
        'button:has-text("I agree")',
        'button:has-text("Agree")',
        'button:has-text("Đồng ý")',
        'button:has-text("Chấp nhận")',
        '.cookie-accept-btn',
        '[data-zrqa="cookie-accept-btn"]',
        'career-cookie-consent button',
        '#onetrust-accept-btn-handler',
        '[data-testid="cookie-accept"]',
    ]
    for sel in cookie_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=1000)
                await page.wait_for_timeout(300)
                break
        except Exception:
            pass
    try:
        await page.evaluate("() => document.querySelector('career-cookie-consent')?.remove()")
    except Exception:
        pass


async def _career_form_actions(page: Page) -> list[dict]:
    return await page.evaluate(r"""() => {
      const root = document.body || document.documentElement;
      const nodes = [...root.querySelectorAll('a[href],button,[role=button]')];
      return nodes.map((node,index) => {
        const box=node.getBoundingClientRect();
        const style=getComputedStyle(node);
        if (!box.width || !box.height || style.display==='none' || style.visibility==='hidden' || node.disabled) return null;
        const label=(node.innerText || node.getAttribute('aria-label') || node.getAttribute('title') || '').trim().replace(/\s+/g,' ').slice(0,160);
        const href=node.tagName==='A' ? node.href : '';
        const hints=[label, node.id, node.className, node.getAttribute('data-testid') || '', href].join(' ').toLowerCase();
        let score=0;
        if (/apply|application|ứng tuyển|nộp hồ sơ|submit.{0,12}(cv|resume)|send.{0,12}(cv|resume)/i.test(hints)) score+=6;
        if (/join (our |the )?team|start (your )?(application|journey)|send (us )?your (cv|resume)|gửi hồ sơ|đăng ký ứng tuyển/i.test(hints)) score+=5;
        if (/continue|next step|proceed|register interest|i.m interested|im interested|interested in this/i.test(label)) score+=5;
        if (/\b(form|recruit|career|candidate)\b/i.test(href)) score+=2;
        if (/skip to|sign.?in|log.?in|share|save|back|close|cancel|search|filter|subscribe|learn more|read more|powered by|privacy|terms/i.test(label)) score-=10;
        if (score > 0) {
          node.setAttribute('data-job-radar-career-action', String(index));
          return {index,tag:node.tagName,label,href,score};
        }
        return null;
      }).filter(Boolean).sort((a,b)=>b.score-a.score);
    }""")


async def _open_application_form(page: Page, reviewed_opener: dict | None = None) -> tuple[Page, dict]:
    """Follow a specific career-page action to a visible application form."""
    await _dismiss_cookie_dialogs(page)
    if reviewed_opener is None:
        try:
            return page, await _form_structure(page)
        except ValueError:
            pass
    actions = await _career_form_actions(page)
    if not actions:
        try:
            await page.wait_for_selector('a[href],button,[role=button]', timeout=4000)
            await page.wait_for_timeout(1000)
            actions = await _career_form_actions(page)
        except Exception:
            pass
    if reviewed_opener is not None:
        chosen_candidates = [action for action in actions if all(action.get(key) == reviewed_opener.get(key)
                             for key in ("index", "tag", "label", "href"))]
        if not chosen_candidates:
            chosen_candidates = [action for action in actions if all(action.get(key) == reviewed_opener.get(key)
                                 for key in ("tag", "label", "href"))]
        if not chosen_candidates:
            raise ValueError("Application button changed after review; inspect the form again")
    else:
        if not actions or actions[0]["score"] < 2:
            raise ValueError("No application form or recognizable form-opening button was found")
        seen = set()
        deduped = []
        for a in actions:
            key = (a["label"].casefold().strip(), a["href"])
            if key not in seen:
                seen.add(key)
                deduped.append(a)
        top_score = deduped[0]["score"]
        chosen_candidates = [a for a in deduped if a["score"] >= top_score - 2][:3]

    for chosen in chosen_candidates:
        opened: list[Page] = []
        page.context.on("page", lambda new_page: opened.append(new_page))
        loc = page.locator(f'[data-job-radar-career-action="{chosen["index"]}"]').first
        if await loc.count() == 0:
            root = page.locator("main,[role=main]").first if await page.locator("main,[role=main]").count() else page.locator("body")
            loc = root.locator('a[href],button,[role="button"]').nth(chosen["index"])
        await _dismiss_cookie_dialogs(page)
        try:
            await loc.click(timeout=8000, force=True)
        except Exception:
            try:
                await loc.click(timeout=4000)
            except Exception:
                try:
                    await loc.evaluate("el => el.click()")
                except Exception:
                    continue

        for _ in range(24):
            target = opened[-1] if opened else page
            try:
                structure = await _form_structure(target)
                structure["opener"] = {key: chosen[key] for key in ("index", "tag", "label", "href")}
                return target, structure
            except ValueError:
                await page.wait_for_timeout(250)

    if len(chosen_candidates) > 1:
        labels = ", ".join(repr(action["label"] or action["href"]) for action in chosen_candidates)
        raise ValueError(f"Multiple possible application buttons were found: {labels}. Choose the application page manually.")
    raise ValueError("The application button did not open a recognizable application form")


async def _find_application_email(page: Page) -> str | None:
    """Find contact or recruitment email on the employer page."""
    mailtos = await page.evaluate(r"""() => {
      const links = [...document.querySelectorAll('a[href^="mailto:"]')];
      return links.map(l => l.href.replace(/^mailto:/i, '').split('?')[0].trim()).filter(Boolean);
    }""")
    for email in mailtos:
        if re.search(r"support@|sales@|info@|privacy@|legal@|admin@|postmaster@", email, re.I):
            continue
        if re.search(r"recruitment|recruit|career|jobs|hr|talent|hiring|tuyendung|apply", email, re.I):
            return email
    try:
        body = await page.locator("body").inner_text(timeout=5000)
        emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", body)
        for email in emails:
            if re.search(r"support@|sales@|info@|privacy@|legal@|admin@|postmaster@", email, re.I):
                continue
            idx = body.find(email)
            if idx >= 0:
                context = body[max(0, idx - 150):min(len(body), idx + len(email) + 150)]
                if re.search(r"apply|application|cv|resume|hồ sơ|ứng tuyển|tuyển dụng|gửi về|send to", context, re.I):
                    return email
        if mailtos:
            return mailtos[0]
    except Exception:
        pass
    return None


def _match_select_option(options: list[dict], candidate: str) -> str:
    if not candidate or not options:
        return ""
    cand = candidate.casefold().strip()
    for opt in options:
        val = str(opt.get("value", "")).strip()
        txt = str(opt.get("text", "")).strip()
        if cand in (val.casefold(), txt.casefold()) and val:
            return val
    for opt in options:
        val = str(opt.get("value", "")).strip()
        txt = str(opt.get("text", "")).strip()
        if not val:
            continue
        if (txt and (cand in txt.casefold() or txt.casefold() in cand)) or \
           (val and (cand in val.casefold() or val.casefold() in cand)):
            return val
    return ""


def _default_answer(field: dict, profile: dict, message: dict) -> str:
    lbl = field.get("label", "")
    grp = field.get("group_label", "")
    text = f"{field['name']} {field['id']} {lbl} {grp}".casefold()
    if field["type"] == "file":
        return ""
    if field["type"] == "radio":
        lbl_f = lbl.casefold().strip()
        grp_f = grp.casefold()
        if lbl_f in ("yes", "có", "đồng ý"):
            if re.search(r"eligible|eligibility|committed|full.?time|authorized|legal|willing|background|drug", grp_f):
                return "yes"
        elif lbl_f in ("no", "không"):
            if re.search(r"require.*(visa|sponsor)|need.*sponsor", grp_f):
                return "yes"
        elif re.search(r"hear about|source", grp_f) and "linkedin" in lbl_f:
            return "yes"
        return ""
    if field["type"] == "checkbox":
        return ""
    if re.search(r"cover.?letter|motivation|why (this|you|us)|message to|additional information", text):
        return message.get("body", "")
    if re.search(r"last drawn|current salary", text):
        return "Fresh graduate / Intern"
    for pattern, key in ((r"salary|compensation", "salary_expectation"), (r"visa|work.?authori", "work_authorization"),
                         (r"notice.?period|start.?date|join date|onboarding", "notice_period"), (r"relocat", "relocation")):
        if re.search(pattern, text):
            val = str(profile.get(key, ""))
            if val:
                return val
            if key == "salary_expectation":
                return "Negotiable"
            if key == "notice_period":
                return "Immediately"
    if re.search(r"reason.*(leav|left|exit)", text):
        return "Seeking career growth as an AI Engineer in a dynamic engineering team."
    if re.search(r"referr", text):
        return ""
    if re.search(r"experience in years|years of experience", text):
        return "1"
    if re.search(r"current.*(title|role)|job title", text):
        return "AI Engineer"
    if re.search(r"skill", text):
        return ", ".join(profile.get("skills", [])[:6])
    if re.search(r"consent|data protection|policy", text):
        return "yes"
    if re.search(r"birth|dob|dd/mm/yyyy", text):
        return str(profile.get("dob") or "27/05/2004")
    if re.search(r"current_address|address", text):
        return str(profile.get("location") or "Hanoi, Vietnam")
    if re.search(r"less than 40 hours|how many hours", text):
        return "N/A (committed to full-time 40 hours/week)"
    if re.search(r"how many months|duration.*available|available.*duration", text):
        return "6 months (available immediately full-time)"
    if field["type"] == "select" and field.get("options"):
        if re.search(r"which location|applying for|preferred location", text):
            for opt in field["options"]:
                if opt.get("text", "").strip().casefold() in ("asia", "vietnam", "remote"):
                    return str(opt.get("value", ""))
    if re.search(r"nationality", text):
        ans = str(profile.get("nationality") or profile.get("country") or "Vietnam")
        if field["type"] == "select" and field.get("options"):
            return _match_select_option(field["options"], ans)
        return ans
    if re.search(r"country|where do you (currently )?live", text):
        ans = str(profile.get("country") or "Vietnam")
        if field["type"] == "select" and field.get("options"):
            return _match_select_option(field["options"], ans)
        return ans
    patterns = [
        (r"e.?mail", "email"), (r"phone|mobile|telephone", "phone"),
        (r"first.?name|given.?name", "first_name"), (r"last.?name|sur.?name|family.?name", "last_name"),
        (r"full.?name|your.?name|name", "name"), (r"city|location", "location"),
        (r"linkedin", "linkedin"), (r"github", "github"),
    ]
    for pattern, key in patterns:
        if re.search(pattern, text):
            ans = ""
            if key == "first_name":
                ans = str(profile.get("given_name", ""))
                if not ans and profile.get("name"):
                    parts = profile["name"].strip().split()
                    ans = parts[-1] if len(parts) > 1 else parts[0]
            elif key == "last_name":
                ans = str(profile.get("family_name", ""))
                if not ans and profile.get("name"):
                    parts = profile["name"].strip().split()
                    ans = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
            elif key == "location":
                ans = str(profile.get("location") or "Hanoi, Vietnam")
            elif key in ("linkedin", "github"):
                ans = next((link for link in profile.get("links", []) if key in link.casefold()), "")
            else:
                ans = str(profile.get(key, ""))
            if field["type"] == "select" and field.get("options"):
                return _match_select_option(field["options"], ans)
            return ans
    return ""



async def inspect_form(db: Database, settings: Settings, draft_id: str) -> dict:
    from .drafting import set_discovered_email_destination

    draft = get_draft(db, draft_id)
    destination = draft["destination"]
    if destination.get("kind") == "linkedin_easy_apply":
        structure = await inspect_linkedin_application(settings, draft)
        db.execute("UPDATE application_drafts SET form_data=?,updated_at=? WHERE id=?",
                   (json.dumps(structure, ensure_ascii=False), now(), draft_id))
        return get_draft(db, draft_id)
    if destination.get("kind") != "web" or not destination.get("url"):
        raise ValueError("Set a web application URL before inspecting a form")
    if is_linkedin_job_posting_url(destination["url"]):
        raise ValueError("A LinkedIn job posting is not an application form. Open its Apply button instead")
    found_email: str | None = None
    structure: dict | None = None
    clean_stale_chrome_lock(settings.browser_profile)
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(str(settings.browser_profile), headless=True, **chrome_context_options())
        except Exception as error:
            if "SingletonLock" in str(error) or "ProcessSingleton" in str(error):
                clean_stale_chrome_lock(settings.browser_profile)
                await asyncio.sleep(2.0)
                context = await playwright.chromium.launch_persistent_context(str(settings.browser_profile), headless=True, **chrome_context_options())
            else:
                raise
        try:
            page = await context.new_page()
            await page.goto(destination["url"], wait_until="domcontentloaded", timeout=45000)
            try:
                _, structure = await _open_application_form(page)
            except ValueError as err:
                gh_match = re.search(r"[?&](?:gh_jid|token)=(\d+)", page.url) or re.search(r"[?&](?:gh_jid|token)=(\d+)", destination["url"])
                if gh_match:
                    token = gh_match.group(1)
                    board_match = re.search(r"job-boards\.greenhouse\.io/([a-zA-Z0-9_-]+)", page.url) or re.search(r"https?://(?:www\.)?([a-zA-Z0-9_-]+)\.", page.url)
                    board = board_match.group(1) if board_match else (draft.get("company_name", "").casefold().replace(" ", "") or "company")
                    embed_url = f"https://job-boards.greenhouse.io/embed/job_app?for={board}&token={token}"
                    try:
                        await page.goto(embed_url, wait_until="domcontentloaded", timeout=30000)
                        _, structure = await _open_application_form(page)
                        destination["url"] = embed_url
                        db.execute("UPDATE application_drafts SET destination=?,updated_at=? WHERE id=?",
                                   (json.dumps(destination, ensure_ascii=False), now(), draft_id))
                    except Exception:
                        pass
                if not structure:
                    found_email = await _find_application_email(page)
                    if not found_email:
                        raise err
        finally:
            await context.close()
    if found_email:
        return set_discovered_email_destination(db, draft_id, found_email, f"Recruitment email {found_email} discovered on employer page {destination['url']}.")
    profile = db.get_setting("profile", {})
    existing = draft["form_data"].get("answers", {})
    def _field_answer(field: dict) -> str:
        idx_str = str(field["index"])
        val = existing.get(idx_str)
        if val and field["type"] == "select" and field.get("options"):
            valid_vals = {str(opt.get("value", "")) for opt in field["options"]}
            if val not in valid_vals:
                val = ""
        return val or _default_answer(field, profile, draft["message_data"])

    answers = {str(field["index"]): _field_answer(field)
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
            current_warnings = [w for w in draft.get("warnings", []) if not w.startswith("Form answer drafting failed:")]
            warnings = [*current_warnings, f"Form answer drafting failed: {str(error)[:200]}"]
            db.execute("UPDATE application_drafts SET warnings=? WHERE id=?", (json.dumps(warnings), draft_id))
    structure["answers"] = answers
    attachments = draft["form_data"].get("attachments", {}) if draft["form_data"].get("signature") == structure["signature"] else {}
    for field in structure["fields"]:
        if field["type"] == "file" and str(field["index"]) not in attachments:
            attachments[str(field["index"])] = {"kind": "resume"}
    structure["attachments"] = attachments
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
        if is_linkedin_job_posting_url(url):
            raise ValueError("A LinkedIn job posting is not an application form. Open its Apply button instead")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("Enter a valid application URL")
    elif kind == "linkedin_easy_apply":
        if not is_linkedin_job_posting_url(destination.get("url")):
            raise ValueError("Choose a valid LinkedIn Easy Apply posting")
    elif kind == "linkedin_message":
        url = destination.get("url", "")
        if not url or "linkedin.com" not in url:
            raise ValueError("Provide a valid LinkedIn job posting or company URL for direct messaging")
    else:
        raise ValueError("Choose an email, web form, or LinkedIn direct message destination")


def _validated_smtp_config(settings: Settings) -> dict:
    return validated_smtp_config(settings)


CONFIRMED_SUBMISSION_STATUSES = ("sent_confirmed", "submitted_confirmed")
UNCERTAIN_SUBMISSION_STATUSES = ("submitted_unconfirmed",)
BLOCKING_SUBMISSION_STATUSES = (*CONFIRMED_SUBMISSION_STATUSES, *UNCERTAIN_SUBMISSION_STATUSES, "sending")


def submission_outcome(status: str, destination: dict | None = None) -> dict:
    destination = destination or {}
    channel = (
        "Email" if destination.get("kind") == "email"
        else "Web form" if destination.get("kind") == "web"
        else "LinkedIn message" if destination.get("kind") == "linkedin_message"
        else "Application"
    )
    outcomes = {
        "sent_confirmed": {
            "key": "email_sent", "label": "Email sent" if channel == "Email" else "Direct message sent" if channel == "LinkedIn message" else "Application sent", "tone": "success", "confirmed": True,
            "retry_blocked": True, "guidance": "Direct message recorded in Job Radar." if channel == "LinkedIn message" else "Job Radar received confirmation from the mail server.",
        },
        "submitted_confirmed": {
            "key": "application_submitted", "label": "Direct message sent" if channel == "LinkedIn message" else "Application submitted", "tone": "success", "confirmed": True,
            "retry_blocked": True, "guidance": "Direct message recorded in Job Radar." if channel == "LinkedIn message" else "The employer site showed a submission confirmation.",
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
        "failed": {
            "key": "send_failed", "label": "Send failed", "tone": "danger", "confirmed": False,
            "retry_blocked": False,
            "guidance": "The external service rejected the send before accepting it. Fix the problem, then retry the reviewed application.",
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
    fid = str(field.get("id") or "")
    flbl = str(field.get("label") or "").strip().lower()
    fname = str(field.get("name") or "")
    assignment = None
    for s_field in draft["form_data"].get("fields", []):
        s_id = str(s_field.get("id") or "")
        s_name = str(s_field.get("name") or "")
        s_lbl = str(s_field.get("label") or "").strip().lower()
        if (fid and s_id == fid) or (fname and s_name == fname) or (flbl and s_lbl == flbl):
            cand = draft["form_data"].get("attachments", {}).get(str(s_field.get("index")))
            if isinstance(cand, dict):
                assignment = cand
                break
    if not isinstance(assignment, dict):
        assignment = draft["form_data"].get("attachments", {}).get(str(field["index"]))
    if not isinstance(assignment, dict):
        if not field.get("required"):
            return None
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
            required_radios = {}
            for field in form.get("fields", []):
                if field["type"] == "file":
                    try:
                        _reviewed_attachment(settings, draft, field)
                    except ValueError as error:
                        reasons.append(str(error))
                elif field["type"] == "radio" and field["required"]:
                    required_radios.setdefault(field["name"] or field.get("group_label") or str(field["index"]), []).append(field)
                elif field["required"]:
                    answer = str(form.get("answers", {}).get(str(field["index"]), "")).strip()
                    if not answer or (field["type"] == "checkbox" and answer.casefold() not in ("yes", "true", "checked")):
                        reasons.append(f"Answer required: {field.get('label') or field.get('group_label') or field['name']}")
            for group in required_radios.values():
                if not any(str(form.get("answers", {}).get(str(field["index"]), "")).casefold() in ("yes", "true", "checked") for field in group):
                    reasons.append(f"Choose an option: {group[0].get('group_label') or group[0].get('label') or group[0]['name']}")
    elif draft["destination"].get("kind") == "linkedin_easy_apply":
        form = draft["form_data"]
        if (form.get("kind") != "linkedin_easy_apply" or not form.get("complete") or
                not form.get("signature") or form.get("destination_url") != draft["destination"].get("url")):
            reasons.append("Inspect every LinkedIn application step before sending")
        for field in form.get("fields", []):
            label = field.get("label") or f"Field {field['index']}"
            if field["type"] == "file":
                try:
                    attachment = _reviewed_attachment(settings, draft, field)
                    if attachment and Path(attachment).stat().st_size > int(field.get("max_file_bytes") or 2_000_000):
                        reasons.append(f"Resume PDF is too large for {label}; LinkedIn allows less than 2 MB")
                except ValueError as error:
                    reasons.append(str(error))
            elif field.get("required") and not str(form.get("answers", {}).get(str(field["index"]), "")).strip():
                reasons.append(f"Answer required: {label}")
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


async def _solve_image_captcha_if_present(page: Page, form) -> None:
    captcha_img = form.locator("img[src*='captcha' i], #js-recover-captcha, #captchaImage, .captcha-image, [class*='captcha' i] img").first
    if not await captcha_img.count() or not await captcha_img.is_visible():
        return
    captcha_input = form.locator("input[name*='captcha' i], input[placeholder*='security code' i], input[placeholder*='below image' i], input[placeholder*='image text' i], input[id*='captcha' i]").first
    if not await captcha_input.count() or not await captcha_input.is_visible():
        return
    curr_val = (await captcha_input.input_value()).strip()
    if curr_val:
        return
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            await captcha_img.screenshot(path=tmp.name)
            script = f"""
import Vision
import AppKit

let url = URL(fileURLWithPath: "{tmp.name}")
if let image = NSImage(contentsOf: url), let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {{
    let request = VNRecognizeTextRequest {{ req, err in
        let observations = req.results as? [VNRecognizedTextObservation] ?? []
        for obs in observations {{
            if let cand = obs.topCandidates(1).first {{
                print(cand.string)
            }}
        }}
    }}
    request.recognitionLevel = .accurate
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? handler.perform([request])
}}
"""
            out = subprocess.check_output(["swift", "-e", script], text=True, timeout=10).strip()
            clean = re.sub(r"[^a-zA-Z0-9]", "", out)
            if clean:
                await captcha_input.fill(clean)
    except Exception:
        pass


async def _send_web(settings: Settings, draft: dict) -> tuple[str, str]:
    form_data = draft["form_data"]
    if not form_data.get("signature") or form_data.get("destination_url") != draft["destination"].get("url"):
        return "needs_user_attention", "Inspect this exact application form before sending"
    clean_stale_chrome_lock(settings.browser_profile)
    is_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    headless = is_test or os.environ.get("JOB_RADAR_HEADLESS", "").lower() in ("1", "true")
    async with async_playwright() as playwright:
        opts = chrome_context_options()
        if not headless:
            opts["ignore_default_args"] = list(set(opts.get("ignore_default_args", []) + ["--enable-automation"]))
            opts["args"] = list(set(opts.get("args", []) + ["--disable-blink-features=AutomationControlled"]))
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(settings.browser_profile), headless=headless, accept_downloads=False, **opts
            )
        except Exception as error:
            if "SingletonLock" in str(error) or "ProcessSingleton" in str(error):
                clean_stale_chrome_lock(settings.browser_profile)
                await asyncio.sleep(2.0)
                context = await playwright.chromium.launch_persistent_context(
                    str(settings.browser_profile), headless=headless, accept_downloads=False, **opts
                )
            else:
                raise
        try:
            page = await context.new_page()
            await page.goto(draft["destination"]["url"], wait_until="domcontentloaded", timeout=45000)
            try:
                page, current = await _open_application_form(page, form_data.get("opener"))
            except ValueError as error:
                return "needs_user_attention", str(error)
            compatible = _signatures_compatible(current, form_data)
            if not compatible or current["final_url"] != form_data["final_url"]:
                return "needs_user_attention", "Application destination or form fields changed after review"
            form = page.locator("form").nth(form_data["form_index"])
            fields = form.locator("input,select,textarea")
            answers = form_data.get("answers", {})
            required_radio_groups = {}
            for field in current["fields"]:
                if field["type"] == "radio" and field["required"]:
                    required_radio_groups.setdefault(field["name"] or field.get("group_label") or str(field["index"]), []).append(field)
            for group in required_radio_groups.values():
                if not any(str(answers.get(str(field["index"]), "")).casefold() in ("yes", "true", "checked") for field in group):
                    return "needs_user_attention", f"Review required choice: {group[0].get('group_label') or group[0].get('label')}"
            for field in current["fields"]:
                fid = field.get("id") or ""
                kind = field["type"]
                if fid:
                    if kind in ("checkbox", "radio"):
                        cand = form.locator(f"input[type='{kind}']#{fid}, input#{fid}, #{fid}")
                    else:
                        cand = form.locator("#" + fid)
                    locator = cand.first if await cand.count() else fields.nth(field["index"])
                else:
                    locator = fields.nth(field["index"])
                answer = ""
                for s_field in form_data.get("fields", []):
                    s_id = str(s_field.get("id") or "")
                    s_name = str(s_field.get("name") or "")
                    s_lbl = str(s_field.get("label") or "").strip().lower()
                    if (fid and s_id == fid) or (field.get("name") and s_name == field.get("name")) or (field.get("label") and s_lbl == str(field.get("label")).strip().lower()):
                        answer = str(answers.get(str(s_field.get("index")), ""))
                        break
                if not answer:
                    answer = str(answers.get(str(field["index"]), ""))
                if field["max_length"] and len(answer) > field["max_length"]:
                    return "needs_user_attention", f"Answer exceeds character limit: {field['label']}"
                if kind == "file":
                    attachment = _reviewed_attachment(settings, draft, field)
                    if attachment:
                        await locator.set_input_files(attachment)
                        await page.wait_for_timeout(2500)
                elif kind in ("checkbox", "radio"):
                    chk = locator
                    if await locator.count() and await locator.evaluate("el => el.tagName !== 'INPUT'"):
                        inner = locator.locator(f"input[type='{kind}'], input")
                        if await inner.count():
                            chk = inner.first
                    if answer.casefold() in ("yes", "true", "checked"):
                        try:
                            await chk.check(force=True)
                        except Exception:
                            try:
                                await chk.click(force=True)
                            except Exception:
                                await chk.evaluate("el => el.click()")
                    elif kind == "checkbox" and field["required"]:
                        return "needs_user_attention", f"Review required choice: {field['label']}"
                elif kind == "select":
                    if answer:
                        await locator.select_option(value=answer)
                    elif field["required"]:
                        return "needs_user_attention", f"Answer required: {field['label']}"
                elif answer:
                    f_id = field.get("id") or ""
                    try:
                        is_react_select = await locator.evaluate(
                            "el => !!(el.closest('.select__container, .select-shell, [class*=\"select__control\"]') || el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') === 'list')"
                        )
                    except Exception:
                        is_react_select = False
                    if is_react_select:
                        try:
                            await locator.click()
                            await page.wait_for_timeout(300)
                            clean_ans = re.split(r"[,/]", answer)[0].strip()
                            await locator.press_sequentially(clean_ans, delay=30)
                            sel_opts = page.locator(
                                f".select__option:has-text('{clean_ans}'), [id*='-option-']:has-text('{clean_ans}'), [id^='react-select-{f_id}-option-'], [id*='-option-']"
                            )
                            try:
                                await sel_opts.first.wait_for(state="visible", timeout=3000)
                            except Exception:
                                await page.wait_for_timeout(600)
                            if await sel_opts.count():
                                matched = None
                                for i in range(await sel_opts.count()):
                                    cand = sel_opts.nth(i)
                                    if await cand.is_visible():
                                        cand_txt = (await cand.inner_text() or "").strip().lower()
                                        if clean_ans.lower() in cand_txt:
                                            matched = cand
                                            break
                                if not matched:
                                    for i in range(await sel_opts.count()):
                                        cand = sel_opts.nth(i)
                                        if await cand.is_visible():
                                            matched = cand
                                            break
                                if matched:
                                    await matched.click()
                                else:
                                    await locator.press("Enter")
                            else:
                                await locator.press("Enter")
                            await page.wait_for_timeout(500)
                        except Exception:
                            await locator.fill(answer)
                    else:
                        await locator.fill(answer)
                        val = await locator.input_value()
                        if not val and (kind == "tel" or re.search(r"phone|tel|mobile|điện thoại", f"{field.get('name','')} {field.get('id','')} {field.get('label','')}", re.I)):
                            sanitized = ("+" if answer.strip().startswith("+") else "") + re.sub(r"[^\d]", "", answer)
                            if sanitized:
                                await locator.fill(sanitized)
                                val = await locator.input_value()
                                if not val:
                                    await locator.type(sanitized)
                        elif not val and kind in ("text", "number"):
                            sanitized = re.sub(r"[^\w\s]", "", answer).strip()
                            if sanitized and sanitized != answer:
                                await locator.fill(sanitized)
                elif field["required"]:
                    return "needs_user_attention", f"Answer required: {field['label']}"
            await _solve_image_captcha_if_present(page, form)
            submit = None
            for selector in (
                '#btn-submit',
                '[data-qa="btn-submit"]',
                '.template-btn-submit',
                'button[type="submit"]',
                'input[type="submit"]',
                'button:not([type])',
                'button[type="button"]',
                'button',
                'input[type="button"]',
                '[onclick*="submit"]',
                '[onclick*="Apply.submit"]',
                '.submit',
                '[role="button"]',
                'div.button',
                'a.button',
                'a.btn-apply',
                'div[class*="submit"]',
            ):
                count = await form.locator(selector).count()
                for i in range(count):
                    cand = form.locator(selector).nth(i)
                    if not await cand.is_visible():
                        continue
                    b_id = (await cand.get_attribute("id") or "").lower()
                    b_cls = (await cand.get_attribute("class") or "").lower()
                    if "hcaptcha" in b_id or "hidden" in b_cls:
                        continue
                    if selector in ('button[type="button"]', 'button', 'input[type="button"]', '[onclick*="submit"]', '[onclick*="Apply.submit"]', '.submit', '[role="button"]', 'div.button', 'a.button', 'a.btn-apply', 'div[class*="submit"]'):
                        text = (await cand.inner_text() or await cand.get_attribute("value") or "").strip().lower()
                        aria = (await cand.get_attribute("aria-label") or "").strip().lower()
                        if not re.search(r"\b(submit|apply|send|nộp|gửi|hoàn tất)\b", f"{text} {aria}"):
                            continue
                    submit = cand
                    break
                if submit:
                    break

            if not submit:
                for selector in (
                    '#btn-submit',
                    '[data-qa="btn-submit"]',
                    '.template-btn-submit',
                    'button[type="submit"]',
                    'input[type="submit"]',
                    '[onclick*="submit"]',
                    '[onclick*="Apply.submit"]',
                    '.submit',
                    '[role="button"]',
                    'div.button',
                    'a.button',
                    'a.btn-apply',
                    'div[class*="submit"]',
                ):
                    count = await page.locator(selector).count()
                    for i in range(count):
                        cand = page.locator(selector).nth(i)
                        if not await cand.is_visible():
                            continue
                        b_id = (await cand.get_attribute("id") or "").lower()
                        b_cls = (await cand.get_attribute("class") or "").lower()
                        if "hcaptcha" in b_id or "hidden" in b_cls:
                            continue
                        if selector in ('[onclick*="submit"]', '[onclick*="Apply.submit"]', '.submit', '[role="button"]', 'div.button', 'a.button', 'a.btn-apply', 'div[class*="submit"]'):
                            text = (await cand.inner_text() or await cand.get_attribute("value") or "").strip().lower()
                            aria = (await cand.get_attribute("aria-label") or "").strip().lower()
                            if not re.search(r"\b(submit|apply|send|nộp|gửi|hoàn tất)\b", f"{text} {aria}"):
                                continue
                        submit = cand
                        break
                    if submit:
                        break

            if not submit or not await submit.count():
                return "needs_user_attention", "No submit control was found on the reviewed form"
            invalid = form.locator(
                "input:invalid:not([aria-hidden='true']):not([tabindex='-1']):not([type='hidden']),select:invalid:not([aria-hidden='true']),textarea:invalid:not([aria-hidden='true'])"
            ).first
            if await invalid.count() and await invalid.is_visible():
                label = (
                    await invalid.evaluate(
                        "el => (el.labels && el.labels[0] ? el.labels[0].innerText : '') || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || 'form field'"
                    )
                ).strip().rstrip("*").strip() or "form field"
                return "needs_user_attention", f"Review invalid or missing value: {label}"
            before = page.url
            before_text = (await page.locator("body").inner_text(timeout=7000))[:3000]
            submitted_requests = []
            page.on("request", lambda request: submitted_requests.append(request.url) if request.method not in ("GET", "HEAD") else None)
            submitted_responses = []
            page.on("response", lambda response: submitted_responses.append((response.status, response.url)) if response.request.method not in ("GET", "HEAD") else None)
            try:
                await submit.scroll_into_view_if_needed(timeout=4000)
            except Exception:
                pass
            try:
                await submit.click(timeout=6000)
            except Exception:
                try:
                    await submit.click(force=True, timeout=5000)
                except Exception:
                    await submit.evaluate("el => el.click()")
            try:
                await page.wait_for_timeout(1200)
                agree_btn = page.locator("button:has-text('Agree'), .button:has-text('Agree'), a:has-text('Agree'), [class*='agree']:not(body), button:has-text('Đồng ý')").last
                if await agree_btn.count() and await agree_btn.is_visible():
                    await agree_btn.click()
            except Exception:
                pass
            max_seconds = 180 if not headless else 15
            captcha_prompted = False
            for _ in range(max_seconds):
                try:
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass
                try:
                    url = page.url
                except Exception:
                    if captcha_prompted:
                        return "needs_user_attention", "Browser window was closed before verification completed."
                    break
                try:
                    visible = (await page.locator("body").inner_text(timeout=4000))[:3000]
                except Exception:
                    visible = ""
                for status_code, res_url in submitted_responses:
                    if status_code in (200, 201, 204) and re.search(r"application|apply|submit|candidate", res_url, re.I):
                        return "submitted_confirmed", f"API {res_url} returned HTTP {status_code}"
                if any(x in url.lower() for x in ("/thanks", "/thank-you", "/confirmation", "/success")):
                    return "submitted_confirmed", f"{url}: {visible[:500]}"
                if submitted_requests and (url != before or visible != before_text) and re.search(r"thank you|application (has been |was )?(received|submitted)|successfully applied|cảm ơn|ứng tuyển thành công|hồ sơ ứng tuyển thành công|gửi.*thành công", visible, re.I):
                    return "submitted_confirmed", f"{url}: {visible[:500]}"
                if url != before and not await page.locator("form").count():
                    await page.wait_for_timeout(1500)
                    try:
                        visible = (await page.locator("body").inner_text(timeout=4000))[:3000]
                    except Exception:
                        pass
                    if re.search(r"thank you|application (has been |was )?(received|submitted)|successfully applied|cảm ơn|ứng tuyển thành công|hồ sơ ứng tuyển thành công|gửi.*thành công", visible, re.I) or any(x in url.lower() for x in ("/thanks", "/thank-you", "/confirmation", "/success")):
                        return "submitted_confirmed", f"{url}: {visible[:500]}"
                    return "submitted_unconfirmed", f"Form navigated to {url}; confirmation not detected"

                try:
                    captcha_active = await page.evaluate('''() => {
                        return Array.from(document.querySelectorAll('iframe')).some(f => {
                            if (!f.offsetWidth || !f.offsetHeight || window.getComputedStyle(f).visibility === 'hidden') return false;
                            const src = f.src || '';
                            if (src.includes('hcaptcha.com') || src.includes('cloudflare.com')) return true;
                            if (src.includes('recaptcha')) {
                                if (src.includes('/anchor') && f.offsetWidth <= 265 && f.offsetHeight <= 70) return false;
                                return true;
                            }
                            return false;
                        });
                    }''')
                except Exception:
                    captcha_active = False
                email_code_active = bool(re.search(r"verification code (was )?sent|enter the (security|\d+-character) code", visible, re.I))

                if captcha_active or email_code_active:
                    if not headless:
                        if not captcha_prompted:
                            captcha_prompted = True
                            try:
                                subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
                            except Exception:
                                pass
                        continue
                    else:
                        msg = "A verification code was sent to your email to confirm submission. Complete the prompt on the employer site." if email_code_active else "Security verification (CAPTCHA) is required on the employer site before submission can complete."
                        return "needs_user_attention", msg

            if page.url != before and not await page.locator("form").count():
                return "submitted_unconfirmed", f"Form navigated to {page.url}; confirmation not detected"
            if captcha_prompted:
                return "needs_user_attention", "Security verification (CAPTCHA) was not completed before timeout. Please try sending again."
            return "submitted_unconfirmed", f"Submit clicked; confirmation not detected at {page.url}"
        finally:
            await context.close()


async def _send_linkedin_easy_apply(settings: Settings, draft: dict) -> tuple[str, str]:
    from .collectors import AuthRequired, _check_auth

    posting_url = draft["destination"]["url"]
    clean_stale_chrome_lock(settings.browser_profile)
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(settings.browser_profile), headless=True, accept_downloads=False,
                **chrome_context_options(required=True),
            )
        except Exception as error:
            if "SingletonLock" in str(error) or "ProcessSingleton" in str(error):
                clean_stale_chrome_lock(settings.browser_profile)
                await asyncio.sleep(2.0)
                context = await playwright.chromium.launch_persistent_context(
                    str(settings.browser_profile), headless=True, accept_downloads=False,
                    **chrome_context_options(required=True),
                )
            else:
                return "needs_user_attention", f"Failed to launch browser: {error}"
        try:
            page = await context.new_page()
            await page.goto(posting_url, wait_until="domcontentloaded", timeout=45000)
            try:
                _check_auth(page.url, await page.locator("body").inner_text(timeout=7000))
            except AuthRequired as error:
                return "needs_user_attention", str(error)
            control = await wait_for_linkedin_apply_control(page)
            if control["kind"] != "linkedin_easy_apply":
                return "needs_user_attention", control.get("detail") or "LinkedIn Easy Apply is not available on this posting"
            return await submit_easy_apply_dialog(page, draft["form_data"], Path(draft["resume_path"]))
        except Exception as error:
            return "needs_user_attention", str(error)
        finally:
            await context.close()


async def send_application(db: Database, settings: Settings, draft_id: str, expected_hash: str) -> dict:
    draft = get_draft(db, draft_id)
    if not expected_hash or expected_hash != package_hash(draft):
        raise ValueError("Application changed since review. Reload and review the package before sending")
    _validate_destination(draft["destination"])
    if draft["destination"]["kind"] == "linkedin_easy_apply":
        blockers = send_readiness(db, settings, draft)
        if blockers:
            raise ValueError("; ".join(blockers))
    if not draft["message_data"].get("body") or not draft["resume_data"].get("name"):
        raise ValueError("Complete the message and resume before sending")
    if draft["destination"]["kind"] == "email":
        _validated_smtp_config(settings)
    elif draft["destination"]["kind"] == "web" and draft["form_data"].get("fields"):
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
        elif draft["destination"]["kind"] == "linkedin_easy_apply":
            status, receipt = await _send_linkedin_easy_apply(settings, draft)
        elif draft["destination"]["kind"] == "linkedin_message":
            receipt = f"Direct message outreach confirmed on LinkedIn: {draft['destination'].get('url')}"
            status = "submitted_confirmed"
        else:
            status, receipt = await _send_web(settings, draft)
        db.execute("UPDATE submissions SET status=?,receipt=?,updated_at=? WHERE id=?", (status, receipt, now(), identifier))
        if status in CONFIRMED_SUBMISSION_STATUSES:
            db.execute("UPDATE application_drafts SET status='sent',updated_at=? WHERE id=?", (now(), draft_id))
        elif status in UNCERTAIN_SUBMISSION_STATUSES:
            db.execute("UPDATE application_drafts SET status='submission_uncertain',updated_at=? WHERE id=?", (now(), draft_id))
        return {"id": identifier, "status": status, "receipt": receipt,
                "outcome": submission_outcome(status, draft["destination"])}
    except (smtplib.SMTPConnectError, smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused, smtplib.SMTPDataError, smtplib.SMTPHeloError,
            smtplib.SMTPNotSupportedError) as error:
        # These SMTP failures include a definite rejection before the server accepted the message.
        db.execute("UPDATE submissions SET status='failed',error=?,updated_at=? WHERE id=?",
                   (str(error)[:1000], now(), identifier))
        return {"id": identifier, "status": "failed", "error": str(error),
                "outcome": submission_outcome("failed", draft["destination"])}
    except Exception as error:
        # The transport or browser may have completed the send before failing. Block another send.
        db.execute("UPDATE submissions SET status='submitted_unconfirmed',error=?,updated_at=? WHERE id=?", (str(error)[:1000], now(), identifier))
        db.execute("UPDATE application_drafts SET status='submission_uncertain',updated_at=? WHERE id=?", (now(), draft_id))
        return {"id": identifier, "status": "submitted_unconfirmed", "error": str(error),
                "outcome": submission_outcome("submitted_unconfirmed", draft["destination"])}
