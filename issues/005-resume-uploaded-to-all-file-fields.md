# Resume is uploaded to every PDF-compatible file field

- **Status:** Open
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
