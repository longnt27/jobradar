# Zalo Inquiry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a deterministic, reviewed JD inquiry to a Facebook post's Zalo contact from the candidate's personal account after explicit approval.

**Architecture:** Keep editable inquiry drafts and immutable send attempts separate from application submissions. A focused inquiry service validates approval, preserves source evidence, and prevents duplicate sends; a Zalo Web adapter handles authenticated recipient lookup and message confirmation through the existing Chrome profile and browser lock.

**Tech Stack:** Existing Python 3.12+, FastAPI, SQLite, Playwright, and vanilla JavaScript/CSS; no new product dependency or AI provider.

**Spec:** `docs/superpowers/specs/2026-10-12-zalo-inquiry-design.md`

## Global Constraints

- The initial message is deterministic and does not call a drafting provider.
- Its Vietnamese default is exactly three sentences: `Em chào anh/chị, em thấy bài đăng tuyển vị trí {position} trong nhóm Facebook {group}.` / `Anh/chị có thể chia sẻ JD chi tiết cho em được không ạ?` / `Em có thể gửi CV qua Zalo cho anh/chị được không ạ?`
- It sends no CV or other attachment.
- The position label is always editable, and an empty label blocks approval.
- Regeneration never overwrites a manually edited message without an explicit action.
- One **Approve and send Zalo inquiry** action approves the exact recipient and final message text.
- A sent inquiry means **contacted, awaiting reply**, not **applied**.
- Zalo's visible web interface is the transport; do not call undocumented messaging endpoints or issue friend requests.
- Once a send could have reached Zalo, an unclear result is uncertain and must not be retried automatically.
- Preserve existing unrelated workspace changes. At execution, use the using-git-worktrees skill for isolation; the feature must not depend on uncommitted LinkedIn message changes.

## Review Focus

- Concurrent approvals or a restart during sending must retain one durable attempt and block a second send (Task 2).
- A merged vacancy with several Facebook sightings must use the selected post's group, title, and phone evidence (Tasks 1 and 4).
- A global template edit must affect new drafts without silently replacing an existing edited inquiry (Tasks 1 and 4).
- An old identical outgoing message must not be mistaken for confirmation of the new send (Task 3).
- Expired login, restricted messaging, or a recipient identity change must stop before sending and expose a useful recovery action (Tasks 3 and 4).

---

## File Structure

- Create `job_radar/zalo_contacts.py`: explicit Zalo contact extraction, phone normalization, template validation/rendering, and observation context.
- Create `job_radar/zalo_inquiry.py`: draft storage, review hashes, lookup evidence, attempt claiming, duplicate guards, and manual resolution.
- Create `job_radar/zalo_browser.py`: Zalo Web authentication detection, phone lookup, recipient identity verification, and one verified send.
- Create `job_radar/zalo_api.py`: an APIRouter factory joining the service and adapter; use injected database, settings, and browser lock.
- Create `job_radar/static/zalo.js`: inquiry review and template settings UI; expose a small `window.JobRadarZalo` interface to app.js.
- Modify `job_radar/db.py`: additive inquiry tables and indexes.
- Modify `job_radar/social_browser.py` and `job_radar/browser_login.py`: Zalo connection support without treating old shared login dates as Zalo authentication.
- Modify `job_radar/web.py`: register the router, serve/cache-version the new script, and include inquiry context in job detail.
- Modify `job_radar/static/index.html`, `job_radar/static/app.js`, and `job_radar/static/app.css`: connection/template controls and job detail hooks.
- Create `tests/test_zalo_contacts.py`, `tests/test_zalo_inquiry.py`, `tests/test_zalo_browser.py`, `tests/test_zalo_api.py`, and `tests/test_zalo_ui.py`; extend existing social login tests.

### Task 1: Deterministic message and source-specific contact candidates

**Files:** Create `job_radar/zalo_contacts.py` and `tests/test_zalo_contacts.py`.

