# False web submission confirmation when browser validation blocks the form

- **Status:** Closed
- **Severity:** Critical
- **Labels:** `bug`, `applications`, `data-integrity`

## Summary

A page's existing success-like text is treated as proof that the application was submitted.

## Steps to reproduce

1. Inspect a local application form whose page already says 'Thank you for visiting our careers page.'
2. Enter an invalid value in a required email field and click Send.
3. Observe the browser blocks submission; the test server receives no POST.

## Expected behavior

The draft remains unsent and reports the validation error or an uncertain outcome.

## Actual behavior

The API returns `submitted_confirmed` and changes the draft to `sent`. The local server log showed only GET requests.

## Code references

`job_radar/apply.py:211-219`, `job_radar/apply.py:249-251`

## Acceptance criteria

Confirm submission only from a post-submit transition or receipt specific to that attempt; check browser validity before clicking and never use static page text alone.

## Resolution

The sender checks browser field validity before clicking Submit. Confirmation now requires both an observed submission request and a page transition or changed response text; pre-existing thank-you copy cannot confirm an attempt.
