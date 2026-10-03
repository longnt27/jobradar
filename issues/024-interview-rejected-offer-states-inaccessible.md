# Interview, Rejected, and Offer states have no UI controls

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `jobs`, `ui`

## Summary

The API supports later application states, but the Jobs UI exposes only Interesting and Ignore actions and omits those states from its filter.

## Steps to reproduce

1. Open any job detail.
2. Inspect the available state buttons and the Job state filter.
3. Try to mark or find an Interview, Rejected, or Offer job through the UI.

## Expected behavior

The user can track and filter all supported states in the workspace.

## Actual behavior

No UI action or filter option exists for these states; they can only be set through the API.

## Code references

`job_radar/web.py:57-59`, `job_radar/static/index.html:41`, `job_radar/static/app.js:108-119`

## Acceptance criteria

Expose state transitions and filter options for all supported application outcomes.
