# Destination warning remains after a valid destination is saved

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `applications`, `ui`

## Summary

Draft warnings are generated at preparation and are not reconciled after edits.

## Steps to reproduce

1. Prepare a job with no application URL.
2. Save a valid email destination.
3. Reload the draft.

## Expected behavior

The missing-destination warning disappears and Send readiness reflects current validation.

## Actual behavior

The draft kept `No application destination is known` after saving the email address. Send is enabled even before a destination is supplied.

## Code references

`job_radar/drafting.py:194-197`, `job_radar/drafting.py:225-241`, `job_radar/static/app.js:254-255`

## Acceptance criteria

Recompute warnings on every draft update and enable Send only after current package checks pass.

## Resolution

Draft updates now recalculate destination warnings. The detail API reports current send blockers, including invalid destination, missing SMTP settings, uninspected web forms, unanswered required fields, and unassigned files; the Send button uses that readiness state. A regression test checks the warning clears and readiness changes after an email destination and SMTP settings are saved.
