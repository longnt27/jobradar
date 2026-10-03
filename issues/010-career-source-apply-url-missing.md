# Career collector never records application destinations

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `collection`, `applications`

## Summary

Career postings are collected without an `apply_url`, even when their detail pages show application instructions.

## Steps to reproduce

1. Scan a career page with a posting that has an Apply link or application email.
2. Open the resulting job in Job Radar.

## Expected behavior

The best current application destination is captured or clearly extracted for review.

## Actual behavior

The career collector constructs `ObservedJob` without `apply_url`. In the QA scan, a VinAI description contained application email addresses while the job showed no destination.

## Code references

`job_radar/collectors.py:261-265`

## Acceptance criteria

Extract and validate employer Apply links or email instructions; retain the source link as fallback.
