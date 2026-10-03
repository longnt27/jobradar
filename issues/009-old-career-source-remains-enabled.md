# Changing an employer career URL keeps the old source enabled

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `employers`, `scanning`

## Summary

Updating an employer adds a new scan source without retiring the old one.

## Steps to reproduce

1. Add a QA employer with career URL A.
2. Set its career URL to B.
3. List career sources for that employer.

## Expected behavior

Only the current URL is enabled unless the user deliberately keeps multiple career sources.

## Actual behavior

Both A and B remained enabled and scheduled in the isolated QA app.

## Code references

`job_radar/web.py:389-402`, `job_radar/web.py:146-153`

## Acceptance criteria

Replace or disable the superseded source when the URL is changed.
