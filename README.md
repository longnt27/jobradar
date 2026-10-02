# Job Radar

A local job discovery and application assistant for macOS.

## Install

From this project folder, run one command:

```sh
./install.sh
```

The installer sets up the Python app and its browser, starts background scanning at login, and opens [Job Radar](http://127.0.0.1:8787/#setup). If needed, it installs [uv using Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/). Re-running `./install.sh` updates the app and restarts the background service.

## Set up in the app

The **Setup** screen guides you through the remaining personal steps:

1. Add your name, email, skills, and preferences in **Profile**.
2. Click **Open sign-in browser**, sign in to LinkedIn and Facebook, then click **I've finished signing in**. Job Radar saves that browser session locally. The first scan verifies access.
3. Add the Facebook groups you want scanned. The app already has 27 LinkedIn searches. Every enabled source is checked every four hours while your Mac is on.
4. Add experience and select GitHub repositories in **Experience**. Review and approve any project claim before the app uses it in a resume.
5. Optionally expand **Email applications** or **Telegram alerts** and save those settings in the UI.

The registry contains 216 employers, including Vingroup entities, banks, Viettel, VNPT, FPT, CMC, and technology companies. An employer appears as actively scanned only when it has an enabled source. Add a career page from its employer card.

## Apply

Open a job, choose a drafting provider, and click **Prepare application**. Codex CLI is the default and uses remote inference through a CLI running on your Mac. Antigravity and Claude Code are also available when installed. Codex with Ollama uses a local model; the local template uses no model. The app displays the selected mode.

Review the tailored PDF resume, destination, message, and form answers. Save any edits and inspect the updated PDF. **Send application** submits exactly the package shown on screen. Email applications use the SMTP details saved in Setup. Supported single-page forms are filled and submitted through the local browser profile. The app records the outcome and blocks duplicate sends when completion is uncertain.

## Current limits

- Static career pages and stable single-page application forms work best. JavaScript-only boards, multi-step forms, CAPTCHA, verification, and changed form fields can need manual attention.
- The employer registry is broad, but most career URLs still need to be added. A registry entry alone does not mean live scan coverage.
- The baseline ranking is deterministic; embeddings are not yet included.
- Repository inspection reads selected files and history without running repository code. You must confirm what you personally contributed.

## Development

`uv run pytest -q` runs the tests. Data is stored in `~/Library/Application Support/JobRadar` by default. Set `JOB_RADAR_DATA_DIR` or `JOB_RADAR_PORT` to change the location or local port. The macOS background service can be removed with `uv run job-radar uninstall-service`.
