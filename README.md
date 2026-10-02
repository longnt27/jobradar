# Job Radar

Job Radar is a local web app for discovering AI roles, reviewing evidence from your work, drafting tailored applications, and sending one reviewed application at a time. Data is stored on your Mac in `~/Library/Application Support/JobRadar` by default.

## Start

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), Git, and macOS for the background service.

```sh
uv sync --extra dev
uv run playwright install chromium
uv run job-radar serve
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). Complete **Profile**, then add and approve experience in **Experience**. Enter a GitHub username to choose public repositories to inspect, or enter a repository URL directly. Repository claims remain unapproved until you edit and approve them.

The initial registry contains more than 200 employers, including Vingroup entities, Vietnamese banks, Viettel, VNPT, FPT, CMC, fintech, software, and global technology companies. Registry membership is separate from scan coverage. A verified VinAI careers source and 27 LinkedIn job searches are seeded. Add other career URLs on an employer card, and add Facebook group URLs in **Sources**. Each enabled source is checked every 240 minutes while the app runs. The app records failed and empty scans separately.

## Browser sign-in and background scans

Stop the server, then run:

```sh
uv run job-radar login
```

Sign in to LinkedIn and Facebook in the browser window, then press Enter in the terminal. The app reuses this local browser profile. Add Facebook group URLs in **Sources**; no groups are preselected because access depends on your account. Restart the server, or install a macOS LaunchAgent:

```sh
uv run job-radar install-service
```

The service starts at login and keeps the four-hour schedule active with the browser window closed. `uv run job-radar uninstall-service` removes it. The service runs from this checkout's virtual environment, so keep the checkout at its current path. Logs are in the data directory. Stop the service before running `job-radar login`, because Chromium cannot share a profile with another running process.

## Prepare and send

Open a job, choose a drafting provider, and click **Prepare application**. The default local template does not call a model. `codex_local` invokes Codex OSS with an Ollama model on the Mac; `codex`, `agy`, and `claude` invoke installed local CLIs that may send the job and selected evidence to remote models. The app labels the chosen mode. Provider errors leave the job unsent.

The draft selects approved evidence, builds a text-readable PDF resume, drafts a message, and tries to inspect the destination form. Review the PDF, destination, message, and every answer. Save edits and review the regenerated PDF. **Send application** passes the displayed package fingerprint to the server; a changed package is rejected. Email sends through configured SMTP. A supported web form is filled and submitted in the saved browser profile. The app records confirmation text or an unresolved status and prevents duplicate sending when a send may have completed.

To configure email submission, run `uv run job-radar configure-smtp`. It prompts for SMTP host, port, username, app password, and From address. Credentials are saved in `smtp.json` inside the app data directory with owner-only file permissions, so the background service can access them. Environment overrides `JOB_RADAR_SMTP_HOST`, `JOB_RADAR_SMTP_PORT`, `JOB_RADAR_SMTP_USER`, `JOB_RADAR_SMTP_PASSWORD`, and `JOB_RADAR_SMTP_FROM` are also supported. You can set `JOB_RADAR_GITHUB_TOKEN` to raise GitHub API limits for public repository discovery.

For optional Telegram alerts, create a bot, get your chat ID, and run `uv run job-radar configure-telegram`. The token and chat ID are saved in an owner-only file in the app data directory. Only newly found jobs at or above the profile's alert score are sent. Alerts contain title, company, score, location, and a source or application link. `JOB_RADAR_TELEGRAM_TOKEN` and `JOB_RADAR_TELEGRAM_CHAT_ID` can override the saved settings.

## Current limits

- Career-page scanning handles static links and descriptions; JavaScript-only boards and some applicant tracking systems need dedicated adapters.
- Form submission supports a stable single-page form. Changed fields, extra steps, CAPTCHA, verification, and unknown required answers need manual attention. A submit click without a clear receipt is marked unconfirmed, and the app will not automatically retry it.
- The baseline ranking is deterministic. The current build has no embeddings or automatic discovery of every employer's career URL. The employer registry shows which companies have an active source.
- Repository inspection reads the selected repository's README, manifests, file list, and recent commits. It does not run repository code or infer your personal contribution; you approve that claim.

## Development

```sh
uv run pytest -q
```

Set `JOB_RADAR_DATA_DIR` to use a separate data directory, and `JOB_RADAR_PORT` to change the local port.
