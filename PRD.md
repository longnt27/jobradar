# Job Radar Product Requirements Document

**Status:** Draft for implementation  
**Date:** 2 October 2026  
**Owner:** Personal use  
**Target machine:** Mac mini M4 with 16 GB unified memory

## Product summary

Job Radar is a local job discovery and assisted application system for AI and adjacent engineering roles. It checks LinkedIn job searches and selected Facebook groups every four hours, scans a broad catalog of employer career sites, combines repeated sightings of the same vacancy, and ranks opportunities against a personal candidate profile. For a job the user wants, it builds a tailored resume, message, and form answers from verified experience and repository evidence. The user reviews the complete application and clicks Send; Job Radar then fills and submits the application and records the outcome.

The employer registry has no fixed size limit. The initial registry includes the Vingroup technology ecosystem, Viettel, VNPT, FPT, CMC, Vietnamese banks, major financial technology firms, research groups, global technology employers with Vietnam operations, and remote friendly employers. New employers found in job posts or supplied by the user enter the same registry. The number of enabled sites is governed by the Mac mini's capacity and measured scan time, not an artificial product cap.

The database, ranking, scheduling, repository index, and application state run on the Mac mini. A strict local inference mode uses on-device models. An optional connected agent mode invokes installed Codex, Antigravity (`agy`), or Claude Code CLIs from the Mac for stronger drafting; their default proprietary models may process content remotely and may use plan allowances or incur charges. The product must identify the selected mode before sending candidate or job content to any model provider. No cloud database or hosted application server is required. Telegram is an optional external notification channel, so notification text leaves the Mac mini when that channel is enabled.

## Product decisions and assumptions

| Area | Decision |
| --- | --- |
| LinkedIn cadence | One complete configured search cycle every four hours, six times per day |
| Facebook cadence | One complete configured group cycle every four hours, six times per day |
| Career site cadence | Four hours by default; configurable by employer and adapter |
| Clock | Scheduled cycle starts at 00:00, 04:00, 08:00, 12:00, 16:00, and 20:00 Asia/Ho_Chi_Minh |
| Collection | Browser first for LinkedIn and Facebook; use the most reliable available page or feed adapter for employer sites |
| Browser account | Dedicated persistent Chromium profile, signed in manually by the user |
| Actions on source sites | Collect jobs and, after the user reviews and clicks Send, fill and submit the selected application |
| Role focus | AI research, applied AI, ML, LLM, computer vision, data and platform roles adjacent to AI |
| Geographic focus | Hanoi, Vietnam wide roles, and remote roles compatible with a Vietnam based candidate |
| Processing | Rules, multilingual embeddings, a local small model, and an optional larger judge for uncertain cases |
| Application drafting | Verified experience and repository evidence → job-specific resume, message, and form answers |
| Drafting providers | Configurable Codex, Antigravity, Claude Code, or strictly local model adapter; expose data destination and usage mode |
| Submission authority | One explicit Send action per reviewed application and destination; no unattended bulk sending |
| Interface | Telegram alerts and daily digest first; local search and review view for history |

These are product requirements, not guarantees that a source will expose every post. Source interfaces, feed ordering, authentication, and employer sites can change. The product must record what it actually scanned and expose coverage gaps instead of implying complete visibility.

## Users and jobs to be done

The primary user is one candidate. They want to discover promising positions early, spend less time checking sources, and understand why a job deserves attention. The system must support Vietnamese and English listings and mixed language posts.

The user can:

1. Define a candidate profile, preferred roles, locations, skills, exclusions, and employer preferences.
2. Configure LinkedIn searches, Facebook groups, and an unrestricted employer watchlist.
3. Receive timely high value alerts and a concise daily digest.
4. Open the original sources and the preferred application link.
5. Supply experience, resume history, a GitHub account, and selected repositories; verify what the system says the user personally did.
6. Review a tailored resume, message, destination, attachments, and every form answer, edit them, and click Send once.
7. Let the system populate and submit the reviewed application, then inspect its receipt or a precise failure state.
8. Mark results as Interesting, Ignore, Prepare, Ready, Applied, Interview, Rejected, or Offer.
9. Search past jobs, inspect why a decision was made, and undo an incorrect merge or dismissal.

## Goals and exclusions

### Goals

- Run the configured LinkedIn and Facebook cycles every four hours while the Mac mini is available and authenticated.
- Capture new relevant jobs from the widest practical employer catalog, including every named seed in the appendix.
- Preserve source evidence and distinguish a job's published time from the time Job Radar first observed it.
- Reduce duplicate and irrelevant items while retaining realistic stretch opportunities.
- Explain each surfaced result with matched skills, gaps, experience interpretation, work location, freshness, and source links.
- Keep stored candidate data local and make the inference destination explicit; strict local mode keeps inference local as well.
- Build job-specific application packages grounded in approved experience and repository evidence.
- Submit each package after the user's explicit review and Send action, with duplicate protection and proof of outcome.
- Make failures visible so the user knows when a source has stopped yielding results.

