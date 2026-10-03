# Literal C++ job search returns an FTS syntax error

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `search`

## Summary

User search text is passed directly to SQLite FTS5 query syntax.

## Steps to reproduce

1. Open Jobs.
2. Enter `C++` and click Search.

## Expected behavior

Jobs containing C++ are returned, or the query is interpreted as literal text.

## Actual behavior

The API returned 422 with `fts5: syntax error near '+'`; the UI displayed `Invalid search`.

## Code references

`job_radar/web.py:422-429`

## Acceptance criteria

Escape/tokenize ordinary search text before MATCH, or provide a documented advanced-query mode separately.