**Interfaces:** Produce `DEFAULT_ZALO_TEMPLATE: str`, `normalize_zalo_phone(value: str) -> str`, `validate_zalo_template(template: str) -> str`, `render_zalo_message(template: str, position: str, group: str) -> str`, and `contact_candidates(db: Database, vacancy_id: str) -> list[dict]`. Each candidate contains `observation_id`, `phone`, `raw_phone`, `evidence`, `post_url`, `group`, and `position`. Normalize Vietnamese local/84/+84 formats to the same lookup number; accept explicitly international numbers in E.164 form and reject malformed or missing numbers. Template fields are limited to `{position}` and `{group}`.

Store the reusable template under the existing settings key `zalo_inquiry_template`; use `DEFAULT_ZALO_TEMPLATE` when absent.

- [ ] **Step 1: Write failing tests.** `test_default_message_uses_em_and_exact_cv_question` compares the full three-sentence string. `test_contacts_require_zalo_on_same_or_adjacent_line` includes unlabeled phone numbers, dates, and salary numbers. `test_phone_formats_share_one_identity` checks `0912 345 678`, `84912345678`, and `+84 912 345 678`. `test_multiple_sightings_keep_their_own_context` checks different groups and positions for one vacancy. `test_template_rejects_unknown_fields` rejects `{email}` and malformed braces while treating inserted values as text.

  ```python
  assert render_zalo_message(DEFAULT_ZALO_TEMPLATE, "AI Engineer", "AI Jobs Vietnam") == (
      "Em chào anh/chị, em thấy bài đăng tuyển vị trí AI Engineer trong nhóm Facebook AI Jobs Vietnam.\n"
      "Anh/chị có thể chia sẻ JD chi tiết cho em được không ạ?\n"
      "Em có thể gửi CV qua Zalo cho anh/chị được không ạ?"
  )
  assert normalize_zalo_phone("+84 912 345 678") == normalize_zalo_phone("0912 345 678")
  ```
- [ ] **Step 2: Run** `uv run pytest tests/test_zalo_contacts.py -q`; confirm failures are from missing behavior.
- [ ] **Step 3: Implement the interfaces** using saved observation payload/title and source name. Return all explicit candidates rather than silently selecting the first one; source text remains untrusted data.
- [ ] **Step 4: Run** `uv run pytest tests/test_zalo_contacts.py -q`; require all tests passing.
- [ ] **Step 5: Commit** the contact module and tests with `feat: prepare deterministic Zalo inquiry messages`.

### Task 2: Durable review and send attempt service

**Files:** Modify `job_radar/db.py`; create `job_radar/zalo_inquiry.py` and `tests/test_zalo_inquiry.py`.

**Interfaces:** Consume Task 1. Produce `create_inquiry(db, vacancy_id: str, observation_id: str, phone: str | None = None) -> dict`, `get_inquiry(db, inquiry_id: str) -> dict`, `update_inquiry(db, inquiry_id: str, updates: dict) -> dict`, `regenerate_inquiry(db, inquiry_id: str) -> dict`, `record_recipient(db, inquiry_id: str, recipient: dict) -> dict`, `inquiry_hash(inquiry: dict) -> str`, `claim_inquiry_send(db, inquiry_id: str, expected_hash: str) -> dict`, `finish_inquiry_send(db, attempt_id: str, result: dict) -> dict`, `resolve_inquiry_attempt(db, attempt_id: str, sent: bool) -> dict`, `recover_inquiry_sends(db) -> int`, and `inquiry_context(db, vacancy_id: str) -> dict`.

`zalo_inquiry_drafts` stores IDs, normalized phone, position, group, template snapshot, final message, recipient/lookup evidence as JSON, and timestamps. `zalo_inquiry_attempts` stores draft/vacancy IDs, phone, approved hash/payload, status, receipt/error, and timestamps. Statuses are `sending`, `sent_confirmed`, `send_uncertain`, `needs_attention`, `failed`, and `confirmed_not_sent`. A partial unique index on `(vacancy_id, phone)` for `sending`, `sent_confirmed`, and `send_uncertain` guards concurrent duplicate sends. `recover_inquiry_sends` converts leftover `sending` attempts to `send_uncertain` at application startup, before send handlers run; constructing a second Database object must not mutate a live send.

