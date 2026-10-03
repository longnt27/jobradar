# Vacancy merge and refresh retain stale application URLs

- **Status:** Open
- **Severity:** High
- **Labels:** `bug`, `collection`, `applications`

## Summary

Canonical vacancies can keep an old application destination after the source changes or a same-title posting is merged.

## Steps to reproduce

1. Ingest a posting with application URL A.
2. Ingest the same source URL with changed content and application URL B.
3. Separately, ingest a new posting URL with the same company, title, and location but new description and application URL B.

## Expected behavior

The current vacancy displays the current source description and application URL, or distinct requisitions remain separate when identity is uncertain.

## Actual behavior

The same-URL update kept A. The new-URL merge kept both the old description and A; both cases were reproduced with an isolated SQLite database.

## Code references

`job_radar/ingest.py:63-70`, `job_radar/ingest.py:82-100`

## Acceptance criteria

Refresh all canonical destination fields when evidence changes; avoid automatic merge based only on company, title, and location.
