# Repository results push project review thousands of pixels below the search

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `projects`, `ui`

## Summary

All repository results expand above Selected projects. Inspecting a repository refreshes the project cards but does not scroll or focus the new card.

## Steps to reproduce

1. Open GitHub projects and search username `longnt27`.
2. Inspect any repository in the result list.
3. Try to find its generated project card.

## Expected behavior

The newly inspected project is immediately visible or linked from the result row.

## Actual behavior

The 32 repository rows made the page 6,305 pixels tall; Selected projects began about 5,391 pixels below the viewport. Inspect left the viewport at the result row.

## Code references

`job_radar/static/index.html:120-121`, `job_radar/static/app.js:286-312`, `job_radar/static/app.js:492-516`

## Acceptance criteria

Focus or scroll to the inspected card, and constrain or paginate the repository results so review remains nearby.

## Resolution

The repository list is bounded to a scrollable 380 px area, and inspecting or reopening a repository scrolls to its project editor. Browser verification with 32 repository rows measured a 380 px results list and the editor near the search, rather than thousands of pixels below it.
