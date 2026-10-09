"""Inspect the Apply control on a saved LinkedIn posting without submitting it."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from .application_action import is_linkedin_job_posting_url
from .settings import Settings
from .social_browser import chrome_context_options


_EASY_APPLY = re.compile(r"easy\s+apply|ứng\s+tuyển\s+dễ\s+dàng", re.I)
_FINAL_SUBMIT = re.compile(r"submit application|send application|nộp đơn ứng tuyển", re.I)


async def _easy_apply_step(page: Page):
    """Return the visible Easy Apply step, including controls inside shadow DOM."""
    for name in ("Next", "Review", _FINAL_SUBMIT):
        button = page.get_by_role("button", name=name, exact=isinstance(name, str)).filter(
            has_text=name if isinstance(name, str) else _FINAL_SUBMIT).last
        try:
            if not await button.is_visible():
                continue
            for depth in range(1, 11):
                root = button.locator("xpath=" + "/".join(".." for _ in range(depth)))
                text = await root.inner_text(timeout=1500)
                match = re.search(r"(\d+)\s*/\s*(\d+)\s+pages?", text, re.I)
                if match:
                    return root, button, ("submit" if not isinstance(name, str) else name.casefold()), int(match.group(1)), int(match.group(2))
        except Exception:
            continue
    submit = page.get_by_role("button", name=_FINAL_SUBMIT).filter(has_text=_FINAL_SUBMIT).last
    if await submit.is_visible():
        for dialog in await page.get_by_role("dialog").all():
            if await dialog.is_visible() and await dialog.get_by_role("button", name=_FINAL_SUBMIT).count():
                return dialog, submit, "submit", 1, 1
    raise ValueError("LinkedIn Easy Apply did not show a recognizable application step")


async def _wait_for_easy_apply_step(page: Page) -> None:
    for _ in range(40):
        try:
            await _easy_apply_step(page)
            return
        except ValueError:
            await page.wait_for_timeout(250)
    raise ValueError("LinkedIn Easy Apply did not open a recognizable application form")


async def _step_fields(root, step: int, start_index: int) -> list[dict]:
    data = await root.evaluate("""node => {
      const visible = el => {const box=el.getBoundingClientRect();return box.width>0 && box.height>0};
      const resume = /\\bresume\\b/i.test(node.innerText) &&
        [...node.querySelectorAll('button')].some(button => /upload resume/i.test(button.innerText));
      const controls = [...node.querySelectorAll('input,select,textarea')];
      const fields = controls.map((el, position) => {
        const type = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text').toLowerCase();
        if (!visible(el) || ['hidden','submit','button','reset','image','password'].includes(type) || (resume && type === 'radio')) return null;
        const labelNode = el.labels?.[0] || el.closest('label');
        const cleanLabel = labelNode?.cloneNode(true);
        cleanLabel?.querySelectorAll('input,select,textarea,button').forEach(control => control.remove());
        const label = (cleanLabel?.textContent || el.getAttribute('aria-label') ||
          el.getAttribute('placeholder') || '').trim().replace(/\\s*\\*\\s*$/, '');
        return {position, type, label:label.slice(0,250), name:el.name || '', required:!!el.required,
          options:type === 'select' ? [...el.options].map(option => ({value:option.value,text:option.text.trim()})) : [],
          value:type === 'checkbox' || type === 'radio' ? (el.checked ? 'yes' : '') : el.value || '',
          max_length:el.maxLength > 0 ? el.maxLength : null};
      }).filter(Boolean);
      if (resume) fields.push({position:-1,type:'file',label:'Resume',name:'resume',required:true,
        options:[],value:'',accept:'.pdf,application/pdf',max_file_bytes:2000000,max_length:null});
      return fields;
    }""")
    return [{**field, "step": step, "index": start_index + offset} for offset, field in enumerate(data)]


def _easy_signature(fields: list[dict], posting_url: str, total_steps: int) -> str:
    stable = [{key: field.get(key) for key in ("step", "position", "type", "label", "required", "options", "accept", "max_file_bytes")}
              for field in fields]
    return hashlib.sha256(json.dumps({"url": posting_url, "steps": total_steps, "fields": stable},
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refresh_saved_answer_blockers(form: dict) -> dict:
    """Replace stale missing-answer messages after the user edits an inspected step."""
    if form.get("kind") != "linkedin_easy_apply":
        return form
    answers = form.get("answers") or {}
    missing = [f"Answer required: {field.get('label') or f'Field {field.get('index')}'}"
               for field in form.get("fields", [])
               if field.get("required") and field.get("type") != "file"
               and not str(answers.get(str(field.get("index"))) or "").strip()]
    other = [str(blocker) for blocker in form.get("inspection_blockers", [])
             if not str(blocker).startswith("Answer required:")]
    return {**form, "inspection_blockers": [*missing, *other]}


async def _fill_step(page: Page, root, fields: list[dict], answers: dict[str, str], resume_path: Path) -> list[str]:
    missing: list[str] = []
    controls = root.locator("input,select,textarea")
    for field in fields:
        label = field["label"] or f"Field {field['index']}"
        if field["type"] == "file":
            if not resume_path.is_file() or resume_path.stat().st_size > field["max_file_bytes"]:
                missing.append(f"Choose a resume PDF smaller than 2 MB for {label}")
                continue
            upload = root.get_by_role("button", name=re.compile("Upload resume", re.I)).first
            try:
                async with page.expect_file_chooser(timeout=6000) as chooser:
                    await upload.click(timeout=6000)
                await (await chooser.value).set_files(str(resume_path))
            except Exception:
                missing.append(f"Could not attach the reviewed resume for {label}")
            continue
        answer = str(answers.get(str(field["index"]), ""))
        if field["required"] and not answer.strip():
            missing.append(f"Answer required: {label}")
            continue
        if not answer:
            continue
        locator = controls.nth(field["position"])
        try:
            if field["type"] == "select":
                await locator.select_option(value=answer)
            elif field["type"] in ("checkbox", "radio"):
                if answer.casefold() in ("yes", "true", "checked", "1"):
                    await locator.check()
            else:
                if field.get("max_length") and len(answer) > field["max_length"]:
                    missing.append(f"Answer is too long: {label}")
                    continue
                await locator.fill(answer)
        except Exception:
            missing.append(f"Could not fill: {label}")
    return missing


async def _advance(page: Page, button, step: int) -> bool:
    await button.click(timeout=10000)
    for _ in range(30):
        await page.wait_for_timeout(150)
        try:
            _, _, _, current, _ = await _easy_apply_step(page)
            if current > step:
                return True
        except ValueError:
            pass
    return False


async def inspect_easy_apply_dialog(page: Page, posting_url: str, existing_answers: dict[str, str],
                                    resume_path: Path) -> dict:
    """Inspect and prefill every reachable step, stopping before submission."""
    await page.get_by_role("button", name=_EASY_APPLY).first.click(timeout=12000)
    await _wait_for_easy_apply_step(page)
    fields: list[dict] = []
    answers: dict[str, str] = {}
    attachments: dict[str, dict] = {}
    total_steps = 0
    complete = False
    blockers: list[str] = []
    for _ in range(12):
        root, button, action, step, total_steps = await _easy_apply_step(page)
        current = await _step_fields(root, step, len(fields))
        fields.extend(current)
        for field in current:
            key = str(field["index"])
            if field["type"] == "file":
                attachments[key] = {"kind": "resume"}
            else:
                answers[key] = str(existing_answers.get(key, field.get("value") or ""))
        blockers = await _fill_step(page, root, current, answers, resume_path)
        if blockers:
            break
        if action == "submit":
            complete = True
            break
        if not await _advance(page, button, step):
            blockers = ["LinkedIn did not advance to the next application page. Review this step manually."]
            break
    return {"kind": "linkedin_easy_apply", "fields": fields, "answers": answers,
            "attachments": attachments, "complete": complete, "inspection_blockers": blockers,
            "signature": _easy_signature(fields, posting_url, total_steps),
            "destination_url": posting_url, "final_url": posting_url, "steps_total": total_steps}


async def submit_easy_apply_dialog(page: Page, reviewed: dict, resume_path: Path) -> tuple[str, str]:
    """Fill the reviewed LinkedIn steps and submit only if every step still matches."""
    await page.get_by_role("button", name=_EASY_APPLY).first.click(timeout=12000)
    await _wait_for_easy_apply_step(page)
    seen: list[dict] = []
    expected = reviewed.get("fields") or []
    posting_url = reviewed.get("destination_url") or page.url
    for _ in range(12):
        root, button, action, step, total_steps = await _easy_apply_step(page)
        current = await _step_fields(root, step, len(seen))
        checked = seen + current
        if _easy_signature(checked, posting_url, total_steps) != _easy_signature(expected[:len(checked)], posting_url, total_steps):
            return "needs_user_attention", "LinkedIn form changed after review; inspect it again"
        seen = checked
        blockers = await _fill_step(page, root, current, reviewed.get("answers") or {}, resume_path)
        if blockers:
            return "needs_user_attention", "; ".join(blockers)
        if action == "submit":
            if not reviewed.get("complete") or _easy_signature(seen, posting_url, total_steps) != reviewed.get("signature"):
                return "needs_user_attention", "LinkedIn form changed after review; inspect it again"
            await button.click(timeout=15000)
            try:
                await page.get_by_text(re.compile(r"application sent|application submitted|you applied|ứng tuyển thành công", re.I)).first.wait_for(timeout=8000)
                return "submitted_confirmed", f"LinkedIn confirmed application at {page.url}"
            except Exception:
                return "submitted_unconfirmed", f"LinkedIn submit was clicked; confirmation was not detected at {page.url}"
        if not await _advance(page, button, step):
            return "needs_user_attention", "LinkedIn did not advance after filling the reviewed answers"
    return "needs_user_attention", "LinkedIn application had more steps than were reviewed"


async def inspect_linkedin_application(settings: Settings, draft: dict) -> dict:
    """Open the user's saved LinkedIn session and inspect this draft's Easy Apply flow."""
    from .collectors import _check_auth

    posting_url = draft["destination"].get("url")
    if not is_linkedin_job_posting_url(posting_url):
        raise ValueError("This draft has no LinkedIn Easy Apply posting")
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(settings.browser_profile), headless=True, **chrome_context_options(required=True),
        )
        try:
            page = await context.new_page()
            await page.goto(posting_url, wait_until="domcontentloaded", timeout=45000)
            _check_auth(page.url, await page.locator("body").inner_text(timeout=7000))
            control = await wait_for_linkedin_apply_control(page)
            if control["kind"] != "linkedin_easy_apply":
                raise ValueError(control.get("detail") or "This posting no longer offers LinkedIn Easy Apply")
            return await inspect_easy_apply_dialog(
                page, posting_url, draft["form_data"].get("answers", {}), Path(draft["resume_path"]),
            )
        finally:
            await context.close()


def _external_application_url(value: str | None) -> bool:
    parts = urlsplit(value or "")
    host = (parts.hostname or "").casefold()
    return parts.scheme in {"http", "https"} and bool(host) and host != "linkedin.com" and not host.endswith(".linkedin.com")


async def find_linkedin_apply_control(page: Page) -> dict:
    """Identify the selected posting's Apply action; never click Easy Apply here."""
    return await page.evaluate(r"""() => {
      const root = document.querySelector('main') || document;
      const headings = [...root.querySelectorAll('h1,h2,h3,[role=heading]')];
      const about = headings.find(node => node.textContent.trim() === 'About the job');
      if (!about) return {kind:'unknown', detail:'The selected job details have not loaded yet.'};
      const selectedText = root.innerText.split('About the job')[0];
      const closedRegex = /(?:no longer|not currently)\s+accepting\s+applications|không còn nhận đơn|hiện không nhận đơn/i;
      if (closedRegex.test(selectedText) || closedRegex.test(root.innerText))
        return {kind:'closed', detail:'This LinkedIn posting is no longer accepting applications.'};
      if (/applied on company site|you applied|application submitted/i.test(selectedText))
        return {kind:'already_applied', detail:'LinkedIn shows this posting as already applied. Verify its status before another send.'};
      const visible = node => {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const candidates = [...root.querySelectorAll('a[href],button')]
        .filter(node => visible(node) && !!(node.compareDocumentPosition(about) & Node.DOCUMENT_POSITION_FOLLOWING)).map(node => {
        const label = `${node.innerText || ''} ${node.getAttribute('aria-label') || ''}`.trim().replace(/\s+/g, ' ');
        const easy = /easy\s+apply|ứng\s+tuyển\s+dễ\s+dàng/i.test(label);
        const apply = /\bapply\b|ứng\s+tuyển/i.test(label);
        const href = node.tagName === 'A' ? node.href : '';
        return {node, label, easy, apply, href};
      }).filter(item => item.easy || item.apply);
      const selected = candidates.find(item => item.easy) ||
        candidates.find(item => item.href && !/linkedin\.com/i.test(new URL(item.href).hostname)) ||
        candidates.find(item => /^apply\b|^ứng\s+tuyển/i.test(item.label)) || candidates[0];
      if (!selected) return {kind:'unknown', detail:'No Apply control was visible on this posting.'};
      if (selected.easy) return {kind:'linkedin_easy_apply', detail:'LinkedIn Easy Apply opens a multi-step popup.'};
      selected.node.setAttribute('data-job-radar-apply-control', 'true');
      return {kind:'apply', url:selected.href || '', detail:selected.label};
    }""")


