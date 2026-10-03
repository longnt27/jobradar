# Saving Telegram settings without a token returns a server error

- **Status:** Open
- **Severity:** Low
- **Labels:** `bug`, `notifications`, `validation`

## Summary

The Telegram form allows a blank token to mean “keep saved token,” but a first-time blank submission reaches `save_telegram` and raises an uncaught `ValueError`.

## Steps to reproduce

1. Start with no Telegram settings.
2. Submit an empty bot token and a nonempty chat ID.

## Expected behavior

The API returns a clear 4xx validation message asking for the initial bot token.

## Actual behavior

The API returned HTTP 500 `Internal Server Error` in the QA instance.

## Code references

`job_radar/web.py:227-231`, `job_radar/notifications.py:22-25`, `job_radar/static/index.html:103`

## Acceptance criteria

Validate the first-time token requirement at the API boundary and surface a readable error in the form.
