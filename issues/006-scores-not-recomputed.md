# Profile edits leave existing job match scores stale

- **Status:** Closed
- **Severity:** High
- **Labels:** `bug`, `ranking`, `profile`

## Summary

Scores are computed at ingestion and not refreshed when a candidate changes skills or other ranking inputs.

## Steps to reproduce

1. Scan a C++ posting before adding skills.
2. Add `C++` to the profile.
3. Open the existing job and inspect its score explanation.

## Expected behavior

The displayed score and matched skills reflect the current profile, or clearly show the profile version used.

## Actual behavior

The posting still reported no matched skills after the profile contained `C++`; unchanged rescans do not recalculate it.

## Code references

`job_radar/web.py:241-247`, `job_radar/ingest.py:65-73`

## Acceptance criteria

Recompute affected scores on profile save or version scores and schedule a full recalculation.

## Resolution

Saving profile data now recomputes every existing vacancy score and explanation from the current profile. A regression test confirms a scanned C++ job updates after the candidate adds C++ to their skills.
