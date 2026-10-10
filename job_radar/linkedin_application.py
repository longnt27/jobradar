"""Inspect the Apply control on a saved LinkedIn posting without submitting it."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from .application_action import is_linkedin_job_posting_url
from .settings import Settings
from .social_browser import chrome_context_options, clean_stale_chrome_lock


_EASY_APPLY = re.compile(r"easy\s+apply|ứng\s+tuyển\s+dễ\s+dàng", re.I)
_FINAL_SUBMIT = re.compile(r"submit(?:\s+application)?|send application|nộp đơn(?:\s+ứng tuyển)?", re.I)
_REVIEW_BUTTON = re.compile(r"review(?:\s+application)?|xem lại(?:\s+đơn)?", re.I)
_NEXT_BUTTON = re.compile(r"next|tiếp(?: theo)?", re.I)


async def _easy_apply_step(page: Page):
    """Return the visible Easy Apply step, including controls inside shadow DOM."""
    candidates = (
        (_FINAL_SUBMIT, "submit"),
        (_REVIEW_BUTTON, "review"),
        (_NEXT_BUTTON, "next"),
    )
    for pattern, default_action in candidates:
        button = page.get_by_role("button", name=pattern).filter(has_text=pattern).last
        try:
            if not await button.is_visible():
                continue
            for depth in range(1, 12):
                root = button.locator("xpath=" + "/".join(".." for _ in range(depth)))
                text = await root.inner_text(timeout=1500)
                match = re.search(r"(\d+)\s*(?:/|of|trên)\s*(\d+)", text, re.I)
                if match:
                    cur = int(match.group(1))
                    tot = int(match.group(2))
                    action = "submit" if default_action == "submit" else default_action
                    return root, button, action, cur, tot
        except Exception:
            continue
    for pattern, default_action in candidates:
        button = page.get_by_role("button", name=pattern).filter(has_text=pattern).last
        try:
            if await button.is_visible():
                dialogs = page.locator("dialog[open], [data-testid='dialog'], .jobs-easy-apply-modal, [role='dialog']")
                for idx in range(await dialogs.count()):
                    dialog = dialogs.nth(idx)
                    if await dialog.is_visible() and await dialog.get_by_role("button", name=pattern).count() > 0:
                        text = await dialog.inner_text(timeout=1500)
                        match = re.search(r"(\d+)\s*(?:/|of|trên)\s*(\d+)", text, re.I)
                        cur, tot = (int(match.group(1)), int(match.group(2))) if match else (1, 1)
                        action = "submit" if default_action == "submit" else default_action
                        return dialog, button, action, cur, tot
        except Exception:
            continue
    raise ValueError("LinkedIn Easy Apply did not show a recognizable application step")


async def _wait_for_easy_apply_step(page: Page) -> None:
    for _ in range(40):
        try:
            await _easy_apply_step(page)
            return
        except ValueError:
            await page.wait_for_timeout(250)
    raise ValueError("LinkedIn Easy Apply did not open a recognizable application form")


def _infer_default_radio_answer(label: str, options: list[dict]) -> str:
    text = (label or "").casefold()
    opt_texts = [str(o.get("text", "")).casefold() for o in options]
    has_yes = any(t in ("yes", "có", "đồng ý") for t in opt_texts)
    has_no = any(t in ("no", "không") for t in opt_texts)

    if re.search(r"require.*sponsorship|cần.*bảo lãnh", text):
        if has_no:
            for o in options:
                if str(o.get("text", "")).casefold() in ("no", "không"):
                    return str(o.get("value") or o.get("text"))
            return "No"

    if re.search(r"willing|authorized|legally|drug test|background check|sẵn sàng|cho phép|đồng ý|tuân thủ", text):
        if has_yes:
            for o in options:
                if str(o.get("text", "")).casefold() in ("yes", "có", "đồng ý"):
                    return str(o.get("value") or o.get("text"))
            return "Yes"

    return ""


async def _step_fields(root, step: int, start_index: int) -> list[dict]:
    data = await root.evaluate(r"""node => {
      const visible = el => {
        const box = el.getBoundingClientRect();
        const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        return box.width > 0 && box.height > 0 && (!style || (style.visibility !== 'hidden' && style.display !== 'none'));
      };
      const isVisibleControl = el => {
        if (visible(el)) return true;
        const type = (el.type || '').toLowerCase();
        if (['radio', 'checkbox'].includes(type)) {
          const container = el.closest('[role="radio"], [role="checkbox"], label, fieldset, [role="radiogroup"]');
          return container ? visible(container) : false;
        }
        return false;
      };

      const resume = /\bresume\b/i.test(node.innerText) &&
        [...node.querySelectorAll('button')].some(button => /upload resume/i.test(button.innerText));

      const allControls = [...node.querySelectorAll('input, select, textarea')];
      const fields = [];
      const processedRadioGroups = new Set();

      allControls.forEach((el, position) => {
        const type = el.tagName === 'SELECT' ? 'select' :
                     el.tagName === 'TEXTAREA' ? 'textarea' :
                     (el.type || 'text').toLowerCase();

        if (!isVisibleControl(el) || ['hidden', 'submit', 'button', 'reset', 'image', 'password'].includes(type)) {
          return;
        }

        if (resume && type === 'radio') {
          return;
        }

        if (type === 'radio') {
          const groupContainer = el.closest('fieldset, [role="radiogroup"], [data-test-form-builder-radio-button-form-component]') || el.parentElement;
          const groupKey = el.name ? ('name:' + el.name) : ('pos:' + position);

          if (processedRadioGroups.has(groupKey)) {
            return;
          }
          processedRadioGroups.add(groupKey);

          let rawLabel = '';
          const legend = groupContainer?.querySelector('legend');
          if (legend && legend.textContent.trim()) {
            rawLabel = legend.textContent.trim();
          } else {
            const titleEl = groupContainer?.querySelector('[data-test-form-builder-radio-button-form-component__title], h3, h4, [role="heading"]');
            if (titleEl && titleEl.textContent.trim()) {
              rawLabel = titleEl.textContent.trim();
            } else {
              const firstRadio = groupContainer?.querySelector('[role="radio"], input[type="radio"]');
              if (firstRadio) {
                for (const child of (groupContainer?.querySelectorAll('p, span, label, h3, h4') || [])) {
                  if (child.compareDocumentPosition(firstRadio) & Node.DOCUMENT_POSITION_FOLLOWING) {
                    const txt = child.textContent.trim();
                    if (txt && !/^\s*\d+\s*(\/|of|trên)\s*\d+/i.test(txt)) {
                      rawLabel = txt;
                      break;
                    }
                  }
                }
              }
            }
          }
          if (!rawLabel) {
            let prev = groupContainer?.previousElementSibling;
            while (prev) {
              const txt = prev.textContent.trim();
              if (txt && !/^\s*\d+\s*(\/|of|trên)\s*\d+/i.test(txt) && !['BUTTON', 'HR', 'FOOTER'].includes(prev.tagName)) {
                rawLabel = txt;
                break;
              }
              prev = prev.previousElementSibling;
            }
          }
          if (!rawLabel && groupContainer?.parentElement) {
            for (const cand of groupContainer.parentElement.querySelectorAll('[data-test-form-builder-radio-button-form-component__title], h3, h4, p, span.t-bold, label')) {
              const txt = cand.textContent.trim();
              if (txt && !/^\s*\d+\s*(\/|of|trên)\s*\d+/i.test(txt) && !groupContainer.contains(cand)) {
                rawLabel = txt;
                break;
              }
            }
          }
          if (!rawLabel) {
            const labelNode = el.labels?.[0] || el.closest('label');
            rawLabel = (labelNode?.textContent || el.getAttribute('aria-label') || '').trim();
          }

          const isRequired = !!(groupContainer?.querySelector('input[required]')) ||
                             /\*/.test(rawLabel) ||
                             !!el.required;

          const cleanLabel = rawLabel.replace(/\s*\*\s*$/, '').trim().slice(0, 250);

          const optionInputs = allControls.filter(c => {
            if ((c.type || '').toLowerCase() !== 'radio') return false;
            if (el.name && c.name) return c.name === el.name;
            if (groupContainer) return groupContainer.contains(c);
            return c === el;
          });

          const options = [];
          let checkedValue = '';

          optionInputs.forEach(optInput => {
            const optContainer = optInput.closest('[role="radio"], .fb-radio-buttons, label') || optInput.parentElement;
            let optText = '';
            if (optContainer && optContainer !== groupContainer) {
              const optClone = optContainer.cloneNode(true);
              optClone.querySelectorAll('input').forEach(i => i.remove());
              optText = optClone.textContent.trim();
            }
            if (!optText) {
              optText = (optInput.labels?.[0]?.textContent || optInput.value || '').trim();
            }
            const optVal = optText || optInput.value || '';
            if (optInput.checked) {
              checkedValue = optVal;
            }
            if (optVal && !options.some(o => o.value === optVal)) {
              options.push({value: optVal, text: optText || optVal});
            }
          });

          fields.push({
            position,
            type: 'radio',
            label: cleanLabel || el.name || 'Choice',
            name: el.name || '',
            required: isRequired,
            options,
            value: checkedValue,
            max_length: null
          });
          return;
        }

        const labelNode = el.labels?.[0] || el.closest('label');
        const cleanLabelNode = labelNode?.cloneNode(true);
        cleanLabelNode?.querySelectorAll('input,select,textarea,button').forEach(control => control.remove());
        let label = (cleanLabelNode?.textContent || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim();
        if (!label) {
          const prev = el.previousElementSibling;
          if (prev && ['LABEL', 'P', 'SPAN', 'H3'].includes(prev.tagName)) {
            label = prev.textContent.trim();
          } else {
            const next = el.nextElementSibling;
            if (next && ['LABEL', 'P', 'SPAN'].includes(next.tagName)) {
              label = next.textContent.trim();
            } else if (el.parentElement) {
              const pClone = el.parentElement.cloneNode(true);
              pClone.querySelectorAll('input,select,textarea,button').forEach(c => c.remove());
              label = pClone.textContent.trim();
            }
          }
        }
        const isRequired = !!el.required || /\*/.test(label);
        const finalLabel = label.replace(/\s*\*\s*$/, '').trim().slice(0, 250);

        fields.push({
          position,
          type,
          label: finalLabel,
          name: el.name || '',
          required: isRequired,
          options: type === 'select' ? [...el.options].map(opt => ({value: opt.value, text: opt.text.trim()})) : [],
          value: type === 'checkbox' ? (el.checked ? 'yes' : '') : el.value || '',
          max_length: el.maxLength > 0 ? el.maxLength : null
        });
      });

      if (resume) {
        fields.push({
          position: -1,
          type: 'file',
          label: 'Resume',
          name: 'resume',
          required: true,
          options: [],
          value: '',
          accept: '.pdf,application/pdf',
          max_file_bytes: 2000000,
          max_length: null
        });
      }

      return fields;
    }""")
    result = []
    for offset, field in enumerate(data):
        item = {**field, "step": step, "index": start_index + offset}
        if item.get("type") == "radio" and item.get("options") and not item.get("value"):
            inferred = _infer_default_radio_answer(item.get("label", ""), item.get("options", []))
            if inferred:
                item["value"] = inferred
        result.append(item)
    return result


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
    missing = [f"Answer required: {field.get('label') or ('Field ' + str(field.get('index')))}"
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
                has_selected = (
                    await root.locator("input[type=radio]:checked, [aria-checked=true]").count() > 0
                    or await root.locator(".jobs-document-upload__title, [data-test-document-title]").count() > 0
                )
                if not has_selected and await upload.is_visible():
                    async with page.expect_file_chooser(timeout=6000) as chooser:
                        await upload.click(timeout=6000)
                    await (await chooser.value).set_files(str(resume_path))
                    await page.wait_for_timeout(1000)
            except Exception:
                missing.append(f"Could not attach the reviewed resume for {label}")
            continue

        answer = str(answers.get(str(field["index"]), "") or field.get("value") or "")
        if field["required"] and not answer.strip():
            if field["type"] == "radio" and field.get("options"):
                inferred = _infer_default_radio_answer(label, field["options"])
                if inferred:
                    answer = inferred
                else:
                    missing.append(f"Answer required: {label}")
                    continue
            elif field["type"] == "radio" and re.search(r"\byes\b", label, re.I):
                answer = "yes"
            else:
                missing.append(f"Answer required: {label}")
                continue

        if not answer:
            continue

        try:
            if field["type"] == "radio":
                chosen = None
                opt_pattern = re.compile(rf"^\s*{re.escape(answer)}\s*$", re.I)
                scope = root
                containers = root.locator("fieldset, [role='radiogroup'], [data-test-form-builder-radio-button-form-component]")
                c_count = await containers.count()
                if c_count == 1:
                    scope = containers.first
                elif c_count > 1:
                    words = [w for w in re.sub(r'[^\w\s]', '', label).split() if len(w) > 3][:3]
                    for ci in range(c_count):
                        cand = containers.nth(ci)
                        cand_text = await cand.inner_text()
                        if any(w.casefold() in cand_text.casefold() for w in words):
                            scope = cand
                            break

                candidates = scope.locator("[role='radio'], label, .fb-radio-buttons").filter(has_text=opt_pattern)
                if await candidates.count() > 0:
                    chosen = candidates.first
                else:
                    candidates = scope.locator("[role='radio'], label, .fb-radio-buttons").filter(
                        has_text=re.compile(re.escape(answer), re.I)
                    )
                    if await candidates.count() > 0:
                        chosen = candidates.first

                if chosen is not None:
                    await chosen.click(timeout=3000)
                    radio_input = chosen.locator("input[type='radio']").first
                    if await radio_input.count() > 0 and not await radio_input.is_checked():
                        await radio_input.check(force=True)
                else:
                    if field.get("position", -1) >= 0 and field["position"] < await controls.count():
                        locator = controls.nth(field["position"])
                        await locator.check(force=True)

            elif field["type"] == "select":
                locator = controls.nth(field["position"])
                try:
                    await locator.select_option(value=answer)
                except Exception:
                    await locator.select_option(label=answer)

            elif field["type"] == "checkbox":
                locator = controls.nth(field["position"])
                if answer.casefold() in ("yes", "true", "checked", "1"):
                    await locator.check()
                else:
                    await locator.uncheck()

            else:
                locator = controls.nth(field["position"])
                if field.get("max_length") and len(answer) > field["max_length"]:
                    missing.append(f"Answer is too long: {label}")
                    continue
                await locator.fill(answer)
        except Exception:
            missing.append(f"Could not fill: {label}")
    return missing


async def _advance(page: Page, button, step: int) -> bool:
    for _ in range(30):
        try:
            if await button.is_visible() and await button.is_enabled():
                await button.click(timeout=2000)
        except Exception:
            pass
        await page.wait_for_timeout(250)
        try:
            _, new_button, action, current, _ = await _easy_apply_step(page)
            if current > step or (current == step and action == "submit"):
                return True
            button = new_button
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
                await page.get_by_text(re.compile(
                    r"application (?:sent|submitted|was sent)|your application was sent|you applied|ứng tuyển thành công|đã gửi đơn ứng tuyển",
                    re.I
                )).first.wait_for(timeout=8000)
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
    clean_stale_chrome_lock(settings.browser_profile)
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(settings.browser_profile), headless=True, **chrome_context_options(required=True),
            )
        except Exception as error:
            if "SingletonLock" in str(error) or "ProcessSingleton" in str(error):
                clean_stale_chrome_lock(settings.browser_profile)
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
            control = await wait_for_linkedin_apply_control(page)
            if control["kind"] != "linkedin_easy_apply":
                raise ValueError(control.get("detail") or "This posting no longer offers LinkedIn Easy Apply")
            return await inspect_easy_apply_dialog(
                page, posting_url, draft["form_data"].get("answers", {}), Path(draft["resume_path"]),
            )
        finally:
            await context.close()


def unwrap_linkedin_redirect(value: str | None) -> str | None:
    """Extract destination URL from LinkedIn /safety/go or redirect wrappers."""
    if not value:
        return None
    parts = urlsplit(value)
    host = (parts.hostname or "").casefold()
    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and ("/safety/go" in parts.path or "/safety/redirect" in parts.path):
        query = parse_qs(parts.query)
        for key in ("url", "dest", "target", "redirect", "redir", "session_redirect"):
            val = query.get(key, [None])[0]
            if val:
                decoded = unquote(val)
                p = urlsplit(decoded)
                if p.scheme in ("http", "https") and p.hostname and not (p.hostname.casefold() == "linkedin.com" or p.hostname.casefold().endswith(".linkedin.com")):
                    return decoded
    return None


def _external_application_url(value: str | None) -> bool:
    if unwrap_linkedin_redirect(value):
        return True
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
      const isExternal = href => {
        try {
          const u = new URL(href);
          const host = u.hostname.toLowerCase();
          if (host !== 'linkedin.com' && !host.endsWith('.linkedin.com')) return true;
          if (u.pathname.includes('/safety/go') || u.pathname.includes('/safety/redirect')) {
            const dest = u.searchParams.get('url') || u.searchParams.get('dest') || u.searchParams.get('target');
            if (dest) {
              const du = new URL(dest);
              return du.hostname.toLowerCase() !== 'linkedin.com' && !du.hostname.toLowerCase().endsWith('.linkedin.com');
            }
          }
        } catch (_) {}
        return false;
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
        candidates.find(item => item.href && isExternal(item.href)) ||
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
    clean_stale_chrome_lock(settings.browser_profile)
    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(settings.browser_profile), headless=True, **chrome_context_options(required=True),
            )
        except Exception as error:
            if "SingletonLock" in str(error) or "ProcessSingleton" in str(error):
                clean_stale_chrome_lock(settings.browser_profile)
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
            external_url = unwrap_linkedin_redirect(control.get("url")) or (control.get("url") if _external_application_url(control.get("url")) else None)
            if external_url:
                return {"kind": "web", "url": external_url, "detail": "External Apply link found on LinkedIn."}
            opened: list[Page] = []
            context.on("page", lambda new_page: opened.append(new_page))
            try:
                await page.locator('[data-job-radar-apply-control="true"]').click(timeout=10000)
            except PlaywrightTimeoutError:
                if await linkedin_sign_in_dialog_visible(page):
                    return {"kind": "sign_in_required", "detail": "LinkedIn is covering Apply with a sign-in dialog. Sign in to LinkedIn in Job Radar's browser, then try again."}
                return {"kind": "unknown", "detail": "LinkedIn's Apply control could not be opened. Open the posting to continue manually."}
            
            target_url = None
            for _ in range(20):
                await asyncio.sleep(0.25)
                target = opened[-1] if opened else page
                target_url = unwrap_linkedin_redirect(target.url) or (target.url if _external_application_url(target.url) else None)
                if target_url:
                    break

            if target_url:
                return {"kind": "web", "url": target_url, "detail": "External Apply destination opened from LinkedIn."}
            return {"kind": "unknown", "detail": "Apply did not reveal a supported external application page. Open the posting to continue manually."}
        finally:
            await context.close()
