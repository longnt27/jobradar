# Failed project generation exposes raw README HTML as an approvable bullet

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `projects`, `ui`

## Summary

The fallback project claim uses the first README line without stripping markup. If model generation fails, the UI presents that claim as a project bullet with an active Approve button.

## Steps to reproduce

1. Use Codex to inspect `https://github.com/longnt27/pronunciation-assessment`.
2. Let the six-bullet validation error leave generation unfinished.
3. Open GitHub projects and inspect the unapproved card.

## Expected behavior

The card clearly indicates generation failed, shows clean placeholder text, and requires a meaningful reviewed claim before approval.

## Actual behavior

The bullet textarea contained `Project: pronunciation-assessment. <div align="center">` and the Approve project button was enabled.

## Code references

`job_radar/evidence.py:112-127`, `job_radar/static/app.js:291-307`

## Acceptance criteria

Strip README markup for fallback text, expose the generation failure on the card, and block approval of placeholder claims.