### Exclusions for the first release

- Unattended bulk applications, unsolicited recruiter outreach, and connection requests. An email sent in response to a specific reviewed job is an application and remains in scope.
- LLM fine tuning or a learned ranking model before useful feedback data exists.
- A large multiuser dashboard or hosted web service.
- A promise that every company has a public career page or that every public post is visible in a given scan.

## Functional requirements

### Candidate profile and search configuration

**PR 01.** Store the profile in versioned local configuration. Fields include resume text or structured experience, projects, skills with proficiency and evidence, desired role families, seniority tolerance, salary preference if known, work mode, languages, preferred companies, excluded companies, and negative preferences such as pure operations or sales roles.

**PR 02.** Separate hard constraints from preferences. A Hanoi only onsite location outside the user's radius may be hard excluded; a nominal three year experience requirement is a soft factor unless the user explicitly changes it. Missing location or compensation must remain unknown rather than being inferred.

**PR 03.** Support English and Vietnamese role synonyms, including AI Engineer, Applied AI Engineer, Machine Learning Engineer, ML Scientist, Research Engineer, AI Researcher, LLM Engineer, Generative AI Engineer, Agent Engineer, Computer Vision Engineer, NLP Engineer, Data Scientist, MLOps Engineer, Robotics AI Engineer, Perception Engineer, and Vietnamese equivalents. A role family can be enabled or disabled without code changes.

**PR 04.** Configuration changes must be validated before the next scan. The user can preview the effective search matrix and enabled company count.

### LinkedIn collection

**PR 05.** Run a full scan every four hours. A scan consists of every enabled search template and location combination. The initial matrix covers Hanoi, Vietnam, and Vietnam compatible remote roles, with recent job filters where available. Search templates must avoid needless duplicate queries while retaining broad role coverage.

**PR 06.** Record each visible result's stable job identifier when present, title, company, location, work mode, displayed posting age or date, URL, recruiter information when visible, application URL, and description. Capture the raw text and a parser version. Do not fabricate fields that are hidden or absent.

**PR 07.** Check the result list and detail view where required to obtain the description. Pagination and scrolling stop when the configured depth is reached or when the collector reaches a known observation boundary. The stop condition and result count are logged for every query.

**PR 08.** Continue the remaining queries after one query fails. If authentication expires or a verification challenge appears, stop that source, record `needs_user_attention`, and resume after manual account recovery. Never silently report a successful empty scan in this state.

### Facebook group collection

**PR 09.** Run a full scan of all enabled groups every four hours. Store the group identifier, visible ordering mode, number of posts inspected, newest and oldest visible post timestamps, and scan result.

**PR 10.** For each newly observed post, retain post URL or stable identifier, group, author display name when visible, publication timestamp when visible, text, links, contact details included in the post, and a reference to images or attachments. Extract text from images only when needed for a likely job post and when local OCR can do so.

**PR 11.** Classify whether a post is recruitment related before full job extraction. Distinguish direct employer posts, recruiter posts, referrals, reposts, job seeking posts, events, training ads, and unrelated content. Job seeking and unrelated posts are excluded from vacancy creation but may remain as lightweight observations for duplicate scan control.

**PR 12.** When a group offers chronological ordering, use it. If ordering cannot be trusted, scan to a configured depth and report reduced coverage confidence. A post that is absent from the rendered feed cannot be treated as observed.

**PR 13.** Keep the original post link even when an external application link exists. For private groups, avoid forwarding the full post text to Telegram by default; send a short locally generated summary and the source link.

### Employer career collection

**PR 14.** Maintain an employer registry with canonical name, aliases, parent organization, subsidiary or brand relationship, sector tags, geography, career page URL, adapter type, scan interval, last verified date, active status, and owner notes. A missing career URL is a discovery task, not a reason to delete the employer.

**PR 15.** Support reusable adapters for common applicant tracking systems and structured feeds, plus generic rendered page and custom site adapters. Favor a direct employer career listing or public structured feed when available. Capture original employer job IDs, detail URLs, apply URLs, dates, location, description, and department.

**PR 16.** The registry has no hard cap. A background discovery process proposes new employers from LinkedIn/Facebook company fields, outbound application domains, company announcements, and manually supplied names. New records are marked `candidate` until their identity and career source are verified. The user can promote, disable, merge, or alias them.

