# Job and application cards are not keyboard accessible

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `accessibility`, `ui`

## Summary

The selectable cards are clickable divs without focus, role, or keyboard handlers.

## Steps to reproduce

1. Navigate Jobs or Applications with Tab and Enter only.
2. Try to open a card.

## Expected behavior

Each card is reachable and operable by keyboard with an announced interactive role.

## Actual behavior

Accessibility inspection exposed the cards as containers rather than buttons or links; the click handlers are mouse-only.

## Code references

`job_radar/static/app.js:84-93`, `job_radar/static/app.js:235-239`

## Acceptance criteria

Render semantic buttons or links for selection and preserve focus styling.
