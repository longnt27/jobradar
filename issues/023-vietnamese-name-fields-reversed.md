# Automatic first and last name answers split Vietnamese names incorrectly

- **Status:** Open
- **Severity:** Medium
- **Labels:** `bug`, `applications`, `localization`

## Summary

The default form answer uses the first word of the profile name as “First name” and the rest as “Last name.” For Vietnamese names written surname-first, that reverses the given and family names.

## Steps to reproduce

1. Set the profile name to `Nguyen Trung Long`.
2. Inspect an application form with First name and Last name fields.

## Expected behavior

The fields are filled according to the candidate's chosen name order, or left for review when ambiguous.

## Actual behavior

`_default_answer` returned First name `Nguyen` and Last name `Trung Long` in a direct QA check.

## Code references

`job_radar/apply.py:74-83`

## Acceptance criteria

Store preferred given/family names explicitly or ask the user to review the split before it is used in a package.
