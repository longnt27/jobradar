# Job Radar QA issues

These local Markdown files track QA cases, one per finding. They have not been posted to GitHub. Each case records its current status.

## Test scope

- Automated suite: 28 passed with `uv run --extra dev pytest -q` on the final checked working tree.
- Codex CLI 0.153.4: QA resume extraction, Quizzer project drafting, application drafting, and custom form answers succeeded. Pronunciation Assessment project drafting failed validation on a six-bullet response.
- GitHub profile `longnt27`: 32 public repositories listed; `quizzer` and `pronunciation-assessment` inspected from isolated QA data.
- Visual checks: Home, Jobs, Applications, Profile, Projects, Sources, Employers, and generated A4 PDFs.
- No real application, email, or Telegram alert was sent. External social sign-in and live SMTP were not exercised.

## Cases

| ID | Severity | Issue |
| --- | --- | --- |
| [001](001-false-web-submission-confirmation.md) | Critical | False web submission confirmation when browser validation blocks the form |
| [002](002-email-config-lockout.md) | High | Missing SMTP configuration creates a permanent duplicate-send lock |
| [003](003-stale-application-url.md) | High | Vacancy merge and refresh retain stale application URLs |
| [004](004-required-radio-group.md) | High | Required radio group rejects the unselected choice |
| [005](005-resume-uploaded-to-all-file-fields.md) | High | Resume is uploaded to every PDF-compatible file field |
| [006](006-scores-not-recomputed.md) | High | Profile edits leave existing job match scores stale |
| [007](007-irrelevant-sales-high-score.md) | Medium | Obvious non-target role receives a high match score |
| [008](008-failed-scan-reported-complete.md) | Medium | Source scan failure is shown as Scan complete |
| [009](009-old-career-source-remains-enabled.md) | Medium | Changing an employer career URL keeps the old source enabled |
| [010](010-career-source-apply-url-missing.md) | Medium | Career collector never records application destinations |
| [011](011-failed-telegram-alert-not-retried.md) | Medium | Transient Telegram failure loses a new-job alert |
| [012](012-application-review-incomplete-resume-editing.md) | Medium | Application review cannot edit all content in the generated resume |
| [013](013-job-search-field-collapses.md) | Medium | Jobs search input collapses to a few pixels |
| [014](014-cpp-search-fts-error.md) | Medium | Literal C++ job search returns an FTS syntax error |
| [015](015-empty-search-retains-detail.md) | Medium | Empty job search keeps an unrelated selected job visible |
| [016](016-cards-keyboard-inaccessible.md) | Medium | Job and application cards are not keyboard accessible |
| [017](017-stale-destination-warning.md) | Medium | Destination warning remains after a valid destination is saved |
| [018](018-codex-project-schema-overflow.md) | Medium | Codex project drafting fails when it returns six bullets |
| [019](019-raw-readme-html-project-placeholder.md) | Medium | Failed project generation exposes raw README HTML as an approvable bullet |
| [020](020-form-control-text-unintentionally-bold.md) | Low | Form controls inherit bold label weight and reduce readability |
| [021](021-repository-results-hide-selected-projects.md) | Medium | Repository results push project review thousands of pixels below the search |
| [022](022-telegram-empty-token-server-error.md) | Low | Saving Telegram settings without a token returns a server error |
| [023](023-vietnamese-name-fields-reversed.md) | Medium | Automatic first and last name answers split Vietnamese names incorrectly |
| [024](024-interview-rejected-offer-states-inaccessible.md) | Medium | Interview, Rejected, and Offer states have no UI controls |
