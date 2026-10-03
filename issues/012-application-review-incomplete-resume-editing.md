# Application review cannot edit all content in the generated resume

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `applications`, `ui`

## Summary

The review screen displays a PDF but exposes draft edits only for summary and selected project bullets.

## Steps to reproduce

1. Prepare an application from a profile with experience, education, achievements, and skills.
2. Open its application review screen.
3. Try to correct an experience bullet or education date for this draft.

## Expected behavior

The user can edit each resume section in the package and inspect the regenerated PDF before sending.

## Actual behavior

No controls exist for those sections in the draft review; saving serializes only summary and project bullet edits.

## Code references

`job_radar/static/app.js:249-253`, `job_radar/static/app.js:275-283`

## Acceptance criteria

Add draft-level controls for all rendered sections or a clear route that regenerates the current draft after source edits.

## Resolution

Application review now exposes draft-level controls for contact details, summary, each experience and project entry, education, achievements, skills, and skill groups. Saving regenerates the PDF; a regression test confirms edits to experience, education, and achievements appear in the rendered file.