`get_inquiry` returns the stored draft/context plus computed `package_hash`, `send_ready`, and `blockers`. Store the SQL group field as `group_name` and expose it as `group` in public context. Hash only the observation ID, normalized phone, position, final message, and recipient identity key/display name; exclude volatile lookup timestamps. A claimed attempt includes `id` and immutable `package_data` with `observation_id`, `phone`, `position`, `message`, and `recipient`. `inquiry_context` returns `{candidates, drafts, attempts, contact_status}`. Raise `InquiryConflict(ValueError)` for stale approval or duplicate attempts, `ValueError` for invalid values, and `KeyError` for missing/foreign records. Freeze edits/regeneration while a draft has a sending, confirmed, or uncertain attempt; an explicitly resolved not-sent attempt permits a fresh lookup/review.

- [ ] **Step 1: Write failing tests.** `test_inquiry_does_not_require_profile_resume_or_provider` creates an inquiry without application prerequisites. `test_edit_invalidates_old_review_hash` rejects approval of earlier text and clears recipient evidence on phone changes. `test_template_setting_does_not_replace_existing_draft` checks snapshot behavior and explicit regeneration. `test_duplicate_approval_claim_is_atomic` issues competing claims and requires one attempt. `test_restart_preserves_uncertain_send_guard` covers a leftover send. `test_manual_resolution_requires_a_fresh_review_for_retry` allows retry only after explicit not-sent resolution and recipient verification. `test_contact_does_not_change_application_progress` requires zero application submission rows and unchanged application progress.

  ```python
  old_hash = inquiry_hash(draft)
  edited = update_inquiry(db, draft["id"], {"message": "Em xin JD chi tiết ạ."})
  assert inquiry_hash(edited) != old_hash
  assert db.one("SELECT COUNT(*) AS count FROM submissions")["count"] == 0
  assert job_after["application_progress"] == job_before["application_progress"]
  ```
- [ ] **Step 2: Run** `uv run pytest tests/test_zalo_inquiry.py -q`; confirm the expected failures.
- [ ] **Step 3: Implement additive tables and service interfaces.** Check observation ownership, nonempty position/message, normalized phone, a verified recipient for that phone, current approval hash, and active duplicate attempts inside the claim transaction. Insert the immutable attempt before external I/O. Keep message edits independent from explicit regeneration. Allow manual resolution only for an uncertain attempt. Manual sent resolution records `receipt.source = 'user_confirmation'`; manual not-sent resolution clears lookup evidence and permits a fresh review.
- [ ] **Step 4: Run** `uv run pytest tests/test_zalo_contacts.py tests/test_zalo_inquiry.py tests/test_job_state_ui.py -q`; require passing results.
- [ ] **Step 5: Commit** service, schema, and tests with `feat: record reviewed Zalo inquiry attempts`.

### Task 3: Zalo sign-in and verified browser transport

**Files:** Create `job_radar/zalo_browser.py` and `tests/test_zalo_browser.py`; modify social/browser login modules and `tests/test_setup.py`.

**Interfaces:** Produce `zalo_authenticated(page: Page) -> bool`, `lookup_recipient_on_page(page: Page, phone: str) -> dict`, `send_inquiry_on_page(page: Page, recipient: dict, message: str) -> dict`, `lookup_zalo_recipient(settings: Settings, phone: str) -> dict`, and `send_zalo_inquiry(settings: Settings, recipient: dict, message: str) -> dict`. Lookup returns `{status: 'ready', recipient: {phone, display_name, identity_key, evidence}}` or `{status: 'needs_attention', detail}`. Sending returns `{status, receipt, error}` using Task 2 statuses. The identity key requires an observable stable account identity or uniquely verified phone lookup; a display name by itself is insufficient.

All five adapter functions are `async def`; callers await their results. Task 2 service functions are synchronous.

