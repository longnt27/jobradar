# Job Radar

A local job discovery and application assistant for macOS.

## Install

From this project folder, run one command:

```sh
./install.sh
```

The installer sets up the Python app, its browser, and Ollama, starts background scanning at login, and opens [Job Radar](http://127.0.0.1:8787/#home). If needed, it installs [uv using Astral's official installer](https://docs.astral.sh/uv/getting-started/installation/) and [Ollama using its official installer](https://ollama.com/download/mac) (or Homebrew when available). Choose or download a text model inside My profile. Re-running `./install.sh` updates the app and restarts the background service.

## Set up in the app

The browser workspace has four main places: **Home**, **Jobs**, **Applications**, and **My profile**. Home shows the next useful action. Work history and GitHub projects open from My profile; sources and employers open from Jobs.

My profile guides you through the one-time personal steps:

1. Choose a drafting provider once. You can change it later in My profile. The app shows whether each provider is installed and whether it uses a local or remote model.
2. Upload a text-based resume PDF. The selected provider extracts contact details, previous positions, education, achievements, and skills. Review the result in My profile and Work history. You can also paste a LaTeX resume. A remote provider receives the extracted PDF text; Codex OSS with Ollama runs locally.
3. Choose a separate **local job matching model** in My profile. Job Radar lists installed Ollama text models and can download a small recommended model from that step. The selected model extracts job requirements and scores ten fit criteria from 1–10 against your skills, work history, and approved projects. Jobs are analyzed in the background, newest first. The job list says **Analyzing** until the local score is ready; completed scores use colored badges. This choice does not change the provider used to draft resumes and applications.
4. Use **Connect LinkedIn and Facebook** in My profile. Each site has its own sign-in button. Click one and complete sign-in in the regular Chrome window (including Google sign-in if you use it). Job Radar detects the signed-in account page, saves the session, closes the sign-in window, and brings the app forward automatically. Repeat for the other site if you use it. Job Radar keeps a separate Chrome profile for these sessions and reuses it for four-hour scans. If a site expires its session, Job Radar shows a local notification and a sign-in-again banner; only that site's scans pause until you reconnect. The 43 company career feeds work without social sign-in. Google Chrome must be installed for social sign-in and scans.
5. **Telegram reviews and job alerts** is a visible step in My profile. Create a bot with BotFather, message it in a private chat, use **Find my chat ID**, and set the alert score. The token stays in a restricted local file. New jobs are alerted after local matching completes. Application reviews include the full drafted details and resume PDF.
6. Open **GitHub projects** from My profile and select repositories. The saved drafting provider describes each project from repository files and history; edit and approve it before it can appear on a resume or influence matching.
7. Optionally add email application settings under **Email application settings**.

The registry contains 181 employers after removing those [based in HCMC](EMPLOYER_SCOPE.md), including Vingroup entities, banks, Viettel, VNPT, FPT, CMC, and technology companies. The app starts with 43 verified direct company career feeds, listed in [CAREER_FEEDS.md](CAREER_FEEDS.md). Some companies use a dedicated recruiting portal hosted by an application platform; those feeds still point to the company's own job listings and direct postings. An employer appears as actively scanned only when it has an enabled source. Add a career page from its employer card.

## Apply

Open a job and click **Prepare application**. The provider saved in Profile drafts the application. Codex CLI, Antigravity, and Claude Code use remote inference through CLIs running on your Mac. Codex OSS with Ollama uses a local model.

For each job, the drafting provider selects approved projects and tailors their bullets to the job description. Previous positions are edited in **Work history**. The A4 PDF uses the supplied layout: header and summary, Experience, Selected Projects, Education, Achievements, and Skills.

Review the tailored project bullets, PDF resume, destination, message, and form answers. Save any edits and inspect the updated PDF. **Approve & send** submits exactly the saved package. Email applications use the SMTP details saved in Profile. Supported single-page forms are filled and submitted through the local browser profile. The app records the outcome and blocks duplicate sends when completion is uncertain.

In **Applications**, you can enable **Automatic draft preparation** and choose a score threshold. When enabled, only jobs discovered afterward with a completed local score strictly above that threshold are considered. Job Radar prepares each draft, inspects a web form when possible, and sends the complete details and resume PDF to your configured private Telegram chat. It waits for your approval before sending any application. Telegram has **Approve & send**, **Edit**, and **Regenerate** buttons; Regenerate asks you to reply with custom instructions. Edit opens the exact draft in Job Radar on this Mac. The same actions are available in Applications. A changed draft invalidates the previous Telegram approval button and receives a new review message. Missing destinations, unavailable email settings, and unanswered required fields appear as needing changes. Automatic preparation starts off by default; enabling it excludes the existing job backlog.

## Current limits

- The 43 built-in feeds cover verified company career listings; some may have no currently matching roles. A successful empty scan does not mean the company has no jobs anywhere.
- Built-in browser adapters cover some JavaScript-rendered career boards. Other career pages and stable single-page application forms work best when their job links are present in the page HTML. Multi-step forms, CAPTCHA, verification, and changed form fields can need manual attention.
- The employer registry is broader than the initial feeds. A registry entry alone does not mean live scan coverage.
- Local extraction can still make mistakes. Job Radar discards some unsupported fields, shows the original posting beside the summary, and lets you rerun failed analyses. The 1–10 criterion scores are judgment calls, not calibrated probabilities. Existing jobs are analyzed sequentially after a model is selected, so a large backlog may take time.
- Repository inspection reads selected files and history without running repository code. You must confirm what you personally contributed.

## Development

`uv run pytest -q` runs the tests. Data is stored in `~/Library/Application Support/JobRadar` by default. Set `JOB_RADAR_DATA_DIR` or `JOB_RADAR_PORT` to change the location or local port. The macOS background service can be removed with `uv run job-radar uninstall-service`.