**PR 17.** Maintain a separate `coverage_status` for each employer: `active_scan`, `source_discovery`, `manual_only`, `temporarily_unavailable`, or `retired`. A broad roster must not be confused with broad live coverage. Show the count in each state.

**PR 18.** Scan every active employer on its configured interval, four hours by default. Spread scans over the four hour window so hundreds of sites do not all load at once. Each company retains a four hour due time even if its actual start is staggered. Record overdue sites.

### Normalization and deduplication

**PR 19.** Keep immutable source observations separate from canonical vacancies. A vacancy can have multiple LinkedIn listings, Facebook posts, and employer pages. Record `first_seen_at`, `last_seen_at`, `source_published_at`, and `source_updated_at` separately, with timestamp confidence and timezone.

**PR 20.** Normalize employer names through the registry and aliases. Normalize title, seniority, location, work mode, and role family without overwriting the raw source text. Historical names and acquisitions must retain their dates and relationships.

**PR 21.** Deduplicate in stages: exact source ID or canonical URL; employer plus title and location; then text and embedding similarity. Never merge solely on semantic similarity. Different seniority levels, offices, or requisition IDs can represent distinct vacancies. Low confidence matches become suggested merges.

**PR 22.** Preserve every source reference and the reason for a merge. A user can split a vacancy and mark two records as distinct. Prefer the direct employer application URL when it is current; otherwise display the available source links.

### Relevance extraction and ranking

**PR 23.** Apply deterministic rules to obvious exclusions and parse explicit fields such as years, salary, dates, links, and onsite requirements. Every exclusion gets a reason code. A rule must not discard a job merely because an experience number exceeds the candidate's years.

**PR 24.** Use a local multilingual embedding model for candidate to job similarity and duplicate candidates. Embedding cutoffs are conservative; borderline roles proceed to extraction rather than disappearing.

**PR 25.** Use a quantized local model near the 4B class to produce schema validated fields: role family, seniority, required and preferred skills, responsibilities, domain, likely experience rigidity, work mode, evidence snippets, and missing information. Treat all job text as untrusted data; instructions embedded in a posting must not control the system.

**PR 26.** Escalate uncertain, high potential cases to an optional model near the 9B class. It judges only bounded questions such as `HARD`, `STRONG`, `SOFT`, or `WISHLIST` for experience requirements and `STRONG_MATCH`, `REASONABLE_STRETCH`, `MAJOR_GAP`, or `IRRELEVANT` for fit. Models run sequentially if memory pressure requires it. A judge outage must not block the review queue.

**PR 27.** Produce a deterministic score from extracted evidence. Initial weights are role fit 25, skill and domain fit 25, experience feasibility 20, learning and research value 15, location and work mode 10, freshness 5. Keep score components and model version so old results remain explainable. Employer preference may break ties or be a separately displayed adjustment with a strict cap.

**PR 28.** Rank recently published jobs higher when all else is equal. If the publication date is unreliable, use first observed time and label it. A repost does not reset the underlying vacancy's original age, though it may add a new activity signal.

**PR 29.** Expose strong matches, stretch matches, and uncertain jobs separately. The system can cap notifications but cannot silently delete uncertain items. Feedback changes future ranking only after enough examples exist to evaluate whether it improves precision.

### Review, feedback, and notifications

**PR 30.** Send an immediate Telegram alert for new high scoring jobs after a successful scan. Group multiple sightings of the same vacancy into one alert. Support a daily digest at a user configured time and a quiet period.

**PR 31.** Each alert contains score, title, employer, location, work mode, publication or first seen age, top matches, main gap, experience interpretation with uncertainty, and source/application links. Telegram action buttons set Interesting or Ignore, or open Prepare in the local review view. Applied is set only after a confirmed submission or an explicit manual update. The local view also supports Interview, Rejected, and Offer.

**PR 32.** Store feedback with timestamp, prior state, and optional reason. Never treat Ignore as a universal negative: it may reflect timing, compensation, company preference, or a duplicate. Permit undo.

**PR 33.** Provide local search across titles, employers, descriptions, and notes using SQLite FTS5. Filters include source, company, role family, score, location, date, state, and coverage confidence. A small local web view or CLI is sufficient for the first release.

**PR 34.** Use Telegram Bot API long polling for button updates so the Mac mini needs no public endpoint. Store the bot token in the system keychain or a restricted local secret store, never in the repository.

### Operations and reliability

**PR 35.** Run under `launchd` with one scheduler and one work queue. Prevent overlapping scans of the same source. A missed scheduled run after sleep or reboot is recorded; the next available cycle starts promptly without pretending the missed run occurred.