- [ ] **Step 1: Inspect the current signed-in Zalo Web UI without sending.** Open Zalo Web in Job Radar's existing Chrome profile while holding the browser lock, allowing the user to sign in if needed. Record observable lookup, chat, composer, send control, outgoing bubble, and sent-status selectors. Build the local fixture from that observed interface. If sign-in is unavailable, continue independent tasks but retain live selector verification as an explicit incomplete check.
- [ ] **Step 2: Write failing fixture tests.** `test_lookup_requires_unique_verified_recipient`, `test_login_and_privacy_restrictions_stop_before_send`, `test_changed_identity_stops_before_send`, `test_send_confirms_only_a_new_exact_outgoing_message`, and `test_timeout_after_send_is_uncertain` assert zero send clicks for blockers and exactly one click after a valid reviewed match. The old identical message test must fail until post-send baseline tracking exists. Add login tests rejecting `id.zalo.me` and deceptive hosts as authenticated state, and proving legacy LinkedIn/Facebook login settings do not connect Zalo.

  ```python
  assert blocked_result["status"] == "needs_attention"
  assert blocked_page_send_clicks == 0
  assert successful_result["status"] == "sent_confirmed"
  assert successful_page_send_clicks == 1
  assert timeout_after_click_result["status"] == "send_uncertain"
  assert old_identical_message_without_new_bubble_result["status"] == "send_uncertain"
  ```
- [ ] **Step 3: Run** `uv run pytest tests/test_zalo_browser.py tests/test_chrome_auth_watch.py -q`; confirm expected failures.
- [ ] **Step 4: Implement the adapter and connection support.** Enter through the official Zalo Web URL, use semantic/observed DOM locators, confirm authentication from the chat UI, re-resolve the approved recipient before sending, and check new outgoing content plus a sent indicator. Treat exceptions after the first possible send as uncertain. Persist the compact receipt without unrelated chat contents. Extend `SITES` and `LOGIN_URLS`. For Zalo sign-in, launch Job Radar's owned Chrome process with an ephemeral loopback-only CDP port, observe `zalo_authenticated` through Playwright, and close that connection/process after completion. A visited URL or existing LinkedIn/Facebook login timestamp cannot establish Zalo authentication.
- [ ] **Step 5: Run** `uv run pytest tests/test_zalo_browser.py tests/test_chrome_auth_watch.py tests/test_setup.py -q`; require passing results. Repeat the signed-in lookup preflight without sending to verify the real UI matches the adapter.
- [ ] **Step 6: Commit** adapter, connection support, and tests with `feat: send approved inquiries through Zalo Web`.

### Task 4: API and editable job contact review

**Files:** Create `job_radar/zalo_api.py`, `job_radar/static/zalo.js`, `tests/test_zalo_api.py`, and `tests/test_zalo_ui.py`; modify `web.py`, `static/index.html`, `static/app.js`, `static/app.css`, and `tests/test_social_auth_ui.py`.

**Interfaces:** `create_zalo_router(db: Database, settings: Settings, browser_lock: asyncio.Lock) -> APIRouter` consumes Tasks 1–3. Routes: `GET/PUT /api/setup/zalo-template` (`{template}`), `GET /api/jobs/{vacancy_id}/zalo` (candidates, drafts, attempts), `POST /api/jobs/{vacancy_id}/zalo` (`{observation_id, phone?}`), `GET/PATCH /api/zalo-inquiries/{inquiry_id}` (`{phone?, position?, message?}`), POST `/api/zalo-inquiries/{inquiry_id}/regenerate`, `/lookup`, `/approve` (`{package_hash}`), and POST `/api/zalo-inquiries/{inquiry_id}/attempts/{attempt_id}/resolve` (`{sent}`, validate attempt ownership). Lookup/approve operations acquire the shared browser lock. HTTP 404 means missing/foreign context, 422 invalid input, and 409 stale approval or duplicate attempt. Approval claims the attempt before invoking the adapter and persists every returned outcome. Register `recover_inquiry_sends` in the existing application startup/lifespan.

