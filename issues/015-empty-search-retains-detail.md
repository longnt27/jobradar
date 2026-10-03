# Empty job search keeps an unrelated selected job visible

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `ui`, `search`

## Summary

The detail panel is not cleared when the selected job falls outside the new result set.

## Steps to reproduce

1. Select a job.
2. Search for a term with no results.

## Expected behavior

The detail panel clears or explicitly states that the selected job is outside the filter.

## Actual behavior

The list said `No jobs found` while the previous job title and action buttons stayed visible.

## Code references

`job_radar/static/app.js:80-95`

## Acceptance criteria

Clear `activeJob` and the detail panel whenever the selected job is absent from the filtered results.
