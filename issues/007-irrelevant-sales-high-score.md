# Obvious non-target role receives a high match score

- **Status:** Closed
- **Severity:** Medium
- **Labels:** `bug`, `ranking`

## Summary

Setting the role component to zero is insufficient to exclude a clearly irrelevant title.

## Steps to reproduce

1. Score a `Sales Manager` job in Hanoi whose description mentions AI research and Python.
2. Use a profile with Python as a skill.

## Expected behavior

The role is excluded or receives a clearly low score with an exclusion reason.

## Actual behavior

`score_job` returned 70/100 with role component 0.

## Code references

`job_radar/ranking.py:16-43`

## Acceptance criteria

Apply an exclusion cap or explicit negative-role decision before combining other components.

## Resolution

Titles with an explicit negative role term are capped at 20/100 and the score explanation names the term. A regression test covers a Sales Manager posting with otherwise matching AI and Python text.
