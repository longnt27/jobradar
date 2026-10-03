# Resume is uploaded to every PDF-compatible file field

- **Status:** Closed
- **Severity:** High
- **Labels:** `bug`, `applications`, `attachments`

## Summary

The sender does not distinguish a resume upload from cover letter or other document uploads, while the review UI hides file inputs.

## Steps to reproduce

1. Inspect a form with separate Resume and Cover Letter PDF file fields.
2. Review the package and send it.

## Expected behavior

The user sees each attachment assignment and the sender uploads only the reviewed file to each field.

## Actual behavior

The code sends `resume_path` to every PDF-compatible file input; the review screen filters file fields out. This is a deterministic code-path finding.

## Code references

`job_radar/apply.py:190-193`, `job_radar/static/app.js:253`

## Acceptance criteria

Model attachment fields explicitly, show them in review, and require a reviewed file mapping for each upload.

## Resolution

Each file input now appears in application review with an explicit resume, separate PDF, or optional no-file assignment. Uploaded PDFs are saved under the draft, validated by hash, and sent only to their assigned field. A two-file form test verifies an unassigned required field blocks sending and that distinct files reach the correct inputs.
