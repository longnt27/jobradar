<img src="job_radar/static/logo.svg" alt="Job Radar logo" width="72" height="72">

# Job Radar

Job Radar finds jobs, analyzes how well they fit your experience, and prepares tailored applications for your review. It runs as a browser interface on your Mac, keeps its database and browser sessions locally, and continues scanning in the background after you close the tab.

**Platform:** macOS · **Local address:** [http://127.0.0.1:8787](http://127.0.0.1:8787) · **Sending rule:** an application is sent only when you choose **Approve & send** in Job Radar or approve its current draft in Telegram.

## Install

You need a Mac and an internet connection for installation and job collection. From a checkout of this repository, run one installer command:

~~~sh
./install.sh
~~~

If you do not have the repository yet:

~~~sh
git clone git@github.com:longnt27/jobradar.git
cd jobradar
./install.sh
~~~

The installer installs uv and Ollama if needed, installs the locked Python dependencies and Playwright Chromium, registers a macOS service that starts at login, waits for the app to respond, and opens Job Radar in your browser. Re-run `./install.sh` from the same checkout after pulling updates. The installer does not install or sign in to an AI drafting CLI; choose one during setup below. Google Chrome is needed only if you want LinkedIn or Facebook scanning.

If the browser does not open, visit [http://127.0.0.1:8787](http://127.0.0.1:8787) yourself. The service runs on this Mac; that address is not a hosted website.

## Set up your profile

Open **My profile** and work through its numbered steps. You can return to any step later. The first three steps enable tailored resumes and local job scores; the connection steps are optional.

1. **Choose your AI provider.** Install and sign in to one supported CLI before selecting it: Codex CLI, Antigravity CLI, or Claude Code CLI. Codex OSS uses the `codex` CLI with Ollama for local inference. The app checks whether the selected command is available on your Mac. Remote providers receive the resume text and job details needed for drafting; Codex OSS uses a local model.
2. **Import your resume.** Upload a text-based PDF and click **Extract resume details**. You can also paste the supported LaTeX resume layout. Review the extracted information: open **Personal details** for contact information, education and skills, and **Work history** for previous positions. These are separate editing screens. GitHub projects are added separately; importing a resume does not select projects for you.
3. **Choose a local job matching model.** Select an installed Ollama text model, or download the recommended small model from the step. Job Radar extracts requirements such as skills, experience, location and responsibilities, then scores ten fit criteria from 1–10. The Jobs screen shows **Analyzing** until a score is ready. If an analysis fails, open **Jobs → Local job analysis** to see the failure and retry it. Scores are aids to review, not hiring probabilities.
4. **Connect LinkedIn and Facebook, if wanted.** Use the separate sign-in buttons. Sign in through the regular Chrome window that Job Radar opens. It detects completion, closes that window and reuses the saved session for later scans. If a session expires, the app shows a sign-in-again prompt and pauses scans for that site. Company career feeds work without these accounts.
5. **Connect Telegram, if wanted.** Create a bot with [BotFather](https://t.me/BotFather), send the bot a private message, enter its token and use **Find my chat ID**. Set the minimum score for job alerts. Telegram can deliver application drafts and their resume PDFs for review, with **Approve & send**, **Edit**, and **Regenerate** actions. Keep the bot token private.
6. **Connect email, if wanted.** For Gmail, enable [Google 2-Step Verification](https://support.google.com/accounts/answer/185839?hl=en), [create an app password](https://support.google.com/accounts/answer/185833?hl=en), and click **Use Gmail settings**. Check the full Gmail address in **SMTP username** and **From address**, paste the app password in **Password or app password**, then save. The preset uses `smtp.gmail.com` and port `465`. Use the app password, not your normal Google password. Other SMTP providers can be entered manually. Email settings are needed to send email applications; web forms do not need them.

The app saves these choices. **Configured** means settings are saved; it does not mean an SMTP login or delivery has been tested.

## Find and review jobs

- **Home** shows your next steps, counts and recent scan activity.
- **Jobs** shows postings, their original source links, extracted facts, fit scores and the local analysis queue. Open a job to read its original description and requirements. You can also paste a job description with **Add a job from a description**.
- **Jobs → Sources** lists the feeds actually configured to scan. Pause, enable or scan an individual source there. **Jobs → Employers** is a broader directory; an employer is actively scanned only when it has an enabled source. Add its direct career page from the employer card.

The app includes [43 direct company career feeds](CAREER_FEEDS.md), which are checked on a four-hour schedule, plus LinkedIn searches that require sign-in. You can add Facebook groups and further direct career pages. The [employer directory](EMPLOYER_SCOPE.md) is larger than the active feed list; an employer entry alone does not mean its jobs are being collected. A completed empty scan means the adapter found no matching posting at that time.

## Add projects and prepare an application

1. In **My profile → GitHub projects**, enter your GitHub username to browse public repositories, or paste a repository URL. Select a repository and let the chosen provider draft a project description from its files and history. Review and approve the description before Job Radar can use it in a resume or job match. Confirm the claims describe your own contribution.
2. Open a job in **Jobs** and click **Prepare application**. The drafting provider selects relevant approved projects, adapts their resume bullets to the job, and prepares a resume PDF plus an email message or form answers. Previous jobs remain in the Experience section of the resume.
3. Open the draft in **Applications**. Check the job, destination, resume PDF, project bullets, message, attachments and any form answers. Use **Edit details** or **Regenerate draft** with your own instructions, then save and review the updated PDF. Use **Inspect form** for a web application when available.
4. Click **Approve & send** only after the saved package is correct. Email applications use the SMTP settings in My profile. Supported single-page forms can be filled and submitted through the browser. Missing destinations or required answers appear as blockers. Job Radar records the result and guards against duplicate sends when the outcome is uncertain.

In **Applications → Automatic draft preparation**, you can enable drafting for **newly discovered** jobs whose completed score is **strictly above** your chosen threshold. Existing jobs are excluded when you turn it on. Each draft still waits for your review and approval; enabling this feature does not send applications automatically. If Telegram is configured, the current draft and resume PDF are sent to your private chat for review. Editing a draft invalidates its earlier Telegram approval button.

## Keep it running, update, or remove the service

Job Radar starts at macOS login after installation. Keep the checkout in place: the service runs the Python environment installed there. To open the interface later, visit [http://127.0.0.1:8787](http://127.0.0.1:8787).

| Task | Command from the checkout |
| --- | --- |
| Apply an updated checkout and restart the service | `./install.sh` |
| Restart the service without reinstalling dependencies | `.venv/bin/job-radar install-service` |
| Stop and remove the login service | `.venv/bin/job-radar uninstall-service` |
| Run the app in the foreground for debugging | `.venv/bin/job-radar serve` |

The default data directory is `~/Library/Application Support/JobRadar`. It contains the SQLite database, saved Chrome profile, generated PDFs, SMTP and Telegram settings, and service logs. Removing the login service does **not** delete that data. The installer also leaves installed dependencies in the checkout.

Set `JOB_RADAR_DATA_DIR` or `JOB_RADAR_PORT` when running `./install.sh` to change the persistent data directory or local port. The installer writes those values into the service definition. To use the new port later, open `http://127.0.0.1:<port>`.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The app does not open after installation | Check `~/Library/Application Support/JobRadar/service.stderr.log`, then re-run `./install.sh`. If you set `JOB_RADAR_DATA_DIR`, look for the log there instead. |
| Jobs stay at **Analyzing** or show failures | Make sure Ollama is running and the selected model is installed. Open **Jobs → Local job analysis** to retry failed jobs; change the model in **My profile** if needed. |
| A company has no visible jobs | Check **Jobs → Sources** for an enabled feed and its last scan result. The employer directory includes leads without feeds. Open the original career page to compare results. |
| LinkedIn or Facebook needs sign-in again | Open **My profile → Connect LinkedIn and Facebook** and sign in again for the affected site using Chrome. |
| Gmail rejects the password | Use a Google app password after enabling 2-Step Verification. Some managed or protected Google accounts do not offer app passwords; see [Google's instructions](https://support.google.com/accounts/answer/185833?hl=en). |
| A form cannot be sent | Open its application draft, inspect the form and fill missing required answers. Multi-step forms, CAPTCHA and changed site fields may need manual completion on the original posting. |

## Data and limitations

Job Radar listens on `127.0.0.1` and has no separate login screen. Keep it on your Mac; do not expose its port to a network. SMTP and Telegram credentials are saved in local files with owner-only permissions. The database, browser sessions and generated artifacts are also local, but they are **not encrypted by Job Radar**. Back up the data directory if you need to preserve your profile and drafts.

The selected remote AI provider may receive resume text, job descriptions and approved project facts when it drafts content. Telegram receives the draft and PDF when you enable that connection. Local job matching uses Ollama on your Mac. Repository inspection reads selected files and history without running repository code.

Career sites can change, and the supported adapters do not cover every posting or application form. Always check the original listing and your generated application before sending.

## Development

The app requires Python 3.12 or newer. The installer uses the checked-in `uv.lock` for repeatable dependency installation. To run the test suite from the checkout:

~~~sh
uv run --extra dev pytest -x -vv
~~~

Useful implementation references: [feed catalog](job_radar/feed_catalog.py), [collectors](job_radar/collectors.py), [local analysis](job_radar/local_analysis.py), and [web app](job_radar/web.py).
