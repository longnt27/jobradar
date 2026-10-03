# Form controls inherit bold label weight and reduce readability

- **Status:** Closed
- **Severity:** Low
- **Labels:** `bug`, `ui`, `visual`

## Summary

Inputs and textareas use `font: inherit` inside labels that have `font-weight:700`, so long editable content renders bold.

## Steps to reproduce

1. Prepare a Codex application containing several project bullets.
2. Open its application review or GitHub projects screen.
3. Compare textarea content with surrounding body text.

## Expected behavior

Labels are emphasized while entered content uses a normal reading weight.

## Actual behavior

The summary, project bullets, and other long textarea values appeared bold and dense in visual QA.

## Code references

`job_radar/static/app.css:1`

## Acceptance criteria

Set a normal font weight on editable controls while keeping label text bold.

## Resolution

Editable inputs, selects, and textareas explicitly use normal font weight while their labels remain bold. Browser computed-style verification measured label weight 700 and input weight 400.
