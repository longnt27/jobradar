# Missing SMTP configuration creates a permanent duplicate-send lock

- **Status:** Open
- **Severity:** High
- **Labels:** `bug`, `applications`, `email`

## Summary

A preflight configuration error is recorded as an uncertain submission, making a real send impossible without database intervention.

## Steps to reproduce

1. Prepare a draft with a valid email destination.
2. Leave SMTP unconfigured and call Send.
3. Configure SMTP and call Send again for the same vacancy.

## Expected behavior

The first call reports a configuration error without creating a submission; the user can retry after setup.

## Actual behavior

The first call returned `submitted_unconfirmed` with 'configure-smtp'; the second returned 422 because that status blocks retries.

## Code references

`job_radar/apply.py:224-256`

## Acceptance criteria

Validate SMTP settings before inserting a submission record; reserve uncertain status for attempts that may have reached the transport.