**PR 36.** Track per source health: last attempted run, last successful run, results seen, new results, scan duration, parser failures, authentication state, and coverage confidence. Alert the user when a source has failed for two consecutive cycles or has produced an anomalous empty result relative to its recent baseline.

**PR 37.** Use bounded concurrency and timeouts. Isolate browser crashes, failed sites, and model errors so one source cannot halt the cycle. Queue model processing separately from collection; save raw observations before inference.

**PR 38.** Store the SQLite database in WAL mode. Keep daily backups, allow JSON or CSV export, and support schema migration and restore. Raw content retention is configurable; default to 90 days for raw post and page text, with structured vacancy history retained until the user deletes it.

**PR 39.** Use a dedicated automation browser profile rather than the default personal Chrome profile. Playwright's persistent context supports saved cookies but does not support automating the default Chrome user profile reliably; only one browser process may hold a profile directory at a time. [Playwright browser documentation](https://playwright.dev/docs/api/class-browsertype)

### Candidate evidence and repository intake

**PR 40.** Accept the user's existing resumes, structured employment and education history, personal project notes, publications, portfolio links, GitHub username, selected repository URLs, and optional local repository paths. Private repositories require explicit inclusion; a public GitHub profile does not authorize reading unrelated private repositories.

**PR 41.** Clone or update selected repositories into a dedicated read-only intake directory. Inspect README files, documentation, source tree, manifests, tests, public demos, and Git history. Do not run repository scripts or install their dependencies during evidence intake. Record repository URL, commit SHA, inspection time, license, and whether the project is personal, collaborative, forked, or employer owned.

**PR 42.** Produce a project evidence card for each repository: problem, target users, architecture, technologies, technical challenges, observable results, candidate contribution, links, and supporting file or commit references. Distinguish facts visible in code from the user's own claims and from model inference. A repository's existence does not prove that the user designed or implemented every part of it.

**PR 43.** Ask the user to approve, edit, or reject contribution and impact claims before any claim enters the application evidence library. Quantified outcomes require a source or user confirmation. Preserve approved wording and provenance, including which user supplied statement or repository revision supports it.

**PR 44.** Reindex repositories when their selected branches change, but do not silently change previously approved personal contribution claims. Show a diff and request review when new evidence conflicts with an approved claim.

### Application drafting

**PR 45.** On Prepare, analyze the job description into required skills, preferred skills, responsibilities, seniority, domain, location, application method, and questions. Select the most relevant approved experience and projects by evidence match, recency, and role fit. Show why each project was selected and which relevant projects were left out.

**PR 46.** Generate a tailored resume from a canonical structured resume and approved evidence cards. Reorder and rewrite truthful bullets for the role, highlight relevant projects, and omit weaker material when space is limited. Preserve employer names, dates, degrees, titles, and numeric claims unless the user edits the source record. Never invent years of experience, technologies used, metrics, publications, or responsibilities.

**PR 47.** Render the resume as an ATS readable PDF and optionally DOCX. Keep a stable identity header, simple section headings, selectable text, and a consistent one or two page layout. Validate the generated file for nonempty text, correct links, page count, and visual clipping before it becomes Ready. Store the exact version and hash that the user reviewed.

**PR 48.** Draft the channel appropriate message: email body and subject for email applications; cover letter or short motivation text when requested; and concise answers for employer form questions. Use the posting's language unless the user chooses otherwise. Preserve the user's voice preferences and avoid generic praise or unsupported claims.

**PR 49.** Detect all application form fields, required attachments, allowed file types, character limits, dropdown options, consent checkboxes, and custom questions before the review screen. Draft answers from the verified profile and mark unknown or sensitive questions for user input. Salary expectations, visa status, notice period, and relocation willingness come from explicit profile values or user answers, never guesses.

**PR 50.** Every generated sentence about candidate experience must map to an approved evidence item or be clearly marked as an opinion or motivation. The review screen flags unsupported claims, ambiguous ownership, stale repository evidence, missing required answers, and conflicts between resume and form fields.

**PR 51.** Support configurable drafting providers. The orchestration process runs on the Mac and passes only the job text and selected candidate evidence needed for that draft. Codex CLI supports scripted output and can use a local Ollama or LM Studio provider in OSS mode; this uses the local provider's model rather than a proprietary Codex model. [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) · [Codex local provider configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)

