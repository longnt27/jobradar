# Job Radar QA issues

These local Markdown files track QA cases, one per finding. They have not been posted to GitHub. All 24 cases are closed, with a resolution and a dedicated conventional commit for each.

## Initial QA scope

- Initial automated suite: 28 passed with `uv run --extra dev pytest -q` before the fixes.
- Codex CLI 0.153.4: QA resume extraction, Quizzer project drafting, application drafting, and custom form answers succeeded. Pronunciation Assessment project drafting failed validation on a six-bullet response.
- GitHub profile `longnt27`: 32 public repositories listed; `quizzer` and `pronunciation-assessment` inspected from isolated QA data.
- Visual checks: Home, Jobs, Applications, Profile, Projects, Sources, Employers, and generated A4 PDFs.
- No real application, email, or Telegram alert was sent. External social sign-in and live SMTP were not exercised.

## Resolution verification

- Full suite: 54 passed with `uv run --no-sync pytest -vv -x` after all 24 fixes.
- Browser test covers setting and filtering Interview, Rejected, and Offer states. Additional browser checks covered search layout, empty results, and keyboard job selection.
- Web form regression tests use local HTTP fixtures; no real application was sent.

## Cases

| ID | Status | Severity | Issue |
| --- | --- | --- | --- |
| [001](001-false-web-submission-confirmation.md) | Closed | Critical | False web submission confirmation when browser validation blocks the form |
| [002](002-email-config-lockout.md) | Closed | High | Missing SMTP configuration creates a permanent duplicate-send lock |
| [003](003-stale-application-url.md) | Closed | High | Vacancy merge and refresh retain stale application URLs |
| [004](004-required-radio-group.md) | Closed | High | Required radio group rejects the unselected choice |
| [005](005-resume-uploaded-to-all-file-fields.md) | Closed | High | Resume is uploaded to every PDF-compatible file field |
| [006](006-scores-not-recomputed.md) | Closed | High | Profile edits leave existing job match scores stale |
| [007](007-irrelevant-sales-high-score.md) | Closed | Medium | Obvious non-target role receives a high match score |
| [008](008-failed-scan-reported-complete.md) | Closed | Medium | Source scan failure is shown as Scan complete |
| [009](009-old-career-source-remains-enabled.md) | Closed | Medium | Changing an employer career URL keeps the old source enabled |
| [010](010-career-source-apply-url-missing.md) | Closed | Medium | Career collector never records application destinations |
| [011](011-failed-telegram-alert-not-retried.md) | Closed | Medium | Transient Telegram failure loses a new-job alert |
| [012](012-application-review-incomplete-resume-editing.md) | Closed | Medium | Application review cannot edit all content in the generated resume |
| [013](013-job-search-field-collapses.md) | Closed | Medium | Jobs search input collapses to a few pixels |
| [014](014-cpp-search-fts-error.md) | Closed | Medium | Literal C++ job search returns an FTS syntax error |
| [015](015-empty-search-retains-detail.md) | Closed | Medium | Empty job search keeps an unrelated selected job visible |
| [016](016-cards-keyboard-inaccessible.md) | Closed | Medium | Job and application cards are not keyboard accessible |
| [017](017-stale-destination-warning.md) | Closed | Medium | Destination warning remains after a valid destination is saved |
| [018](018-codex-project-schema-overflow.md) | Closed | Medium | Codex project drafting fails when it returns six bullets |
| [019](019-raw-readme-html-project-placeholder.md) | Closed | Medium | Failed project generation exposes raw README HTML as an approvable bullet |
| [020](020-form-control-text-unintentionally-bold.md) | Closed | Low | Form controls inherit bold label weight and reduce readability |
| [021](021-repository-results-hide-selected-projects.md) | Closed | Medium | Repository results push project review thousands of pixels below the search |
| [022](022-telegram-empty-token-server-error.md) | Closed | Low | Saving Telegram settings without a token returns a server error |
| [023](023-vietnamese-name-fields-reversed.md) | Closed | Medium | Automatic first and last name answers split Vietnamese names incorrectly |
| [024](024-interview-rejected-offer-states-inaccessible.md) | Closed | Medium | Interview, Rejected, and Offer states have no UI controls |