async def wait_for_linkedin_apply_control(page: Page) -> dict:
    """Wait for LinkedIn's client-rendered job card before calling it absent."""
    control = {"kind": "unknown", "detail": "No application button appeared on this posting."}
    for _ in range(32):
        control = await find_linkedin_apply_control(page)
        if control["kind"] != "unknown":
            return control
        await page.wait_for_timeout(250)
    return control


async def linkedin_sign_in_dialog_visible(page: Page) -> bool:
    dialog = page.locator(".modal__overlay--visible").first
    if not await dialog.is_visible():
        return False
    text = (await dialog.inner_text()).casefold()
    return "sign in" in text or "đăng nhập" in text or "join now" in text


async def discover_linkedin_apply(settings: Settings, posting_url: str) -> dict:
    from .collectors import _check_auth

    if not is_linkedin_job_posting_url(posting_url):
        raise ValueError("This draft does not have a LinkedIn job posting to check")
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(settings.browser_profile), headless=True, **chrome_context_options(required=True),
            )
        except Exception as error:
            if "SingletonLock" in str(error) or "ProcessSingleton" in str(error):
                await asyncio.sleep(2.0)
                context = await playwright.chromium.launch_persistent_context(
                    str(settings.browser_profile), headless=True, **chrome_context_options(required=True),
                )
            else:
                raise
        try:
            page = await context.new_page()
            await page.goto(posting_url, wait_until="domcontentloaded", timeout=45000)
            _check_auth(page.url, await page.locator("body").inner_text(timeout=7000))
            if "/login" in page.url or "/checkpoint" in page.url:
                return {"kind": "sign_in_required", "detail": "Sign in to LinkedIn in Job Radar's browser, then try again."}
            if await linkedin_sign_in_dialog_visible(page):
                return {"kind": "sign_in_required", "detail": "LinkedIn is covering Apply with a sign-in dialog. Sign in to LinkedIn in Job Radar's browser, then try again."}
            control = await wait_for_linkedin_apply_control(page)
            _check_auth(page.url, await page.locator("body").inner_text(timeout=7000))
            if control["kind"] != "apply":
                return control
            if _external_application_url(control.get("url")):
                return {"kind": "web", "url": control["url"], "detail": "External Apply link found on LinkedIn."}
            opened: list[Page] = []
            context.on("page", lambda new_page: opened.append(new_page))
            try:
                await page.locator('[data-job-radar-apply-control="true"]').click(timeout=10000)
            except PlaywrightTimeoutError:
                if await linkedin_sign_in_dialog_visible(page):
                    return {"kind": "sign_in_required", "detail": "LinkedIn is covering Apply with a sign-in dialog. Sign in to LinkedIn in Job Radar's browser, then try again."}
                return {"kind": "unknown", "detail": "LinkedIn's Apply control could not be opened. Open the posting to continue manually."}
            await page.wait_for_timeout(900)
            target = opened[-1] if opened else page
            try:
                await target.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            if _external_application_url(target.url):
                return {"kind": "web", "url": target.url, "detail": "External Apply destination opened from LinkedIn."}
            return {"kind": "unknown", "detail": "Apply did not reveal a supported external application page. Open the posting to continue manually."}
        finally:
            await context.close()