**PR 52.** Antigravity CLI supports headless output. Claude Code supports print mode, but its standard setup requires network access for AI processing. A CLI process running on the Mac must not be labeled on-device inference unless its model actually runs locally. Provider settings show `local inference`, `remote inference through local CLI`, or `unavailable`, plus account and usage requirements. No automatic fallback from local to remote inference is allowed. [Antigravity headless mode](https://antigravity.google/docs/cli/headless/) · [Claude Code setup](https://code.claude.com/docs/en/getting-started)

**PR 53.** Normalize every provider's output to the same validated draft schema. Constrain drafting agents to the selected evidence bundle, the job description, and a temporary working directory. Treat job postings, README files, and form text as untrusted input rather than instructions. Provider failure leaves an editable draft state and never triggers submission.

### Review and one-click submission

**PR 54.** The local review screen displays the employer, job title and source URL, exact application destination, final resume preview and download, message or cover letter, every form answer, attachments, provider and data destination used, and any unresolved warnings. The user can edit any field, regenerate one component, or save a reusable answer. The Send button is enabled only after required fields and document checks pass.

**PR 55.** A Send click authorizes submission of exactly the reviewed package to exactly the displayed destination. Bind this authorization to the vacancy, destination URL or email address, field values, message, and attachment hashes. If the destination or substantive form fields change before submission, pause and show the changed package for review. No second confirmation is needed when the package remains the same.

**PR 56.** For email applications, send the reviewed subject, body, and attachments through a configured mail transport. For web forms, open the preferred direct employer application page, populate fields, upload the reviewed files, and submit. When a source routes to an external applicant tracking system, display that final destination in review. Handle multi-step forms within the same authorized submission only while each step matches the reviewed information.

**PR 57.** Record the outcome as `submitted_confirmed`, `sent_confirmed`, `submitted_unconfirmed`, `needs_user_attention`, or `failed`. Save a confirmation number, visible success text, sent message identifier, or receipt screenshot when available. A button click or filled form alone is not proof of submission. Do not automatically retry an uncertain outcome, because that could create a duplicate application.

**PR 58.** Enforce one active application per canonical vacancy and employer requisition ID. Warn on likely duplicate openings and show the existing application state. Permit an intentional second application only after the user reviews it as a separate package. Store every revision, send action, destination, receipt, and failure for audit and later interview preparation.

**PR 59.** If the site requires a CAPTCHA, email verification, two-factor step, unexpected legal consent, or a new question after Send, pause, preserve progress, and ask the user to complete or review that step. Continue submission after the user resolves it; never report completion prematurely.

## Data model

| Entity | Essential fields |
| --- | --- |
| `candidate_profile` | version, preferences, constraints, skills, evidence, updated time |
| `employer` | canonical name, aliases, parent, sector, status, career source, verification date |
| `source_config` | source type, URL/query/group ID, interval, enabled, adapter, scan depth |
| `scan_run` | start, finish, status, items seen, new items, boundary, error, confidence |
| `source_observation` | source ID, URL, raw content, content hash, extracted fields, seen time, source time |
| `vacancy` | canonical title, employer, location, work mode, status, first and last seen |
| `vacancy_source` | vacancy ID, observation ID, source role, merge reason, confidence |
| `analysis` | profile version, model versions, evidence, fields, score components, confidence |
| `feedback` | vacancy ID, state, reason, time, prior state |
| `notification` | vacancy ID, channel, message ID, sent time, action state |
| `repository_snapshot` | repository URL or path, branch, commit SHA, inspection time, visibility, owner context |
| `project_evidence` | project summary, contribution claim, support references, verification state, version |
| `resume_source` | canonical experience, education, skills, links, approved claim IDs |
| `application_draft` | vacancy, selected evidence, provider, provider mode, resume and message versions, answers, warnings |
| `application_submission` | draft hash, destination, authorized time, state, provider receipt, retry decision |

Raw HTML or screenshots are diagnostic artifacts, not the primary database representation. Keep only what is needed to debug parser failures and delete it on the configured retention schedule. Store person contact details only when they are part of a relevant recruiting post.

## Scan and processing flow

```text
Four hour scheduler
  ├── LinkedIn search queue
  ├── Facebook group queue
  └── Employer career queue
          ↓
     Raw observations and scan logs
          ↓
    Normalization and employer aliases
          ↓
    Exact and candidate deduplication
          ↓
    Rules → embeddings → small local model → optional judge
          ↓
    Evidence based deterministic score
          ↓
    Alerts, digest, local review, feedback
          ↓
    Selected job + approved experience and GitHub evidence
          ↓
    Tailored resume + message + form answers
          ↓
    User reviews complete package and clicks Send
          ↓
    Email or form submission → receipt and application history
```

All times are stored in UTC and displayed in Asia/Ho_Chi_Minh. Processing is idempotent: replaying the same observation must not create a second vacancy or duplicate alert. Every job can be traced back to the source observations and analysis version that produced its score. Every application can be traced to the reviewed evidence, generated files, user authorization, final destination, and receipt.

## Employer coverage and seed registry

The following is the starting registry, not a claim that every name currently has open AI roles or an independent career site. Parent, subsidiary, brand, former name, and acquired team are different relationships and must be represented accurately. The registry is maintained continuously; the requirement is to include all these targets and discover more, not to freeze coverage at this list.

### Vingroup and related ecosystem

Vingroup; VinFast; VinSmart Future; VinRobotics; VinMotion; VinDynamics; VinSpace; VinSOC; VinCSS; VinAI; GSM / Xanh SM / Green SM; V-GREEN; VinBus; VinUni; Vinmec; Vinschool; Vinhomes; Vinpearl; and new technology entities announced by the group. Keep VinHMS, VinID, and One Mount as adjacent or historical leads until their current company relationships and hiring sources are verified. Vingroup's 2025 annual report lists VinSmart Future, VinRobotics, VinMotion, VinDynamics, VinSpace, VinSOC, VinCSS, VinFast, GSM, and VinUniversity, among others. [Vingroup annual report](https://ircdn.vingroup.net/storage/Uploads/0_Bao%20cao%20thuong%20nien/2025/ENG%20Vingroup%20AR25_Chap%201-6_260507.pdf)

VinAI remains a separate watch target with its own careers page. Its former generative AI division, MovianAI, was acquired by Qualcomm in 2025; the registry must not collapse all VinAI jobs into Qualcomm jobs. [VinAI careers](https://www.vinai.io/careers/) · [Qualcomm announcement](https://www.qualcomm.com/news/releases/2025/04/qualcomm-expands-generative-ai-capabilities-with-acquisition-of-)

### Viettel, VNPT, FPT, and CMC

- **Viettel:** Viettel Group, Viettel High Tech, Viettel Cyber Security, Viettel Solutions, Viettel Digital, Viettel Software, Viettel Telecom, Viettel IDC, Viettel AI teams, Viettel Research and Development, and other hiring units discovered under the group.
- **VNPT:** VNPT Group, VNPT AI, VNPT IT, VNPT Technology, VNPT Media where roles remain separately posted, VNPT regional technology centers, and other hiring units. VNPT reports establishing VNPT AI in its 2026 operating plan. [VNPT 2026 plan](https://vnpt.com.vn/file/808080809b97b1bb019b9b3d370b02a9/upload/xdata/202604/20260423110355bieu_2_bc_muc_tieu_tong_quat_khkd_2026.pdf)
- **FPT:** FPT Corporation, FPT Software, FPT Smart Cloud / FPT.AI, FPT Digital, FPT IS, FPT Telecom, FPT Online, FPT Education, FPT University, FPT Semiconductor, and other hiring units. [FPT member companies](https://fpt.com/en/business/member-companies)
- **CMC:** CMC Corporation, CMC Global, CMC Technology and Solution / CMC TS, CMC Telecom, CMC Cloud, CMC AI related units, and CMC University.

### Vietnamese banks and bank technology units

Seed every domestic commercial bank in the maintained banking register, including Agribank, Vietcombank, BIDV, VietinBank, MB, Techcombank, VPBank, ACB, Sacombank, SHB, HDBank, TPBank, VIB, MSB, SeABank, OCB, Eximbank, LPBank, Nam A Bank, NCB, ABBank, Bac A Bank, BaoViet Bank, BVBank, VietABank, VietBank, KienlongBank, PGBank, Saigonbank, SCB, PVcomBank, VCBNeo, MBV, GPBank, and Vikki Bank. Add policy and cooperative institutions when they post relevant engineering roles, including Vietnam Bank for Social Policies and Co-opBank. Retain historical aliases such as CBBank, OceanBank, and DongA Bank for matching older posts to current brands. The Vietnam Banks Association lists member institutions and current names, including VCBNeo and MBV. [Vietnam Banks Association](https://vnba.org.vn/vi/vnba/member)

Track Vietnam operations of foreign owned banks and branches as separate employers: HSBC Vietnam, Standard Chartered Vietnam, Shinhan Bank Vietnam, Woori Bank Vietnam, UOB Vietnam, CIMB Vietnam, Public Bank Vietnam, Hong Leong Bank Vietnam, ANZ Vietnam, Citi Vietnam, DBS, MUFG, Mizuho, SMBC, ICBC, Bank of China, KEB Hana, BNP Paribas, Deutsche Bank, Maybank, Bangkok Bank, and newly licensed or newly hiring branches. Validate branch presence and career URLs before marking each `active_scan`.

Also track bank technology arms and subsidiaries when they hire separately, such as MB Ageas Life technology teams, MCredit, FE Credit, Home Credit Vietnam, HD SAISON, and the digital banking units under the banks above. Do not assume a subsidiary's job belongs to the parent requisition system.

### Finance, payments, insurance, and commerce

MoMo, ZaloPay, VNPAY, VNPay, NAPAS, Payoo, OnePay, SmartPay, 9Pay, Moca, Trusting Social, Cake by VPBank, Timo, Finhay, TCBS, SSI, VNDIRECT, VPS, Mirae Asset Vietnam, Bao Viet, FWD Vietnam, Prudential Vietnam, Manulife Vietnam, AIA Vietnam, Chubb Life Vietnam, Shopee, SeaMoney, SPX Express, Lazada, Tiki, Grab, Be Group, Gojek successor hiring entities if active, Traveloka, Agoda, and Booking.com Vietnam teams.

### AI, software, cloud, and research

One Mount, NAVER Vietnam, Zalo AI, VNG, VNG Cloud, MoMo AI teams, Cinnamon AI, Rikkeisoft, Rikkei AI, TMA Solutions, KMS Technology, NashTech Vietnam, Axon Active, NTQ Solution, Sun Asterisk, VMO, Sotatek, SmartOSC, Tek Experts, EPAM Vietnam, Thoughtworks Vietnam, Anduin Transactions, ELSA, Katalon, KiotViet, Base.vn, MISA, Sapo, Haravan, Cốc Cốc, Sky Mavis, Amanotes, Gear Inc, and other product teams discovered from source posts. Include universities and labs with research engineer hiring: VinUni, Hanoi University of Science and Technology, VNU University of Engineering and Technology, Posts and Telecommunications Institute of Technology, Phenikaa University, and their AI laboratories.

### Global technology, chips, industrial, and mobility employers

NVIDIA, Qualcomm, Samsung R&D Vietnam, Samsung Electronics Vietnam, LG Electronics R&D Vietnam, Bosch Vietnam, Intel Products Vietnam, NXP Vietnam, Renesas Vietnam, Synopsys Vietnam, Cadence Vietnam, Marvell Vietnam, MediaTek Vietnam, Ampere, ARM, Microsoft Vietnam, Google Vietnam, Amazon / AWS Vietnam, IBM Vietnam, Oracle Vietnam, SAP Vietnam, Cisco Vietnam, Ericsson Vietnam, Nokia Vietnam, Huawei Vietnam, Panasonic Vietnam, Toyota Vietnam, Honda Vietnam, Hyundai Motor Vietnam, and new employers with Vietnam based AI, embedded, robotics, or software roles. Each must have a verified Vietnam relevant search or careers source before active scanning.

### Continuous registry growth

At least weekly, compare the local employer registry with newly observed employer names, outbound application domains, bank association or regulator lists, and major group disclosures. Generate a review queue for unmatched names, renamed firms, and dead career links. The registry must support thousands of entries; many will be `source_discovery` until a reliable hiring source is identified. Coverage reporting shows both total known employers and actively scanned employers.

## Quality and success measures

| Measure | Target and method |
| --- | --- |
| Scheduled scans | All enabled LinkedIn and Facebook configurations become due every four hours; at least 95% of due cycles complete when the Mac is awake, online, and authenticated |
| Discovery lag | For posts actually visible in a successful cycle, ingest by the end of that cycle plus processing time; report the measured median and 95th percentile |
| Coverage | Show active, discovery, manual only, unavailable, and retired employer counts; no hidden gap between registry size and live scan coverage |
| Duplicate quality | At least 95% of exact same ID or URL sightings attach to the same vacancy; review a sample of fuzzy merges for false merges |
| Ranking usefulness | At least 70% of the top ten daily results are marked worth opening during a two week evaluation, after the candidate profile is calibrated |
| False negatives | Weekly hand review of a sample of low ranked and excluded jobs; record missed viable roles and their reason codes |
| Explanation | Every alert shows at least one positive match, one gap or `none identified`, and the source evidence link |
| Availability | A source failure or expired login is visible within two failed cycles |
| Evidence accuracy | All resume contribution and impact claims are approved by the user and link to a source record |
| Application review | Every required answer and attachment is displayed before Send; zero unreviewed submissions |
| Submission accuracy | No duplicate send for an uncertain outcome; every confirmed application retains a receipt or message identifier |
| Application completion | At least 90% of supported email and stable web form applications complete after one Send click, excluding verification challenges and changed forms |
| Cost | No database or hosting charge; strict local inference has no model service charge, while connected agent mode reports its account or API usage separately |

The desired review queue is approximately 5 to 15 items on a busy day, but the system must not manufacture results to hit that range or suppress genuinely strong jobs solely to stay below it.

## Implementation milestones and acceptance

**Milestone 1, collection foundation.** Implement profile and registry schemas, scan scheduler, source observations, employer adapters, LinkedIn and Facebook browser collectors, scan health, and exact deduplication. Seed the full employer list above with truthful coverage statuses. Acceptance: six scheduled cycles per day for each enabled social source; raw observations can be replayed without duplicates; each employer has a visible status.

**Milestone 2, intelligence and evidence.** Add normalization, conservative fuzzy deduplication, deterministic filters, embeddings, local extraction, scoring, Telegram alerts, feedback, local search, resume source data, and repository evidence intake. Acceptance: every notification is traceable to raw source evidence and score components; a nominal three year requirement alone never causes automatic rejection; every project claim is presented for user verification.

**Milestone 3, assisted application.** Add drafting provider adapters, tailored resume rendering, email and form drafting, full package review, one-click authorized submission, receipts, and duplicate protection. Acceptance: a user can select a job, review all application content, click Send once, and see a confirmed or precise unresolved outcome; no submission occurs without that click.

**Milestone 4, broad coverage and calibration.** Add more employer adapters, source discovery, optional 9B judge, evaluation samples, and ranking calibration. Acceptance: employer coverage metrics reflect live adapters accurately; weekly false negative and merge reviews produce actionable diagnostics; no company count ceiling exists in configuration or schema.

These are implementation milestones inside the first usable product scope. The first daily-use release includes assisted application. The seed list is a target for registry inclusion from Milestone 1. Live collection requires a verified source per employer and is reported explicitly. LinkedIn and Facebook four hour scanning starts in Milestone 1.

## Implementation stack

- Python 3, Playwright, Pydantic, SQLite in WAL mode, SQLite FTS5, and optional vector extension.
- MLX compatible quantized local language model and a multilingual local embedding model. Select exact model versions after measuring memory, speed, and Vietnamese extraction quality on the Mac mini.
- Provider adapters for `codex exec`, `agy --print`, `claude --print`, and a strictly local inference backend. Invoke agents with scoped input files and schema validated outputs. Pin provider and model versions per draft.
- A canonical resume schema and local PDF/DOCX renderer; a browser application adapter and a configured outbound email transport. Keep application credentials in the system keychain or another restricted local secret store.
- `launchd` for scheduling, structured local logs, and Telegram Bot API long polling for interactions. Telegram documents `getUpdates` as a polling method, so no inbound public server is required. [Telegram Bot API](https://core.telegram.org/bots/api#getupdates)
- Configurable employer adapters with shared parsing contracts and fixtures from representative pages.

The browser, model, and database workloads must fit within 16 GB without sustained swapping. The larger judge is optional and loaded only when required. The collector saves data first and lets inference catch up if the machine is busy.

## Key risks and product responses

| Risk | Required response |
| --- | --- |
| Source UI changes | Version adapters, retain limited diagnostic snapshots, detect sudden zero results, and surface failures |
| Feed ordering hides a post | Report source coverage confidence; do not claim that unseen posts were checked |
| Session expires or challenge appears | Pause the affected source, request manual recovery, and continue other sources |
| Employer names and structures change | Maintain dated aliases and parent relationships; verify registry entries regularly |
| Excessive false negatives | Sample rejected items weekly and keep semantic thresholds conservative |
| False vacancy merges | Require multiple signals, preserve source observations, and support splitting |
| Local memory pressure | Separate browser and inference workloads, load models sequentially, and make the larger judge optional |
| Notification fatigue | Send only high score immediate alerts; batch lower score and uncertain items into the digest |
| Stale or reposted listings | Track original publication and first observed dates separately; do not reset age on repost |
| Invented or overstated candidate claims | Require approved evidence cards, show provenance, and block unsupported claims before Ready |
| Shared or employer owned repository | Require user confirmation of personal contribution and allow exclusion of sensitive code |
| Drafting provider sends data remotely | Label inference location and data bundle before use; require an explicit mode choice and prevent silent provider fallback |
| Application form changes after review | Compare live fields and destination to the approved package, then pause for review when substantive content changes |
| Ambiguous submission result | Record an uncertain state and avoid automatic retries |
| Duplicate applications | Check canonical vacancy and requisition ID before enabling Send |

## Definition of done

Job Radar is ready for daily use when LinkedIn and Facebook perform and log every configured four hour cycle; the employer registry includes the seed catalog and accurately reports active career scans; a job can be traced from alert to every original source; approved experience and GitHub evidence can produce a tailored resume, message, and form answers; the user can review the entire package and click Send once to submit; the product records a receipt or an accurate unresolved state; and two weeks of real usage show that the top results save review time without systematically losing viable stretch roles.
