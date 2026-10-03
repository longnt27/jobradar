# Codex project drafting fails when it returns six bullets

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `codex-provider`, `projects`

## Summary

The provider schema sent to Codex removes the `maxItems` constraint. The project prompt does not state the five-bullet limit, so a plausible model response is rejected after generation and the project is left without a draft.

## Steps to reproduce

1. Select Codex as the drafting provider.
2. Inspect `https://github.com/longnt27/pronunciation-assessment`.
3. Retry Generate again if necessary.

## Expected behavior

A valid project draft with at most five bullets is available for review, or the model output is retried with a clear bound.

## Actual behavior

The initial inspect left the repository card ungenerated. A Generate again attempt returned six bullets and failed Pydantic validation: `List should have at most 5 items after validation, not 6`.

## Code references

`job_radar/drafting.py:97-109`, `job_radar/evidence.py:15-19`, `job_radar/evidence.py:39-49`

## Acceptance criteria

State the supported bullet count in the prompt and handle a bounded schema-validation retry or safe normalization while retaining the candidate's review step.
