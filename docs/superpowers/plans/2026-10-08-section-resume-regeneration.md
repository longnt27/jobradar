# Section-level Resume Regeneration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regenerate one resume content section with a smaller model request and preserve every other reviewed part of the application.

**Architecture:** Add section-specific model responses in `drafting.py`, validate and merge into the existing draft, and rebuild its PDF through `update_draft`. Extend the API section enum and the application review control. Skip web-form reinspection for targeted changes.

**Tech Stack:** Python, Pydantic, FastAPI, Playwright, vanilla JavaScript, Tectonic PDF renderer.

**Spec:** `docs/superpowers/specs/2026-10-08-section-resume-regeneration-design.md`

## Global Constraints

- Preserve existing unsent draft sections, destination, and form answers outside the selected section.
- Use the configured drafting provider; never generate contact facts or submit an application in this flow.
- Keep the full-draft option and existing retry-after-quota behavior.

## Review Focus

- Provider returns an invalid project ID: reject without changing the draft.
- Provider returns an incomplete project section: reject without changing the draft.
- Draft changes while the provider runs: reject the stale revision.
- Provider quota fails: preserve the current PDF and saved content.
- PDF render fails: preserve the current DB draft and show the error.

---

### Task 1: Targeted model and merge

**Files:** `job_radar/drafting.py`, `tests/test_applications.py`

**Interfaces:** `regenerate_draft(db, settings, draft_id, prompt, section)` remains the entry point and returns `changes` and `regenerated_section`.

- [x] Write failing tests that capture provider response type and prompt, verify only one section changes, verify the PDF hash changes, and cover invalid projects and stale drafts.
- [x] Run those tests and confirm the relevant failures.
- [x] Implement small section response schemas, prompts, validation, and targeted merge; retain the full-draft path.
- [x] Run targeted tests to green.

### Task 2: API and review screen

**Files:** `job_radar/web.py`, `job_radar/static/app.js`, `tests/test_application_workspace_ui.py`

**Interfaces:** `RegenerateInput.section` adds experience, education, achievements, and skills. Existing POST path and response stay the same.

- [x] Write failing API and browser tests for the new options and targeted request.
- [x] Run those tests and confirm failures.
- [x] Update the section selector, quota guidance, request schema, and changed-section feedback.
- [x] Run targeted tests to green.

### Task 3: Avoid unrelated form work and verify

**Files:** `job_radar/auto_apply.py`, `tests/test_auto_apply.py`

**Interfaces:** The manager keeps `regenerate(draft_id, prompt, section)` and skips `inspect_form` unless `section == 'all'`.

- [x] Write a failing test proving targeted regeneration does not reinspect the web form.
- [x] Implement the conditional and run the test.
- [x] Run relevant application, drafting, PDF, and browser tests; verify the running app.
