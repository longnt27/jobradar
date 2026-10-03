# Required radio group rejects the unselected choice

- **Status:** Closed
- **Severity:** High
- **Labels:** `bug`, `applications`, `web-forms`

## Summary

The form sender treats each required radio input as independently required.

## Steps to reproduce

1. Inspect a local form with a required Yes/No radio group and a resume upload.
2. Answer Yes and leave No unselected.
3. Click Send.

## Expected behavior

Exactly one selected option satisfies the radio group and the form can submit.

## Actual behavior

The app returned `needs_user_attention: Review required choice: No` before submitting.

## Code references

`job_radar/apply.py:193-198`

## Acceptance criteria

Group radios by name; require one selected option per required group and check only that option.

## Resolution

Required radio inputs are validated as a named group; selecting one choice satisfies the group. An end-to-end application form test covers the submission.