`window.JobRadarZalo.mountJob(root, vacancyId, {api, notice})` loads and binds a focused inquiry panel in job detail. `window.JobRadarZalo.mountSettings(root, {api, notice})` binds the reusable template editor. Load `zalo.js` before app.js, serve it from `/zalo.js`, and include it in the existing asset versioning. Add Zalo sign-in controls to the social connection panel. Do not expose a send action in app.js that bypasses the inquiry router.

- [ ] **Step 1: Write failing API and UI tests.** `test_approve_calls_transport_only_with_current_review_hash` edits the bad position text before approval and asserts the exact approved body reaches the mock transport. `test_phone_edit_disables_approval_until_new_lookup` checks recipient evidence reset. `test_multiple_posts_require_explicit_source_selection` shows each group's evidence. `test_existing_custom_message_survives_template_change` requires explicit regeneration. `test_job_refresh_preserves_unsaved_inquiry_edits` protects the editor during job refresh. `test_blocked_and_uncertain_results_have_recovery_actions` checks sign-in/lookup/manual resolution controls. `test_success_shows_contacted_awaiting_reply_and_keeps_prepare_application` checks inquiry and application lifecycles together.

  ```python
  assert stale_approval.status_code == 409
  assert transport_calls == []
  assert approved_response.status_code == 200
  assert transport_calls[0]["message"] == edited_body
  assert page.get_by_role("button", name="Approve and send Zalo inquiry").is_disabled()
  assert page.get_by_text("Contacted, awaiting reply", exact=True).is_visible()
  ```
- [ ] **Step 2: Run** `uv run pytest tests/test_zalo_api.py tests/test_zalo_ui.py -q`; confirm expected failures.
- [ ] **Step 3: Implement routes and review controls.** Display the post excerpt/link, group, phone, profile identity, editable position, and final text together. Saving position does not silently replace the final message; an explicit **Regenerate from template** action does that. Approval remains disabled for unsaved edits, missing recipient evidence, empty position/message, active/confirmed/uncertain attempts, or lookup blockers. The sent state shows the contact number and **Open Zalo Web** control. Render all source and contact values as escaped text. Reset controls and errors when changing drafts or observations, and preserve unsaved editor state during an ordinary job refresh. Update connection copy so Zalo is presented as an optional sending connection rather than a discovery source or a scheduled scan.
- [ ] **Step 4: Run** `uv run pytest tests/test_zalo_api.py tests/test_zalo_ui.py tests/test_social_auth_ui.py tests/test_jobs_discovery_ui.py tests/test_job_state_ui.py -q` and `node --check job_radar/static/zalo.js`; require passing results.
- [ ] **Step 5: Commit** API/UI and tests with `feat: review and approve Zalo contact from job details`.

### Task 5: Complete-flow verification and user documentation

**Files:** Update `README.md`; extend the Task 4 API/UI tests where necessary.

**Interfaces:** Use the completed connection, inquiry, and review interfaces; introduce no new product interface.

- [ ] **Step 1: Add a complete flow test** that imports a Facebook observation, prepares the exact template without invoking any provider, fixes the position, looks up the contact, approves the current hash, verifies one transport call, reloads the durable receipt, and confirms a second approval is blocked while application preparation stays available.
- [ ] **Step 2: Run** the focused Zalo suite and existing social/job lifecycle regressions; then run `uv run pytest -q` once. Investigate failures attributable to the feature, preserving unrelated existing work.
- [ ] **Step 3: Inspect the review UI visually** at desktop and narrow viewport sizes. Verify readable source evidence, visible final text, editable title, disabled/active approval states, and the awaiting-reply state.
- [ ] **Step 4: Verify live Zalo sign-in and recipient lookup without sending.** Record whether authenticated UI/selectors were verified. Sending a real message requires the user's approval of a specific recipient and exact body in Job Radar; fixture tests alone are not evidence of live sending.
- [ ] **Step 5: Document** sign-in, reusable template configuration, position correction, approval, awaiting-reply status, and uncertain-send recovery in README.md. Commit with `docs: explain approved Zalo inquiry workflow`.
- [ ] **Step 6: Review the full feature diff**, fix actionable findings, and rerun only the affected checks. Deliver the implementation with test results and any remaining live verification requirement.
