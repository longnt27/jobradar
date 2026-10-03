# Source scan failure is shown as Scan complete

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `scanning`, `ui`

## Summary

The scan endpoint returns failure details in a successful HTTP response, but the UI always displays completion.

## Steps to reproduce

1. Configure a career source that cannot be reached.
2. Click its Scan now button.
3. Compare the notice with the source status and API result.

## Expected behavior

The notice reports the actual `failed` or `auth_required` status and reason.

## Actual behavior

The UI displayed `Scan complete` even though the source was still running during one test and later failed.

## Code references

`job_radar/scanner.py:113-118`, `job_radar/static/app.js:147-150`

## Acceptance criteria

Use the returned status and error text; refresh after completion and avoid success wording for `already_running` or failed scans.

## Resolution

The source scan action now waits for the API result, refreshes source and home data, and reports success, empty, already running, authentication required, or failure using the returned status and error. The scan button is disabled while that request is active.
