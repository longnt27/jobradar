# Job Radar

A local job discovery and application assistant for macOS.

## Install

From this project folder, run one command:

```sh
./install.sh
```

The installer sets up the Python app and its browser, starts background scanning at login, and opens [Job Radar](http://127.0.0.1:8787/#home). If needed, it installs [uv using Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/). Re-running `./install.sh` updates the app and restarts the background service.

## Set up in the app

The browser workspace has four main places: **Home**, **Jobs**, **Applications**, and **My profile**. Home shows the next useful action. Work history and GitHub projects open from My profile; sources and employers open from Jobs.

My profile guides you through the one-time personal steps:

1. Choose a drafting provider once. You can change it later in My profile. The app shows whether each provider is installed and whether it uses a local or remote model.
2. Upload a text-based resume PDF. The selected provider extracts contact details, previous positions, education, achievements, and skills. Review the result in My profile and Work history. You can also paste a LaTeX resume. A remote provider receives the extracted PDF text; Codex OSS with Ollama runs locally.
3. Open **Connections and notifications** in My profile if you also want LinkedIn searches or Facebook groups. The 30 company career feeds work without social sign-in. Enabled sources are checked every four hours while your Mac is on.
4. Open **GitHub projects** from My profile and select repositories. The saved provider drafts each project description from repository files and history; edit and approve it before it can appear on a resume.
5. Optionally add email application or Telegram alert settings under **Connections and notifications**.

The registry contains more than 200 employers, including Vingroup entities, banks, Viettel, VNPT, FPT, CMC, and technology companies. The app starts with 30 verified company career feeds, listed in [CAREER_FEEDS.md](CAREER_FEEDS.md). Some companies use a dedicated recruiting portal hosted by an application platform; those feeds still point to the company's own job listings and direct postings. An employer appears as actively scanned only when it has an enabled source. Add a career page from its employer card.

## Apply

Open a job and click **Prepare application**. The provider saved in Profile drafts the application. Codex CLI, Antigravity, and Claude Code use remote inference through CLIs running on your Mac. Codex OSS with Ollama uses a local model.

For each job, the drafting provider selects approved projects and tailors their bullets to the job description. Previous positions are edited in **Work history**. The A4 PDF uses the supplied layout: header and summary, Experience, Selected Projects, Education, Achievements, and Skills.

Review the tailored project bullets, PDF resume, destination, message, and form answers. Save any edits and inspect the updated PDF. **Send application** submits exactly the package shown on screen. Email applications use the SMTP details saved in Profile. Supported single-page forms are filled and submitted through the local browser profile. The app records the outcome and blocks duplicate sends when completion is uncertain.

## Current limits

- The 30 initial feeds cover verified company career listings; some may have no currently matching roles. A successful empty scan does not mean the company has no jobs anywhere.
- Other career pages and stable single-page application forms work best when their job links are present in the page HTML. JavaScript-only boards, multi-step forms, CAPTCHA, verification, and changed form fields can need manual attention.
- The employer registry is broader than the initial feeds. A registry entry alone does not mean live scan coverage.
- The baseline ranking is deterministic; embeddings are not yet included.
- Repository inspection reads selected files and history without running repository code. You must confirm what you personally contributed.

## Development

`uv run pytest -q` runs the tests. Data is stored in `~/Library/Application Support/JobRadar` by default. Set `JOB_RADAR_DATA_DIR` or `JOB_RADAR_PORT` to change the location or local port. The macOS background service can be removed with `uv run job-radar uninstall-service`.
