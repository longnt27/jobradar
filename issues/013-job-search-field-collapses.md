# Jobs search input collapses to a few pixels

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `ui`, `responsive`

## Summary

The state selector takes nearly all toolbar width, leaving the search input unusably narrow.

## Steps to reproduce

1. Open Jobs at desktop width.
2. Repeat at a narrow browser width.
3. Inspect the search field next to All jobs.

## Expected behavior

The search field remains readable and usable at both widths.

## Actual behavior

Visual QA showed an approximately 20-pixel-wide input at both tested widths.

## Code references

`job_radar/static/app.css:1`, `job_radar/static/index.html:41`

## Acceptance criteria

Constrain the select width and give the search input a real minimum; stack controls at narrow widths.

## Resolution

The job search has a real minimum width, while the state selector stays constrained and the controls wrap on narrow screens. Browser checks measured the search field at 630 px in a 1200 px viewport and 339 px in a 375 px viewport.
