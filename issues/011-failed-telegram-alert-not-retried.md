# Transient Telegram failure loses a new-job alert

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `notifications`, `scanning`

## Summary

Alert delivery has no durable queue or retry state.

## Steps to reproduce

1. Configure Telegram and make its send request fail temporarily.
2. Scan a new matching job.
3. Restore Telegram and rescan the same unchanged posting.

## Expected behavior

The failed alert remains pending and is retried or visibly marked failed.

## Actual behavior

The scanner logs the failure and continues; a repeated observation is no longer new, so its ID is not sent again. This follows the code path.

## Code references

`job_radar/scanner.py:95-111`, `job_radar/notifications.py:43-65`

## Acceptance criteria

Persist notification attempts per vacancy and channel, then retry unsent alerts independently of scan novelty.

## Resolution

Eligible Telegram alerts are stored per vacancy with pending/sent status, attempt count, and last error. Failed sends remain pending; the scan scheduler retries them independently of whether a later scan discovers a new vacancy. A regression test covers temporary failure, recovery, and duplicate suppression.
