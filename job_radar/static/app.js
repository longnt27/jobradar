const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when = (value) => value ? new Date(value).toLocaleString() : 'Never';
function relativeWhen(value) {
  if (!value) return 'Unknown';
  const date = new Date(value);
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'Just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 172800) return 'Yesterday';
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return date.toLocaleDateString(undefined, {month:'short', day:'numeric', year:date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric'});
}
const exactTimeTitle = (value) => value ? ` title="${escapeHtml(new Date(value).toLocaleString())}"` : '';
function scoreBadge(job) {
  if (job.analysis_status !== 'done' || !Number.isFinite(job.score)) {
    const label = job.analysis_status === 'failed' ? 'Failed' : ['not_configured', 'dismissed'].includes(job.analysis_status) ? 'No score' : 'Analyzing';
    return `<span class="score score-pending" aria-label="${label}">${label}</span>`;
  }
  const range = job.score >= 80 ? 'high' : job.score >= 60 ? 'good' : job.score >= 40 ? 'medium' : 'low';
  return `<span class="score score-${range}" aria-label="Match score ${job.score} out of 100">${job.score}</span>`;
}
let activeJob = null;
let activeJobPinned = false;
let jobsPage = 1;
let jobsInboxMode = 'since_last_visit';
let jobsVisitBoundary = null;
let jobsWorkspaceReady = false;
let savedJobViews = [];
let pendingIgnoreJobId = null;
let employerPage = 1;
let sourcePage = 1;
let projectCards = [];
let discoveredRepos = [];
let selectedProjectId = null;
let projectProviderReady = false;
let projectProcessing = {};
let projectProviderAvailability = {};
let applicationDrafts = [];
let applicationPreparations = [];
let applicationsTotal = 0;
let applicationsPage = 1;
let applicationsPages = 1;
let submissionHistoryPage = 1;
let submissionHistoryPages = 1;
let activeApplicationId = null;
let activePreparationJobId = null;
let applicationReviewChanges = {};
let applicationWorkspaceView = 'drafts';
let currentSearchIntent = null;
let editingPositionId = null;

function formatDescription(value) {
  const blocks = String(value ?? '').replace(/\r\n/g, '\n').split(/\n{2,}/).map((part) => part.trim()).filter(Boolean);
  const bullet = /^[•–-]\s+/;
  return blocks.map((block, index) => {
    const lines = block.split('\n').map((line) => line.trim()).filter(Boolean);
    if (lines.every((line) => bullet.test(line))) {
      return `<ul>${lines.map((line) => `<li>${escapeHtml(line.replace(bullet, ''))}</li>`).join('')}</ul>`;
    }
    const nextIsList = bullet.test(blocks[index + 1] || '');
    const heading = lines.length === 1 && lines[0].length < 100 && !/[.!?]$/.test(lines[0]) &&
      (nextIsList || /^(what|why|nice to have|contact|about|requirements|qualifications|responsibilities|benefits)\b/i.test(lines[0]));
    return heading ? `<h4>${escapeHtml(lines[0])}</h4>` : `<p>${lines.map(escapeHtml).join('<br>')}</p>`;
  }).join('');
}

function renderJobAnalysis(job, score) {
  const status = job.analysis_status;
  const stateMessage = status === 'done' ? `Match reviewed · ${relativeWhen(job.analyzed_at)}`
    : status === 'failed' ? `Match review failed: ${escapeHtml(job.analysis_error || 'Unknown error')}`
    : status === 'dismissed' ? 'Failed review dismissed. You can still decide from the posting itself.'
    : ['pending', 'running'].includes(status) ? 'Job Radar is reading the posting and checking the match.'
    : 'No detailed match review yet. You can still inspect and triage this job.';
  const retry = status === 'failed' ? `<div class="actions"><button type="button" data-analyze="${job.id}" class="secondary">Try review again</button><button type="button" data-dismiss-analysis="${job.id}" class="secondary">Dismiss failed review</button></div>`
    : status === 'dismissed' ? `<button type="button" data-analyze="${job.id}" class="secondary">Run match review</button>` : '';
  const completed = status === 'done';
  const facts = completed ? (score?.facts || {}) : {};
  const criteriaEntries = completed && score?.criteria ? Object.entries(score.criteria)
    .filter(([,item]) => item && typeof item === 'object' && Number.isFinite(item.score)) : [];
  const best = [...criteriaEntries].sort((a,b) => b[1].score - a[1].score).filter(([,item]) => item.score >= 7).slice(0, 2);
  const gaps = [...criteriaEntries].sort((a,b) => a[1].score - b[1].score).filter(([,item]) => item.score <= 5).slice(0, 2);
  const labelFor = (key) => ({
    role:'Role', required_skills:'Required skills', preferred_skills:'Preferred skills',
    experience:'Experience', responsibilities:'Responsibilities', location:'Location',
    work_mode:'Work mode', education:'Education', freshness:'Freshness',
  })[key] || key.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
  const whyItems = !completed ? '<li>Match review is in progress. Read the posting to judge fit for now.</li>' : best.length
    ? best.map(([key,item]) => `<li><strong>${escapeHtml(labelFor(key))}:</strong> ${escapeHtml(item.reason || 'Good fit')}</li>`).join('')
    : score?.explanation ? `<li>${escapeHtml(score.explanation)}</li>` : '<li>No strong fit signal has been identified yet.</li>';
  const riskItems = completed ? [
    ...(score?.hard_exclusions || []).map((reason) => `<li>${escapeHtml(reason)}</li>`),
    ...gaps.map(([key,item]) => `<li><strong>${escapeHtml(labelFor(key))}:</strong> ${escapeHtml(item.reason || 'Needs a closer look')}</li>`),
  ] : [];
  const watchOut = !completed ? '<li>Check the posting requirements while the new review runs.</li>' : riskItems.length ? riskItems.join('') : '<li>No major gap identified by the current match review. Check the posting for anything the model missed.</li>';
  const salary = facts.salary_range || 'Not stated';
  const basics = `<div class="decision-basics-grid">
      <div><span>Salary</span><strong>${escapeHtml(salary)}</strong><small>Not included in the match score</small></div>
      <div><span>Location</span><strong>${escapeHtml([facts.location || job.location, facts.work_mode || job.work_mode].filter(Boolean).join(' · ') || 'Not stated')}</strong></div>
      <div><span>Experience</span><strong>${facts.years_required == null ? 'Not stated' : `${escapeHtml(facts.years_required)} years`}</strong></div>
      <div><span>Role</span><strong>${escapeHtml([facts.role || job.title, facts.seniority].filter(Boolean).join(' · '))}</strong></div>
    </div>`;

  const list = (label, items) => items?.length ? `<div><strong>${label}</strong><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : '';
  const extracted = completed ? `<details class="detail-disclosure"><summary>Full extracted requirements</summary>
    ${facts.summary ? `<p>${escapeHtml(facts.summary)}</p>` : ''}
    <div class="fact-grid">${list('Required skills', facts.required_skills)}${list('Preferred skills', facts.preferred_skills)}${list('Responsibilities', facts.responsibilities)}${list('Education', facts.education)}${list('Spoken languages', facts.languages)}</div>
  </details>` : '';

  const weighted = criteriaEntries.map(([key,item]) => {
    const weight = Number(score?.weights?.[key] || 0);
    return `<div class="criterion-row"><div class="criterion-copy"><strong>${escapeHtml(labelFor(key))}</strong><small>${escapeHtml(item.reason || '')}</small></div><div class="criterion-score"><span>${escapeHtml(item.score)}/10</span><small>${weight}% weight</small></div><div class="criterion-weight"><span style="width:${Math.max(4, Math.min(100, weight))}%"></span></div></div>`;
  }).join('');
  const breakdown = weighted ? `<details class="detail-disclosure"><summary>Detailed match breakdown <span>${job.score}/100</span></summary>
    <p>${escapeHtml(score?.explanation || '')}</p><div class="criteria-list">${weighted}</div>
  </details>` : '';

  return `<div class="review-section job-review-status"><p class="hint">${stateMessage}</p>${retry}</div>
    <div class="job-decision-summary">
      <section class="decision-card decision-card--fit"><h3>Why it fits</h3><ul>${whyItems}</ul></section>
      <section class="decision-card decision-card--risk"><h3>Watch out</h3><ul>${watchOut}</ul></section>
      <section class="decision-card decision-card--basics"><h3>Basics</h3>${basics}</section>
    </div>
    ${extracted}${breakdown}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? (options.headers || {}) : {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  const raw = await response.text();
  let body;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = raw; }
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message || `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.detail = detail;
    error.status = response.status;
    throw error;
  }
  return body;
}

function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function scrollNodeIntoView(node, options = {}) {
  if (!node) return;
  node.scrollIntoView({...options, behavior: prefersReducedMotion() ? 'auto' : (options.behavior || 'smooth')});
}

function statusClass(tone = 'success') {
  const normalized = tone === 'muted' ? 'neutral' : tone || 'success';
  return `status-badge status-badge--${normalized}`;
}

function clearNotice() {
  const node = $('#notice');
  clearTimeout(window.noticeTimeout);
  node.hidden = true;
  node.className = 'toast';
  node.replaceChildren();
}

function notice(message, error = false, action = null) {
  const node = $('#notice');
  clearTimeout(window.noticeTimeout);
  node.hidden = false;
  node.className = `toast${error ? ' error' : ''}`;
  node.setAttribute('role', error ? 'alert' : 'status');
  node.setAttribute('aria-live', error ? 'assertive' : 'polite');
  const messageNode = document.createElement('span');
  messageNode.className = 'toast-message';
  messageNode.textContent = message;
  const children = [messageNode];
  if (action?.label && typeof action.onClick === 'function') {
    const actionButton = document.createElement('button');
    actionButton.type = 'button';
    actionButton.className = 'toast-action';
    actionButton.textContent = action.label;
    actionButton.addEventListener('click', async () => {
      clearTimeout(window.noticeTimeout);
      actionButton.disabled = true;
      try { await action.onClick(); clearNotice(); }
      catch (error) { notice(error.message, true); }
    });
    children.push(actionButton);
  }
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'toast-dismiss';
  dismiss.setAttribute('aria-label', 'Dismiss notification');
  dismiss.textContent = '×';
  dismiss.addEventListener('click', clearNotice);
  children.push(dismiss);
  node.replaceChildren(...children);
  if (!error) window.noticeTimeout = setTimeout(clearNotice, action ? 9000 : 6000);
}

function beginPending(button, label = 'Working…') {
  if (!button) return null;
  button.dataset.pendingText = button.textContent;
  button.dataset.pendingDisabled = button.disabled ? 'true' : 'false';
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  if (label) button.textContent = label;
  button.form?.setAttribute('aria-busy', 'true');
  return button;
}

function endPending(button, {restoreDisabled = true} = {}) {
  if (!button) return;
  const wasDisabled = button.dataset.pendingDisabled === 'true';
  const idleText = button.dataset.pendingText;
  button.removeAttribute('aria-busy');
  button.form?.removeAttribute('aria-busy');
  if (idleText != null) button.textContent = idleText;
  button.disabled = restoreDisabled ? wasDisabled : false;
  delete button.dataset.pendingText;
  delete button.dataset.pendingDisabled;
}

function setTabLoading(target, loading) {
  if (!target) return;
  target.classList.toggle('is-loading', loading);
  if (loading) target.setAttribute('aria-busy', 'true');
  else target.removeAttribute('aria-busy');
  const indicator = $('#tab-loading');
  if (target.classList.contains('active')) {
    indicator.hidden = !loading;
    if (loading) indicator.textContent = `Loading ${($('#page-title').textContent || 'page').toLowerCase()}…`;
  }
}

function socialSiteNames(browser) {
  return (browser.sites || []).map((site) => site === 'linkedin' ? 'LinkedIn' : 'Facebook').join(' and ');
}

function setStepStatus(selector, label, tone = '') {
  const node = $(selector);
  node.textContent = label;
  node.className = statusClass(tone);
}

function renderSocialAuth(browser, linkedinPaused = false) {
  const expired = (browser.sites || []).length > 0;
  const connected = browser.connected_sites || [];
  $('#social-auth-banner').hidden = !expired && !linkedinPaused;
  $('#social-auth-message').textContent = linkedinPaused
    ? 'LinkedIn automated checks are paused after an account activity warning. Other job sources continue checking.'
    : expired ? `${socialSiteNames(browser)} sign-in expired. Sign in again to resume those scans.` : '';
  $('#social-auth-action').hidden = linkedinPaused && !(browser.sites || []).some((site) => site !== 'linkedin');
  const socialReady = !expired && !linkedinPaused && connected.length === 2 && !['opening', 'open'].includes(browser.state);
  setStepStatus('#social-sign-in-status', linkedinPaused ? 'LinkedIn paused' : expired ? 'Sign in again'
    : ['opening', 'open'].includes(browser.state) ? 'Waiting for sign-in'
    : socialReady ? 'Both connected' : `${connected.length} of 2 connected`, socialReady ? '' : 'warning');
  for (const site of ['linkedin', 'facebook']) {
    const label = site === 'linkedin' ? 'LinkedIn' : 'Facebook';
    const needsSignIn = !connected.includes(site) || (browser.sites || []).includes(site);
    $(`#${site}-sign-in-status`).textContent = site === 'linkedin' && linkedinPaused ? 'Automated checks paused'
      : (browser.sites || []).includes(site) ? 'Session expired'
      : connected.includes(site) ? 'Connected' : 'Sign-in needed';
    const button = $(`#setup-${site}-start`);
    button.textContent = needsSignIn ? `Sign in to ${label}` : `Reconnect ${label}`;
    button.disabled = ['opening', 'open'].includes(browser.state);
  }
}

async function refreshSocialAuth() {
  const setup = await api('/api/setup');
  renderSocialAuth(setup.browser, setup.linkedin_automation_paused);
}

async function openSocialSignIn() {
  await openSetupPanel('social-sign-in-panel');
}

async function openSetupPanel(id) {
  const loading = showTab('settings');
  const panel = $(`#${id}`);
  const step = panel.closest('details.flow-panel') || panel;
  step.open = true;
  scrollNodeIntoView(panel, {block:'start'});
  await loading;
  step.open = true;
}

async function loadSetup() {
  clearTimeout(window.setupPoll);
  const data = await api('/api/setup');
  const smtpTest = data.smtp_test || {};
  setStepStatus('#setup-smtp-status', !data.smtp_configured ? 'Optional' : smtpTest.status === 'accepted' ? 'Test email accepted' : smtpTest.status === 'failed' ? 'Test failed' : 'Configured',
    !data.smtp_configured ? 'muted' : smtpTest.status === 'failed' ? 'warning' : '');
  const telegramNotifications = data.telegram_notifications || {};
  const telegramModes = telegramNotifications.modes || {};
  const activeTelegramModes = ['application_reviews','strong_job_alerts','daily_digest'].filter((key) => telegramModes[key]).length;
  setStepStatus('#setup-telegram-status', data.telegram_configured ? `Configured · ${activeTelegramModes} mode${activeTelegramModes === 1 ? '' : 's'}` : 'Optional', data.telegram_configured ? '' : 'muted');
  setStepStatus('#matching-status', data.matching.model ? `Configured · ${data.matching.model}` : 'Choose a model', data.matching.model ? '' : 'warning');
  renderSocialAuth(data.browser, data.linkedin_automation_paused);
  $('#setup-browser-detail').textContent = data.linkedin_automation_paused
    ? 'LinkedIn automated checks are paused after an account activity warning. You can review saved jobs and use LinkedIn manually; other sources continue checking.'
    : data.browser.error || (data.browser.state === 'opening' ? 'Opening Chrome…' : data.browser.state === 'open' ? `Waiting for ${data.browser.active_site === 'linkedin' ? 'LinkedIn' : 'Facebook'} sign-in in Chrome. The window closes automatically when the account page loads.` : data.browser.state === 'reauth_required' ? `${socialSiteNames(data.browser)} needs a new sign-in. Other sources keep scanning.` : data.browser.last_saved_at ? `Saved session last updated ${when(data.browser.last_saved_at)}. Upcoming scans will verify site access.` : 'Choose a site to begin. Google sign-in opens in regular Chrome.');
  const mailForm = $('#setup-smtp-form');
  if (!mailForm.dataset.initialized) {
    mailForm.elements.host.value = data.smtp_host || '';
    mailForm.elements.port.value = data.smtp_port || 587;
    mailForm.elements.user.value = data.smtp_user || '';
    mailForm.elements.from_address.value = data.smtp_from || '';
    mailForm.dataset.initialized = 'true';
  }
  $('#smtp-send-test').disabled = !data.smtp_configured || mailForm.dataset.dirty === 'true';
  $('#smtp-test-result').textContent = mailForm.dataset.dirty === 'true' ? 'Save your changes before sending a test.'
    : smtpTest.status ? `${smtpTest.detail} ${smtpTest.status === 'accepted' ? 'Sent to' : 'Attempted for'} ${smtpTest.recipient} · ${when(smtpTest.checked_at)}`
    : data.smtp_configured ? `No test sent yet. The test will go to ${data.smtp_from}.` : 'Save settings to send a test email.';
  const alertForm = $('#setup-telegram-form');
  if (!alertForm.dataset.initialized) {
    alertForm.elements.chat_id.value = data.telegram_chat_id || '';
    alertForm.elements.application_reviews.checked = telegramModes.application_reviews !== false;
    alertForm.elements.strong_job_alerts.checked = Boolean(telegramModes.strong_job_alerts);
    alertForm.elements.daily_digest.checked = Boolean(telegramModes.daily_digest);
    alertForm.elements.digest_time.value = telegramNotifications.digest_time || '18:00';
    alertForm.elements.quiet_start.value = telegramNotifications.quiet_start || '';
    alertForm.elements.quiet_end.value = telegramNotifications.quiet_end || '';
    alertForm.dataset.initialized = 'true';
  }
  if ((['opening', 'open'].includes(data.browser.state) || ['opening', 'open'].includes(data.chatgpt?.state) || data.matching.download_state === 'downloading') && $('#settings').classList.contains('active')) {
    window.setupPoll = setTimeout(() => loadSetup().then(async (latest) => {
      if (latest?.chatgpt?.state === 'saved') await loadSettings();
    }).catch((error) => notice(error.message, true)), 2000);
  }
  return data;
}

async function openHomeRoute(route) {
  if (!route?.tab) return;
  const params = new URLSearchParams(route.params || {});
  if (route.tab === 'jobs') {
    const suffix = params.toString();
    const hash = `#jobs${suffix ? `?${suffix}` : ''}`;
    history.pushState({tab:'jobs'}, '', hash);
    await showTab(`jobs${suffix ? `?${suffix}` : ''}`, 'replace');
    return;
  }
  if (route.tab === 'applications') {
    const view = params.get('view') || 'drafts';
    const draft = params.get('draft');
    await showTab(draft ? `applications/${draft}` : 'applications');
    setApplicationWorkspaceView(view);
    if (draft) await loadApplications(draft);
    else if (view === 'history') await loadSubmissionHistory();
    else if (['automation','activity'].includes(view)) await loadAutoApply();
    return;
  }
  await showTab(route.tab);
}

function homePriorityLabel(kind) {
  return ({
    submission_uncertain:'Check submission',
    application_review:'Review application',
    application_confirmation:'Confirm method',
    shortlisted_to_prepare:'Prepare shortlisted job',
    strong_unseen_job:'Strong new match',
    unseen_job:'New job',
    track_application:'Track outcome',
  })[kind] || String(kind || '').replaceAll('_', ' ');
}

function homePriorityToneClass(tone) {
  return ({
    danger:'status-badge--danger',
    warning:'status-badge--warning',
    success:'status-badge--success',
    info:'status-badge--info',
  })[tone] || 'status-badge--neutral';
}

async function loadHome() {
  const [data, profile, setup, discovery] = await Promise.all([
    api('/api/status'), api('/api/profile'), api('/api/setup'), api('/api/discovery/coverage')
  ]);
  renderSocialAuth(setup.browser, setup.linkedin_automation_paused);
  const home = data.home || {};
  const counts = home.counts || {};
  const capabilities = setup.capabilities || {};
  const discoveryCapability = capabilities.discovery || {ready:false,status:'not_configured',detail:'Add a job source.'};
  const preparationCapability = capabilities.application_preparation || {ready:false,missing:[]};
  const automaticCapability = capabilities.automatic_drafts || {ready:false,missing:[]};
  const complete = Boolean(discoveryCapability.ready);

  const metrics = [
    {label:'Unseen jobs', value:counts.unseen_jobs || 0, route:{tab:'jobs',params:{inbox:'unseen'}}},
    {label:`Strong matches (${data.strong_match_threshold}+)`, value:counts.strong_matches || 0, route:home.routes?.strong_matches},
    {label:'Applications to review', value:(counts.drafts_to_review || 0) + (counts.submission_uncertain || 0), route:home.routes?.applications},
    {label:'Applied to track', value:counts.tracking || 0, route:{tab:'jobs',params:{application:'applied',outcome:'none',inbox:'all'}}},
  ];
  $('#metrics').innerHTML = metrics.map((item, index) =>
    `<button type="button" class="metric home-metric" data-home-metric="${index}"><strong>${item.value}</strong><span>${escapeHtml(item.label)}</span><small>Open →</small></button>`
  ).join('');
  $('#metrics').querySelectorAll('[data-home-metric]').forEach((button) => button.addEventListener('click', () =>
    openHomeRoute(metrics[Number(button.dataset.homeMetric)].route).catch((error) => notice(error.message, true))
  ));

  $('#home-loop').innerHTML = (home.stages || []).map((stage, index) =>
    `<button type="button" class="home-loop-step ${stage.count ? 'has-work' : ''}" data-home-stage="${index}">
      <span class="home-loop-index">${index + 1}</span>
      <span class="home-loop-copy"><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></span>
      <span class="home-loop-count">${stage.count}</span>
      <span class="step-arrow">→</span>
    </button>`
  ).join('');
  $('#home-loop').querySelectorAll('[data-home-stage]').forEach((button) => button.addEventListener('click', () =>
    openHomeRoute(home.stages[Number(button.dataset.homeStage)].route).catch((error) => notice(error.message, true))
  ));

  const health = home.health || {};
  const sourceIssues = (discovery.sources || []).filter((source) => ['degraded','limited','unknown','paused'].includes(source.coverage?.level));
  const pausedLinkedIn = sourceIssues.filter((source) => source.kind === 'linkedin' && source.coverage?.level === 'paused').length;
  const otherSourceIssues = sourceIssues.length - pausedLinkedIn;
  const sourceCount = (discovery.sources || []).filter((source) => source.enabled !== false).length;
  const discoveryNeedsAttention = !sourceCount || discovery.level === 'degraded' || discovery.level === 'unknown';
  const pendingReviews = Number(health.analysis?.pending || 0);
  const failedReviews = Number(health.analysis?.failed || 0);
  $('#source-summary').textContent = `${discovery.counts.linkedin} LinkedIn searches · ${discovery.counts.facebook} Facebook groups · ${discovery.counts.career} company sites`;
  $('#home-health-message').textContent = !sourceCount
    ? 'No job sources are checking for new postings. Add or enable a source to start discovery.'
    : pausedLinkedIn
    ? `${pausedLinkedIn} LinkedIn search${pausedLinkedIn === 1 ? ' is' : 'es are'} paused after an account warning.${otherSourceIssues ? ` ${otherSourceIssues} other source${otherSourceIssues === 1 ? ' needs' : 's need'} attention.` : ' Other sources continue checking.'}`
    : sourceIssues.length
    ? `${sourceIssues.length} source${sourceIssues.length === 1 ? '' : 's'} need a closer look. Open Job sources to check or repair them.`
    : 'All configured sources are checking normally. Social feeds may still omit older postings.';
  const healthDetails = sourceIssues.slice(0, 3).map((source) =>
    `<div class="home-health-detail"><strong>${escapeHtml(source.name)} · ${escapeHtml(source.coverage?.label || 'Needs attention')}</strong><small>${escapeHtml(source.coverage?.detail || '')}</small></div>`);
  if (sourceIssues.length > 3) healthDetails.push(`<div class="home-health-detail"><strong>${sourceIssues.length - 3} more source${sourceIssues.length === 4 ? '' : 's'} need attention</strong></div>`);
  healthDetails.push(`<div class="home-health-detail"><strong>Job match reviews</strong><small>${pendingReviews} waiting or running · ${failedReviews} failed${setup.matching.model ? ` · ${escapeHtml(setup.matching.model)}` : ' · No local model selected'}</small></div>`);
  $('#home-health-details').innerHTML = healthDetails.join('');
  $('#home-health-status').textContent = discoveryNeedsAttention || sourceIssues.length || failedReviews ? 'Needs attention' : pendingReviews ? 'Reviewing jobs' : 'Healthy';
  $('#home-health-status').className = `status-badge status-badge--${discoveryNeedsAttention || sourceIssues.length || failedReviews ? 'warning' : pendingReviews ? 'info' : 'success'}`;
  $('#home-health-action').hidden = !(discoveryNeedsAttention || sourceIssues.length || failedReviews || pendingReviews);
  $('#home-health-action').textContent = discoveryNeedsAttention || sourceIssues.length ? 'Review job sources →' : 'Review match queue →';
  const healthRoute = discoveryNeedsAttention || sourceIssues.length ? {tab:'sources'} : {tab:'queue'};
  $('#home-health-action').onclick = () => openHomeRoute(healthRoute).catch((error) => notice(error.message, true));

  const required = [
    {label:'Job discovery', detail:discoveryCapability.detail || discovery.label, done:Boolean(discoveryCapability.ready), attention:discoveryCapability.status === 'attention', tab:'sources'},
  ];
  const optional = [
    {label:'Detailed local match review', done:Boolean(setup.matching.model), detail:setup.matching.model ? `Using ${setup.matching.model}` : 'Optional · fallback ranking still works', tab:'settings', panel:'provider-panel'},
    {label:'Application preparation', done:Boolean(preparationCapability.ready), detail:preparationCapability.ready ? 'Ready to prepare applications' : `Optional · needs ${(preparationCapability.missing || []).join(', ') || 'setup'}`, tab:'profile'},
    {label:'Automatic draft preparation', done:Boolean(automaticCapability.ready), detail:automaticCapability.ready ? 'Ready if you choose to enable it' : `Optional · needs ${(automaticCapability.missing || []).join(', ') || 'setup'}`, tab:'applications'},
    {label:'Telegram reviews', done:setup.telegram_configured, detail:setup.telegram_configured ? 'Connected' : 'Optional · not configured', tab:'settings', panel:'telegram-panel'},
    {label:'Email sending', done:setup.smtp_test?.status === 'accepted', detail:setup.smtp_test?.status === 'accepted' ? 'Connected and tested' : setup.smtp_configured ? 'Optional · configured, test pending' : 'Optional · not configured', tab:'settings', panel:'smtp-panel'},
  ];
  $('#home-setup-status').textContent = complete ? (discoveryCapability.status === 'attention' ? 'Discovery ready · check coverage' : 'Discovery ready') : 'Add a job source';
  $('#home-setup-status').className = complete ? (discoveryCapability.status === 'attention' ? 'status-badge status-badge--warning' : 'status-badge status-badge--success') : 'status-badge status-badge--warning';
  $('#home-steps').innerHTML = required.map((step) =>
    `<button class="step-row ${step.done ? 'is-done' : ''}" data-home-step="${step.tab}"><span class="step-check ${step.done ? 'done' : ''}">${step.done ? (step.attention ? '!' : '✓') : '○'}</span><span><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail)}</small></span><span class="step-arrow">→</span></button>`
  ).join('');
  $('#home-optional').innerHTML = `<h4>Optional capabilities</h4>${optional.map((step) =>
    `<button class="home-optional-row" data-home-step="${step.tab}" data-setup-panel="${step.panel || ''}"><span><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail)}</small></span><span class="status-badge ${step.done ? 'status-badge--success' : 'status-badge--neutral'}">${step.done ? 'Ready' : 'Optional'}</span></button>`
  ).join('')}`;
  document.querySelectorAll('[data-home-step]').forEach((button) => button.addEventListener('click', () =>
    button.dataset.setupPanel ? openSetupPanel(button.dataset.setupPanel) : showTab(button.dataset.homeStep)
  ));

  const priorities = home.priority_items || [];
  $('#home-action-count').textContent = home.headline || '';
  if (priorities.length) {
    $('#home-actions').innerHTML = priorities.map((item, index) =>
      `<button class="home-action-row" data-home-priority="${index}">
        <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.subtitle)}</small></span>
        <span class="status-badge ${homePriorityToneClass(item.tone)}">${escapeHtml(homePriorityLabel(item.kind))}</span>
        <span class="step-arrow">→</span>
      </button>`
    ).join('');
  } else if (home.state === 'radar_degraded') {
    $('#home-actions').innerHTML = '<div class="home-empty-warning"><strong>No user action is queued.</strong><p>That does not mean the search is clear: discovery or match review is degraded. Check radar confidence before trusting an empty inbox.</p></div>';
  } else {
    $('#home-actions').innerHTML = '<div class="home-empty-clear"><strong>You’re caught up.</strong><p>No new jobs need triage and no applications need review right now.</p></div>';
  }
  $('#home-actions').querySelectorAll('[data-home-priority]').forEach((button) => button.addEventListener('click', () =>
    openHomeRoute(priorities[Number(button.dataset.homePriority)].route).catch((error) => notice(error.message, true))
  ));

  const firstRoute = priorities[0]?.route || (health.degraded ? health.route : {tab:'jobs',params:{inbox:'unseen'}});
  $('#home-show-all-priorities').hidden = !priorities.length;
  $('#home-show-all-priorities').onclick = () => openHomeRoute(firstRoute).catch((error) => notice(error.message, true));

  $('#home-title').textContent = !complete ? 'Start with job discovery.' : home.headline || 'Work the next best thing.';
  $('#home-description').textContent = !complete
    ? 'Add at least one job source. Discovery is useful on its own; application features stay optional.'
    : home.state === 'radar_degraded'
      ? 'You are caught up on user actions, but discovery or match review needs attention before the radar can be trusted.'
      : 'Triage what is new, prepare the jobs you want, review applications, then track outcomes.';
  $('#home-hero').classList.toggle('is-compact', complete);
  $('#home-primary').textContent = !complete ? 'Add a job source →' : priorities.length ? 'Open top priority →' : health.degraded ? 'Review radar health →' : 'Check unseen jobs →';
  $('#home-primary').onclick = () => openHomeRoute(!complete ? {tab:'sources'} : firstRoute).catch((error) => notice(error.message, true));

  await loadHomeQueue();
}

async function loadHomeQueue() {
  clearTimeout(window.homeQueuePoll);
  const queue = await api('/api/queue');
  const lanes = [
    ['scan', 'Scan', queue.scans], ['analysis', 'Match', queue.analysis], ['draft', 'Draft', queue.drafts],
  ];
  const running = lanes.flatMap(([kind, label, lane]) => lane.active.map((item) => ({kind, label, item, status:'Running'})));
  const waiting = lanes.flatMap(([kind, label, lane]) => lane.waiting.map((item) => ({kind, label, item, status:'Waiting'})));
  const attention = [
    ...queue.analysis.failed.map((item) => ({kind:'analysis', label:'Match', item, status:'Needs attention'})),
    ...(queue.drafts.attention || []).map((item) => ({kind:'draft', label:'Draft', item, status:'Confirm application method'})),
  ];
  const preview = [...running, ...attention, ...waiting].slice(0, 6);
  const total = running.length + waiting.length;
  $('#home-queue-count').textContent = `${total} in queue${attention.length ? ` · ${attention.length} need attention` : ''}`;
  $('#home-queue').innerHTML = preview.length ? preview.map(({kind, label, item, status}) =>
    `<button type="button" class="home-queue-row" data-home-queue-kind="${kind}" data-home-queue-id="${item.id}" ${item.draft_id ? `data-home-queue-draft="${item.draft_id}"` : ''}><span class="home-queue-kind">${label}</span><span class="home-queue-title">${escapeHtml(kind === 'scan' ? item.name : item.title)}</span><small>${status}</small></button>`
  ).join('') : '<p class="queue-empty">Nothing running or waiting.</p>';
  $('#home-queue').querySelectorAll('[data-home-queue-kind]').forEach((button) => button.addEventListener('click', async () => {
    const kind = button.dataset.homeQueueKind, id = button.dataset.homeQueueId;
    if (kind === 'scan') {
      $('#source-kind').value = '';
      await showTab('sources');
      const card = [...document.querySelectorAll('#source-list [data-source-id]')].find((node) => node.dataset.sourceId === id);
      scrollNodeIntoView(card, {block:'center'});
    } else if (kind === 'draft' && button.dataset.homeQueueDraft) {
      await showTab('applications'); await showApplication(button.dataset.homeQueueDraft);
    } else {
      await openJobInJobs(id);
    }
  }));
  if ($('#home').classList.contains('active')) window.homeQueuePoll = setTimeout(() => loadHomeQueue().catch((error) => notice(error.message, true)), 5000);
}

let queueSignature = null;

function queueRow(item, kind, label, position = null, active = false) {
  const name = kind === 'scan' ? item.name : item.title;
  const detail = kind === 'scan' ? item.kind : `${item.company}${item.score == null ? '' : ` · ${item.score}/100`}`;
  return `<button type="button" class="queue-row ${active ? 'is-active' : ''}" data-queue-kind="${kind}" data-queue-id="${item.id}" ${item.draft_id ? `data-queue-draft="${item.draft_id}"` : ''}>
    <span class="queue-row-order">${active ? '●' : position}</span>
    <span class="queue-row-copy"><strong>${escapeHtml(name)}</strong><small>${escapeHtml(detail)}</small></span>
    <span class="queue-row-state">${escapeHtml(label)}</span>
  </button>`;
}

function queueWaiting(target, items, kind, labelFor) {
  const scrollTop = target.querySelector('.queue-scroll')?.scrollTop || 0;
  target.innerHTML = items.length ? `<div class="queue-waiting-head">Waiting <span>${items.length}</span></div><div class="queue-scroll">${items.map((item, index) => queueRow(item, kind, labelFor(item), item.position || index + 1)).join('')}</div>`
    : '<p class="queue-empty">Nothing waiting.</p>';
  if (target.querySelector('.queue-scroll')) target.querySelector('.queue-scroll').scrollTop = scrollTop;
}

function queueFocusToken() {
  const active = document.activeElement;
  if (!active || !$('#queue').contains(active)) return null;
  if (active.dataset.queueDismiss) return {type:'dismiss', id:active.dataset.queueDismiss};
  if (active.dataset.queueKind && active.dataset.queueId) return {type:'row', kind:active.dataset.queueKind, id:active.dataset.queueId};
  if (active.id === 'queue-open-reviews') return {type:'reviews'};
  return null;
}

function restoreQueueFocus(token) {
  if (!token) return;
  let target = null;
  if (token.type === 'row') {
    target = [...document.querySelectorAll('#queue [data-queue-kind][data-queue-id]')]
      .find((node) => node.dataset.queueKind === token.kind && node.dataset.queueId === token.id);
  } else if (token.type === 'dismiss') {
    target = [...document.querySelectorAll('#queue [data-queue-dismiss]')]
      .find((node) => node.dataset.queueDismiss === token.id);
  } else if (token.type === 'reviews') {
    target = $('#queue-open-reviews');
  }
  target?.focus({preventScroll:true});
}

function queueFingerprint(data) {
  const lane = (items, prefix) => items.map((item) => `${prefix}:${item.id}:${item.stage || ''}:${item.position || ''}`);
  return JSON.stringify({
    scans:[...lane(data.scans.active, 'active'), ...lane(data.scans.waiting, 'waiting')],
    analysis:[...lane(data.analysis.active, 'active'), ...lane(data.analysis.waiting, 'waiting'), ...lane(data.analysis.failed, 'failed')],
    drafts:[...lane(data.drafts.active, 'active'), ...lane(data.drafts.waiting, 'waiting'),
      ...lane(data.drafts.attention || [], 'attention')],
    reviewReady:data.drafts.review_ready,
  });
}

function queueUpdateTime() {
  return new Intl.DateTimeFormat(undefined, {hour:'numeric', minute:'2-digit', second:'2-digit'}).format(new Date());
}

function announceQueueUpdate(data, reason, changed) {
  const userTriggered = reason === 'manual' || reason === 'action';
  if (!userTriggered && !(reason === 'poll' && changed)) return;
  const total = [data.scans, data.analysis, data.drafts]
    .reduce((sum, lane) => sum + lane.active.length + lane.waiting.length, 0);
  const failed = data.analysis.failed.length;
  const prefix = reason === 'manual' ? 'Queue refreshed' : reason === 'poll' ? 'Queue changed' : 'Queue updated';
  $('#queue-live').textContent = `${prefix} at ${queueUpdateTime()}. ${total} work item${total === 1 ? '' : 's'} in progress or waiting${failed ? `; ${failed} need${failed === 1 ? 's' : ''} attention` : ''}.`;
}

async function loadQueue(options = {}) {
  const reason = options?.reason || 'initial';
  clearTimeout(window.queuePoll);
  const focusToken = queueFocusToken();
  const data = await api('/api/queue');
  const signature = queueFingerprint(data);
  const changed = queueSignature !== null && queueSignature !== signature;
  queueSignature = signature;
  const scans = data.scans, analysis = data.analysis, drafts = data.drafts;
  const total = [scans, analysis, drafts].reduce((sum, lane) => sum + lane.active.length + lane.waiting.length, 0);
  $('#queue-summary').innerHTML = `<div><strong>${total}</strong><span>work items in progress or waiting</span></div><div><strong>${scans.active.length + scans.waiting.length}</strong><span>scans</span></div><div><strong>${analysis.active.length + analysis.waiting.length}</strong><span>match reviews</span></div><div><strong>${drafts.active.length + drafts.waiting.length}</strong><span>drafts</span></div>`;
  for (const [id, lane] of [['scan', scans], ['analysis', analysis], ['draft', drafts]]) {
    const blocked = id === 'analysis' ? analysis.failed.length : id === 'draft' ? (drafts.attention || []).length : 0;
    $(`#queue-${id}-count`).textContent = `${lane.active.length} running · ${lane.waiting.length} waiting${blocked ? ` · ${blocked} needs attention` : ''}`;
  }
  $('#queue-scan-active').innerHTML = scans.active.length
    ? `<div class="queue-now-head">Scanning now</div>${scans.active.map((item) => queueRow(item, 'scan', item.started_at ? `Started ${when(item.started_at)}` : 'Running', null, true)).join('')}`
    : '<p class="queue-empty">No scan running.</p>';
  queueWaiting($('#queue-scan-waiting'), scans.waiting, 'scan', (item) => item.requested_by === 'you' ? 'Requested by you' : 'Scheduled');
  $('#queue-analysis-active').innerHTML = analysis.active.length
    ? `<div class="queue-now-head">Working now</div>${analysis.active.map((item) => queueRow(item, 'analysis', item.stage === 'scoring' ? 'Checking match' : 'Reading job details', null, true)).join('')}`
    : '<p class="queue-empty">No job being analyzed.</p>';
  queueWaiting($('#queue-analysis-waiting'), analysis.waiting, 'analysis', () => 'Read job, then check match');
  $('#queue-analysis-failed').innerHTML = analysis.failed.length ? `<div class="queue-waiting-head queue-failed-head">Needs attention <span>${analysis.failed.length}</span></div><div class="queue-scroll">${analysis.failed.map((item) => `<div class="queue-failed-row">${queueRow(item, 'analysis', 'Match review failed', '!')}<button type="button" class="text-button" data-queue-dismiss="${item.id}" aria-label="Dismiss failed analysis for ${escapeHtml(item.title)}">Dismiss</button></div>`).join('')}</div>` : '';
  $('#queue-analysis-failed').querySelectorAll('[data-queue-dismiss]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/jobs/${button.dataset.queueDismiss}/dismiss-analysis`, {method:'POST'}); await loadQueue({reason:'action'}); notice('Failed analysis dismissed.'); }
    catch(error) { notice(error.message, true); }
  }));
  if (analysis.service_error) $('#queue-analysis-waiting').insertAdjacentHTML('beforeend', `<p class="queue-attention">${escapeHtml(analysis.service_error)}</p>`);
  else if (!analysis.model) $('#queue-analysis-waiting').insertAdjacentHTML('beforeend', '<p class="queue-attention">Choose a matching model in Settings to review job fit.</p>');
  $('#queue-draft-active').innerHTML = drafts.active.length
    ? `<div class="queue-now-head">Preparing now</div>${drafts.active.map((item) => queueRow(item, 'draft', item.stage === 'regenerating' ? 'Regenerating' : 'Preparing draft', null, true)).join('')}`
    : '<p class="queue-empty">No draft being prepared.</p>';
  queueWaiting($('#queue-draft-waiting'), drafts.waiting, 'draft', (item) =>
    item.requested_by === 'manual' ? 'Requested by you' : drafts.enabled ? 'Automatic draft' : 'Automation off');
  $('#queue-draft-attention').innerHTML = (drafts.attention || []).length
    ? `<div class="queue-waiting-head queue-failed-head">Needs confirmation <span>${drafts.attention.length}</span></div><div class="queue-scroll">${drafts.attention.map((item) => queueRow(item, 'draft', 'Confirm application method', '!')).join('')}</div>`
    : '';
  $('#queue-draft-review').innerHTML = drafts.review_ready ? `<button type="button" class="text-button" id="queue-open-reviews">${drafts.review_ready} draft${drafts.review_ready === 1 ? '' : 's'} ready for review →</button>` : '';
  $('#queue-open-reviews')?.addEventListener('click', () => showTab('applications'));
  document.querySelectorAll('#queue [data-queue-kind]').forEach((button) => button.addEventListener('click', async () => {
    const kind = button.dataset.queueKind, id = button.dataset.queueId;
    if (kind === 'scan') {
      $('#source-kind').value = '';
      await showTab('sources');
      const card = [...document.querySelectorAll('#source-list [data-source-id]')].find((node) => node.dataset.sourceId === id);
      scrollNodeIntoView(card, {block:'center'});
    } else if (kind === 'draft' && button.dataset.queueDraft) {
      await showTab('applications'); await showApplication(button.dataset.queueDraft);
    } else {
      await openJobInJobs(id);
      scrollNodeIntoView($('#job-detail'), {block:'start'}); $('#job-detail').focus({preventScroll:true});
    }
  }));
  $('#queue-updated-at').textContent = `Updated ${queueUpdateTime()} · Auto-refresh every 5 seconds`;
  announceQueueUpdate(data, reason, changed);
  restoreQueueFocus(focusToken);
  if ($('#queue').classList.contains('active')) window.queuePoll = setTimeout(() => loadQueue({reason:'poll'}).catch((error) => notice(error.message, true)), 5000);
}

const JOB_FILTERS = {
  q:'#job-query', decision:'#job-decision', application:'#job-application', outcome:'#job-outcome',
  score:'#job-score', freshness:'#job-freshness', mode:'#job-work-mode', location:'#job-location',
  source:'#job-source', seniority:'#job-seniority', fit:'#job-fit', sort:'#job-sort',
};

function setJobsInboxMode(mode) {
  jobsInboxMode = ['since_last_visit','unseen','all'].includes(mode) ? mode : 'since_last_visit';
  document.querySelectorAll('[data-job-inbox]').forEach((button) => {
    const selected = button.dataset.jobInbox === jobsInboxMode;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
}

function currentJobFilters() {
  const filters = {inbox: jobsInboxMode};
  for (const [key, selector] of Object.entries(JOB_FILTERS)) {
    const value = $(selector)?.value?.trim();
    if (value && !(key === 'sort' && value === 'best') && !(key === 'fit' && value === 'eligible')) filters[key] = value;
  }
  return filters;
}

function applyJobFilterSnapshot(filters = {}) {
  for (const [key, selector] of Object.entries(JOB_FILTERS)) {
    $(selector).value = filters[key] || (key === 'sort' ? 'best' : key === 'fit' ? 'eligible' : '');
  }
  setJobsInboxMode(filters.inbox || 'all');
}

function readJobsHashState() {
  const raw = location.hash.slice(1);
  const [route, search = ''] = raw.split('?');
  if (route !== 'jobs') return;
  const params = new URLSearchParams(search);
  for (const [key, selector] of Object.entries(JOB_FILTERS)) {
    const node = $(selector);
    if (node) node.value = params.get(key) || (key === 'sort' ? 'best' : key === 'fit' ? 'eligible' : '');
  }
  setJobsInboxMode(params.get('inbox') || 'since_last_visit');
  jobsPage = Math.max(1, Number(params.get('page') || 1));
  activeJob = params.get('job') || null;
  activeJobPinned = Boolean(activeJob);
}

function jobsHash() {
  const params = new URLSearchParams();
  for (const [key, selector] of Object.entries(JOB_FILTERS)) {
    const value = $(selector)?.value?.trim();
    if (value && !(key === 'sort' && value === 'best') && !(key === 'fit' && value === 'eligible')) params.set(key, value);
  }
  if (jobsInboxMode !== 'since_last_visit') params.set('inbox', jobsInboxMode);
  if (jobsPage > 1) params.set('page', String(jobsPage));
  if (activeJob) params.set('job', activeJob);
  const search = params.toString();
  return `#jobs${search ? `?${search}` : ''}`;
}

function syncJobsHash(mode = 'replace') {
  if (!$('#jobs').classList.contains('active')) return;
  const hash = jobsHash();
  if (location.hash === hash) return;
  history[mode === 'push' ? 'pushState' : 'replaceState']({tab:'jobs'}, '', hash);
}

async function ensureJobsWorkspace() {
  if (jobsWorkspaceReady) return;
  let savedBoundary = null;
  try { savedBoundary = sessionStorage.getItem('jobRadarJobsVisitBoundary'); } catch {}
  let visit = null;
  if (savedBoundary === null) {
    visit = await api('/api/jobs/visit', {method:'POST', body:'{}'});
    jobsVisitBoundary = visit.previous || '';
    try { sessionStorage.setItem('jobRadarJobsVisitBoundary', jobsVisitBoundary || '__first_visit__'); } catch {}
  } else {
    jobsVisitBoundary = savedBoundary === '__first_visit__' ? '' : savedBoundary;
  }
  $('#jobs-since-label').textContent = jobsVisitBoundary ? 'Since last visit' : 'To review';
  savedJobViews = await api('/api/jobs/views');
  renderSavedJobViews();

  const hasExplicitHash = location.hash.startsWith('#jobs?');
  if (!hasExplicitHash) {
    const defaultView = savedJobViews.find((view) => view.default);
    if (defaultView) {
      applyJobFilterSnapshot(defaultView.filters || {});
      syncJobsHash('replace');
    } else setJobsInboxMode('since_last_visit');
  }
  if (visit) {
    $('#jobs-since-count').textContent = String(visit.since_last_visit || 0);
    $('#jobs-unseen-count').textContent = String(visit.unseen || 0);
  }
  jobsWorkspaceReady = true;
}

function renderSavedJobViews() {
  const select = $('#job-view-select');
  const selected = select.value;
  select.innerHTML = '<option value="">None</option>' + savedJobViews.map((view) =>
    `<option value="${escapeHtml(view.id)}">${escapeHtml(view.name)}${view.default ? ' · default' : ''}</option>`
  ).join('');
  if (savedJobViews.some((view) => view.id === selected)) select.value = selected;
  $('#job-delete-view').disabled = !select.value;
}

function jobDecisionLabel(value) {
  return ({undecided:'Undecided',shortlisted:'Shortlisted',ignored:'Ignored',later:'Later'})[value] || value || 'Undecided';
}

function applicationProgressLabel(value) {
  return ({
    not_started:'Not started',
    draft_ready:'Draft ready',
    applied:'Applied',
    applied_external:'Applied elsewhere',
    preparing:'Preparing in background',
    needs_confirmation:'Application method needs confirmation',
    attention:'Submission needs attention',
  })[value] || value || 'Not started';
}

function confirmPreparationPreflight(preflight) {
  const dialog = $('#job-prepare-confirm-dialog');
  const linkedinEasyApply = preflight.action?.action_type === 'linkedin_easy_apply';
  $('#job-prepare-confirm-title').textContent = linkedinEasyApply ? 'Inspect LinkedIn Easy Apply' : 'Application method needs confirmation';
  dialog.querySelector('button[value="confirm"]').textContent = linkedinEasyApply ? 'Inspect and prepare' : 'Prepare anyway';
  $('#job-prepare-confirm-reason').textContent = preflight.reason || 'Confirm the application method before preparing.';
  $('#job-prepare-confirm-action').textContent = preflight.action_label || 'Manual application';
  $('#job-prepare-confirm-evidence').textContent = preflight.action?.evidence || 'No verified application destination is available.';
  if (typeof dialog.showModal !== 'function') {
    return Promise.resolve(window.confirm(`${preflight.reason || 'Application method needs confirmation.'}\n\nPrepare a draft anyway?`));
  }
  if (dialog.open) dialog.close('cancel');
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true});
    dialog.showModal();
  });
}

function recruitingOutcomeLabel(value) {
  return ({none:'No outcome',interview:'Interview',rejected:'Rejected',offer:'Offer'})[value] || value || 'No outcome';
}

function lifecycleBadge(label, tone = 'neutral') {
  return `<span class="status-badge status-badge--${tone}">${escapeHtml(label)}</span>`;
}

function jobCardDecisionActions(job) {
  if (job.decision_state === 'undecided') {
    return `<button type="button" class="text-button" data-job-decision="shortlisted" data-job-id="${job.id}">Shortlist</button>
      <button type="button" class="text-button" data-job-decision="later" data-job-id="${job.id}">Later · 3d</button>
      <button type="button" class="text-button danger" data-job-decision="ignored" data-job-id="${job.id}">Ignore</button>`;
  }
  return `<button type="button" class="text-button" data-job-decision="undecided" data-job-id="${job.id}">Back to inbox</button>`;
}

async function setJobDecisionWithUndo(job, decision, {reason = null, snoozedUntil = null, askReason = false} = {}) {
  const previous = job.decision_state || 'undecided';
  const previousSnooze = job.snoozed_until || null;
  const payload = {decision, reason, snoozed_until:snoozedUntil};
  await api(`/api/jobs/${job.id}/decision`, {method:'POST', body:JSON.stringify(payload)});
  const message = decision === 'shortlisted' ? 'Job shortlisted. This is a bookmark/decision only; automation follows its own settings.'
    : decision === 'ignored' ? 'Job ignored.'
    : decision === 'later' ? 'Job snoozed for 3 days.'
    : 'Job returned to the inbox.';
  notice(message, false, {
    label:'Undo',
    onClick: async () => {
      await api(`/api/jobs/${job.id}/decision`, {method:'POST', body:JSON.stringify({
        decision:previous, snoozed_until:previous === 'later' ? previousSnooze : null,
      })});
      await loadJobs();
    },
  });
  if (askReason && decision === 'ignored') pendingIgnoreJobId = job.id;
  await loadJobs();
  if (pendingIgnoreJobId === job.id) {
    const dialog = $('#job-ignore-reason-dialog');
    if (typeof dialog.showModal === 'function' && !dialog.open) dialog.showModal();
  }
}

function bindJobCards(jobs) {
  const byId = new Map(jobs.map((job) => [job.id, job]));
  document.querySelectorAll('[data-job]').forEach((node) => node.addEventListener('click', async () => {
    try {
      activeJob = node.dataset.job; activeJobPinned = true; syncJobsHash('push');
      await showJob(node.dataset.job, true);
      if (window.matchMedia('(max-width: 900px)').matches) scrollNodeIntoView($('#job-detail'), {block:'start'});
      $('#job-detail').focus({preventScroll:true});
    } catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-job-decision]').forEach((button) => button.addEventListener('click', async () => {
    const job = byId.get(button.dataset.jobId);
    if (!job) return;
    const decision = button.dataset.jobDecision;
    const snoozedUntil = decision === 'later'
      ? new Date(Date.now() + 3 * 86400 * 1000).toISOString()
      : null;
    try {
      await setJobDecisionWithUndo(job, decision, {
        snoozedUntil,
        askReason: decision === 'ignored',
      });
    } catch(error) { notice(error.message, true); }
  }));
}

function renderJobCard(job) {
  const sourceLabel = !job.source ? 'Manual' : job.source.kind === 'career' ? 'Career page' : job.source.kind === 'linkedin' ? 'LinkedIn' : 'Facebook';
  const tags = [fitClassLabel(job.fit_class), job.work_mode, sourceLabel].filter(Boolean);
  const dateValue = job.published_at || job.first_seen_at;
  const decisionBadge = job.decision_state !== 'undecided'
    ? lifecycleBadge(jobDecisionLabel(job.decision_state), job.decision_state === 'ignored' ? 'danger' : job.decision_state === 'later' ? 'warning' : 'info')
    : '';
  const applicationBadge = job.application_progress !== 'not_started'
    ? lifecycleBadge(applicationProgressLabel(job.application_progress), job.application_progress === 'attention' ? 'danger' : job.application_progress.startsWith('applied') ? 'success' : 'warning')
    : '';
  const outcomeBadge = job.recruiting_outcome !== 'none'
    ? lifecycleBadge(recruitingOutcomeLabel(job.recruiting_outcome), job.recruiting_outcome === 'rejected' ? 'danger' : job.recruiting_outcome === 'offer' ? 'success' : 'info')
    : '';
  return `<div class="item job-card surface-action ${job.read_state === 'unseen' ? 'is-unseen' : ''}" data-job-card="${job.id}">
    <button type="button" class="card-select" data-job="${job.id}" aria-pressed="${job.id === activeJob ? 'true' : 'false'}" aria-label="Open ${escapeHtml(job.title)} at ${escapeHtml(job.company)}">
      ${job.read_state === 'unseen' ? '<span class="job-unread-dot" aria-label="Unseen job"></span>' : ''}
      ${scoreBadge(job)}<div class="item-title">${escapeHtml(job.title)}</div>
      <div class="job-card-company">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div>
      ${job.salary_range ? `<div class="job-card-salary">${escapeHtml(job.salary_range)} <small>salary from posting</small></div>` : ''}
      <div class="job-card-tags"><span class="status-badge status-badge--${fitClassTone(job.fit_class)}">${escapeHtml(tags.shift() || '')}</span>${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}</div>
      <div class="job-card-lifecycle">${decisionBadge}${applicationBadge}${outcomeBadge}</div>
      <div class="job-card-footer"><span${exactTimeTitle(dateValue)}>${job.published_at ? 'Posted' : 'Found'} ${relativeWhen(dateValue)}</span></div>
    </button>
    <div class="job-card-actions">${jobCardDecisionActions(job)}${job.source ? `<a href="${escapeHtml(job.source.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open original posting for ${escapeHtml(job.title)} at ${escapeHtml(job.company)}">Original ↗</a>` : ''}</div>
  </div>`;
}

async function loadJobs() {
  clearTimeout(window.jobPoll);
  await ensureJobsWorkspace();
  const query = new URLSearchParams({
    q: $('#job-query').value,
    decision: $('#job-decision').value,
    application: $('#job-application').value,
    outcome: $('#job-outcome').value,
    inbox: jobsInboxMode,
    since: jobsVisitBoundary || '',
    page: jobsPage,
    page_size: 10,
    focus_id: activeJobPinned && jobsPage === 1 ? activeJob || '' : '',
    min_score: $('#job-score').value,
    fit: $('#job-fit').value,
    freshness: $('#job-freshness').value,
    work_mode: $('#job-work-mode').value,
    location: $('#job-location').value,
    source: $('#job-source').value,
    seniority: $('#job-seniority').value,
    sort: $('#job-sort').value,
  });
  [...query.entries()].forEach(([key,value]) => { if (value === '') query.delete(key); });
  const [result, failures, intent, matching] = await Promise.all([
    api(`/api/jobs/page?${query}`), loadJobAnalysis(), api('/api/search-intent'), api('/api/matching/status'),
  ]);
  if (['#job-decision','#job-application','#job-outcome','#job-score','#job-freshness','#job-work-mode','#job-location','#job-source','#job-seniority'].some((selector) => $(selector).value)) {
    $('#job-more-filters').open = true;
  }
  $('#job-analysis-model').textContent = matching.model ? `Using ${matching.model} on this Mac` : 'No local matching model selected';
  const progress = matching.pending
    ? `${matching.completed} reviewed · ${matching.pending} waiting or in progress. Reviews continue in the background.`
    : matching.model ? `${matching.completed} reviewed · all saved jobs are up to date.` : 'Choose a local model in Settings to review job fit.';
  $('#matching-overview').textContent = matching.service_error
    ? `Review paused: ${matching.service_error}`
    : `${progress}${matching.failed ? ` ${matching.failed} need attention.` : ''}`;
  syncStrongThresholdUi(intent);
  if (jobsPage > result.pages) { jobsPage = result.pages; syncJobsHash(); return loadJobs(); }
  const jobs = result.items;
  if (activeJobPinned && activeJob && !jobs.some((job) => job.id === activeJob) && jobsPage > 1) {
    jobsPage = 1;
    syncJobsHash();
    return loadJobs();
  }
  $('#jobs-since-count').textContent = String(result.inbox?.since_last_visit || 0);
  $('#jobs-unseen-count').textContent = String(result.inbox?.unseen || 0);
  const inboxCopy = jobsInboxMode === 'since_last_visit'
    ? jobsVisitBoundary
      ? `${result.inbox?.since_last_visit || 0} undecided job${result.inbox?.since_last_visit === 1 ? '' : 's'} discovered since your previous Jobs visit.`
      : `${result.inbox?.since_last_visit || 0} undecided job${result.inbox?.since_last_visit === 1 ? '' : 's'} to review.`
    : jobsInboxMode === 'unseen'
      ? `${result.inbox?.unseen || 0} unseen undecided job${result.inbox?.unseen === 1 ? '' : 's'}.`
      : 'Showing your full job history. Decisions, applications, and recruiting outcomes stay separate.';
  $('#jobs-inbox-summary').textContent = inboxCopy;
  $('#jobs-page-summary').textContent = result.total ? `Page ${result.page} of ${result.pages} · ${result.total} jobs` : 'No jobs';
  $('#jobs-prev').disabled = jobsPage <= 1;
  $('#jobs-next').disabled = jobsPage >= result.pages;
  const emptyCopy = jobsInboxMode === 'since_last_visit' ? (jobsVisitBoundary ? 'Nothing new since your previous visit.' : 'No jobs to review yet.')
    : jobsInboxMode === 'unseen' ? 'No unseen jobs left.'
    : 'No jobs match these filters.';
  $('#job-list').innerHTML = jobs.length ? jobs.map(renderJobCard).join('') : `<div class="empty">${emptyCopy}</div>`;
  bindJobCards(jobs);
  if (activeJob && jobs.some((job) => job.id === activeJob)) await showJob(activeJob, activeJobPinned);
  else {
    activeJob = null; activeJobPinned = false; syncJobsHash();
    $('#job-detail').innerHTML = `<div class="empty">${jobs.length ? 'Choose a job to read its posting and match explanation.' : emptyCopy}</div>`;
  }
  if ((matching.pending || jobs.some((job) => ['pending','running'].includes(job.analysis_status))
      || jobs.some((job) => job.application_progress === 'preparing')) && $('#jobs').classList.contains('active')) {
    window.jobPoll = setTimeout(() => loadJobs().catch((error) => notice(error.message, true)), 5000);
  }
}

async function showJob(id, pin = false, loadedJob = null) {
  activeJob = id;
  activeJobPinned = pin;
  document.querySelectorAll('[data-job]').forEach((node) => {
    const selected = node.dataset.job === id;
    node.closest('.item').classList.toggle('is-selected', selected);
    node.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  const job = loadedJob || await api(`/api/jobs/${id}`);
  if (job.read_state === 'unseen') {
    const seen = await api(`/api/jobs/${id}/seen`, {method:'POST', body:'{}'});
    job.seen_at = seen.seen_at;
    job.read_state = 'seen';
    document.querySelector(`[data-job-card="${id}"]`)?.classList.remove('is-unseen');
    document.querySelector(`[data-job="${id}"] .job-unread-dot`)?.remove();
  }
  const [profile, setup] = await Promise.all([api('/api/profile'), api('/api/setup')]);
  const provider = profile.drafting_provider || '';
  const preparation = setup.capabilities?.application_preparation || {ready:false,missing:[]};
  const sourcePriority = {career:0, linkedin:1, facebook:2};
  const sightings = [...job.observations].sort((a,b) => (sourcePriority[a.kind] ?? 9) - (sourcePriority[b.kind] ?? 9) || a.first_seen_at.localeCompare(b.first_seen_at));
  const preferredSighting = sightings[0];
  const matchCompleted = job.analysis_status === 'done';
  const originalPostingUrl = preferredSighting?.url && /^https?:\/\//i.test(preferredSighting.url) ? preferredSighting.url : null;
  const links = sightings.length ? sightings.map((source) =>
    `<div class="job-sighting" data-sighting-id="${source.id}"><div><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.name)}</a> <span class="pill muted">${escapeHtml(source.kind)}</span> ${preferredSighting?.id === source.id ? '<span class="status-badge status-badge--success">Preferred source</span>' : ''}<small>${escapeHtml(source.merge_reason_label)} · first seen ${relativeWhen(source.first_seen_at)}</small></div>${sightings.length > 1 ? `<button type="button" class="text-button" data-split-sighting="${source.id}">Mark as a different job</button>` : ''}</div>`
  ).join('') : '';
  const score = job.score_detail ? JSON.parse(job.score_detail) : null;
  const facts = score?.facts || {};
  const salary = facts.salary_range || '';
  const applicationMethod = job.apply_url
    ? job.apply_url.startsWith('mailto:') ? 'Email the employer' : 'Use the application page'
    : 'Check the original source for application instructions';
  const decisionNote = job.decision_state === 'shortlisted'
    ? 'Shortlisted means you want to keep pursuing this job. It does not send or prepare anything by itself; Automation settings remain separate.'
    : job.decision_state === 'later'
      ? `Hidden from the inbox until ${when(job.snoozed_until)}.`
      : job.decision_state === 'ignored'
        ? `Ignored${job.decision_reason ? `: ${escapeHtml(job.decision_reason)}` : '.'}`
        : 'No decision yet. Opening the job marks it seen without changing this decision.';
  const applicationNote = job.application_progress === 'applied'
    ? 'Applied through a confirmed Job Radar submission.'
    : job.application_progress === 'applied_external'
      ? `Applied elsewhere · ${job.manual_applied_source === 'legacy_state' ? 'migrated from your previous status' : 'marked by you'}.`
      : job.application_progress === 'attention'
        ? 'A submission may have happened, but confirmation is uncertain. Review submission history before retrying.'
        : job.application_progress === 'draft_ready'
          ? 'A prepared application exists and is waiting for review.'
          : job.application_progress === 'preparing'
            ? `${job.preparation_requested_by === 'manual' ? 'Your preparation request' : 'Automatic preparation'} is running in the background. You can keep browsing.`
            : job.application_progress === 'needs_confirmation'
              ? (job.preparation_detail || 'Confirm the application method before drafting work continues.')
              : 'No application has been prepared or recorded yet.';
  const detailDecisionActions = job.decision_state === 'undecided'
    ? `<button type="button" class="secondary" data-detail-decision="shortlisted">Shortlist</button><button type="button" class="secondary" data-detail-decision="later">Later · 3d</button><button type="button" class="danger" data-detail-decision="ignored">Ignore</button>`
    : `<button type="button" class="secondary" data-detail-decision="undecided">Back to inbox</button>${job.decision_state !== 'ignored' ? '<button type="button" class="danger" data-detail-decision="ignored">Ignore</button>' : ''}`;
  const manualAppliedAction = job.application_progress === 'applied_external'
    ? '<button type="button" class="text-button" id="job-manual-applied" data-applied="false">Undo external applied mark</button>'
    : !['applied','attention'].includes(job.application_progress)
      ? '<button type="button" class="text-button" id="job-manual-applied" data-applied="true">I applied elsewhere</button>'
      : '';
  const prepareAction = job.application_progress === 'draft_ready' && job.latest_draft_id
    ? `<button type="button" class="primary" data-open-draft="${job.latest_draft_id}">Open prepared application</button>`
    : ['applied','applied_external','attention'].includes(job.application_progress)
      ? ''
      : job.application_progress === 'preparing'
        ? '<button type="button" class="primary" disabled aria-busy="true">Preparing in background</button>'
        : `<button data-prepare="${id}" class="primary" ${preparation.ready ? '' : 'disabled'}>${job.application_progress === 'needs_confirmation' ? 'Review application method' : 'Prepare application'}</button>`;

  $('#job-detail').setAttribute('tabindex', '-1');
  $('#job-detail').innerHTML = `<div class="job-detail-head"><div><h2>${escapeHtml(job.title)}</h2><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div>
    <div class="item-meta">${escapeHtml(job.work_mode || '')}${job.published_at ? ` · Posted <span${exactTimeTitle(job.published_at)}>${relativeWhen(job.published_at)}</span>` : ''} · Found <span${exactTimeTitle(job.first_seen_at)}>${relativeWhen(job.first_seen_at)}</span></div></div>
    ${job.read_state === 'seen' ? '<span class="status-badge status-badge--neutral">Seen</span>' : ''}</div>

    <div class="job-original-actions">${originalPostingUrl ? `<a class="button-link" href="${escapeHtml(originalPostingUrl)}" target="_blank" rel="noopener noreferrer">Open original posting ↗</a>` : '<span class="hint">No original posting link was saved. The collected description is available below.</span>'}</div>

    <div class="job-lifecycle-grid">
      <section class="job-lifecycle-card"><div class="section-head"><div><h3>Your decision</h3><p>${escapeHtml(jobDecisionLabel(job.decision_state))}</p></div></div><p class="hint">${decisionNote}</p><div class="actions">${detailDecisionActions}</div></section>
      <section class="job-lifecycle-card"><div class="section-head"><div><h3>Application</h3><p>${escapeHtml(applicationProgressLabel(job.application_progress))}</p></div></div><p class="hint">${escapeHtml(applicationNote)}</p>${manualAppliedAction}</section>
      <section class="job-lifecycle-card"><label>Recruiting outcome<select id="job-outcome-control"><option value="none" ${job.recruiting_outcome === 'none' ? 'selected' : ''}>No outcome yet</option><option value="interview" ${job.recruiting_outcome === 'interview' ? 'selected' : ''}>Interview</option><option value="offer" ${job.recruiting_outcome === 'offer' ? 'selected' : ''}>Offer</option><option value="rejected" ${job.recruiting_outcome === 'rejected' ? 'selected' : ''}>Rejected</option></select></label><p class="hint">This is separate from whether you shortlisted or applied.</p></section>
    </div>

    <div class="match-summary-card surface-status">
      <div><span class="status-badge status-badge--${matchCompleted ? fitClassTone(job.fit_class) : 'neutral'}">${escapeHtml(matchCompleted ? fitClassLabel(job.fit_class) : 'Review in progress')}</span><strong>${matchCompleted && job.score != null ? `${job.score}/100` : 'Score pending'}</strong></div>
      ${matchCompleted && job.strongest_signal ? `<p><strong>Strongest signal:</strong> ${escapeHtml(job.strongest_signal.reason)}</p>` : ''}
      ${matchCompleted && job.main_gap ? `<p><strong>Main gap:</strong> ${escapeHtml(job.main_gap.reason)}</p>` : ''}
      ${matchCompleted ? `<p><strong>Evidence confidence:</strong> ${Math.max(0, 100 - Number(job.uncertainty || 0))}%</p>` : '<p class="hint">The previous score is hidden until the new review finishes.</p>'}
      ${matchCompleted && job.missing_evidence?.length ? `<p><strong>Missing evidence:</strong> ${escapeHtml(job.missing_evidence.join(', '))}</p>` : matchCompleted ? '<p class="hint">No major evidence gaps detected.</p>' : ''}
    </div>
    ${links ? `<div class="job-sightings"><div class="job-sightings-head"><strong>Seen on ${job.sighting_count} source${job.sighting_count === 1 ? '' : 's'}</strong><span class="hint">${job.sighting_count > 1 ? `Job Radar combined ${job.sighting_count} sightings so you only review this role once.` : 'One source has reported this role so far.'}</span></div>${links}</div>` : ''}
    ${renderJobAnalysis(job, score)}

    <section class="job-application-method review-section">
      <div class="section-head"><div><h3>How to apply</h3><p class="hint">Check the destination before preparing anything.</p></div></div>
      <p><strong>${escapeHtml(applicationMethod)}</strong></p>
      ${salary ? `<p class="job-detail-salary"><strong>Salary:</strong> ${escapeHtml(salary)} <span>· not included in the match score</span></p>` : ''}
      ${job.apply_url ? `<p><a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener noreferrer">${job.apply_url.startsWith('mailto:') ? 'Application email ↗' : 'Application page ↗'}</a></p>` : ''}
      ${!job.apply_url ? '<p class="hint">No application form has been verified for this posting. Check the original source before sending.</p>' : ''}
    </section>

    <section class="job-prepare-section review-section">
      <div class="section-head"><div><h3>Application preparation</h3><p class="hint">${job.application_progress === 'not_started' ? 'Prepare only after the fit and risks above make sense to you.' : escapeHtml(applicationNote)}</p></div></div>
      <p class="hint">Drafting provider: ${escapeHtml(provider || 'Not configured')} · <button class="text-button" data-tab="settings">Change provider</button></p>
      <p class="processing-disclosure">${escapeHtml(provider ? processingCopy(setup, provider, 'The job posting, your profile details, and approved project evidence') : `Application preparation is optional. To enable it, add ${(preparation.missing || []).join(', ') || 'the application prerequisites'}.`)}</p>
      <div class="actions">${prepareAction}</div>
    </section>

    <details class="detail-disclosure"><summary>Original description</summary><div class="description">${formatDescription(job.description)}</div></details>`;

  $('#job-detail').querySelector('[data-tab="settings"]').addEventListener('click', () => showTab('settings'));
  $('#job-detail').querySelectorAll('[data-split-sighting]').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm('Mark this sighting as a different job? The original source evidence will be preserved.')) return;
    try {
      beginPending(button, 'Splitting…');
      const result = await api(`/api/jobs/${id}/observations/${button.dataset.splitSighting}/split`, {method:'POST'});
      notice('Sighting split into a separate job.');
      await loadJobs();
      activeJob = result.id; activeJobPinned = true; syncJobsHash('push'); await showJob(result.id, true);
    } catch(error) { notice(error.message, true); }
    finally { endPending(button); }
  }));
  $('#job-detail').querySelector('[data-analyze]')?.addEventListener('click', async () => {
    try { await api(`/api/jobs/${id}/analyze`, {method:'POST'}); notice('Match review queued'); await loadJobs(); }
    catch(error) { notice(error.message, true); }
  });
  $('#job-detail').querySelector('[data-dismiss-analysis]')?.addEventListener('click', () => dismissFailedAnalysis(id));

  $('#job-detail').querySelectorAll('[data-detail-decision]').forEach((button) => button.addEventListener('click', async () => {
    const decision = button.dataset.detailDecision;
    const snoozedUntil = decision === 'later' ? new Date(Date.now() + 3 * 86400 * 1000).toISOString() : null;
    try {
      await setJobDecisionWithUndo(job, decision, {snoozedUntil, askReason:decision === 'ignored'});
    } catch(error) { notice(error.message, true); }
  }));

  $('#job-outcome-control').addEventListener('change', async (event) => {
    const previous = job.recruiting_outcome;
    try {
      await api(`/api/jobs/${id}/outcome`, {method:'POST', body:JSON.stringify({outcome:event.target.value})});
      notice(`Recruiting outcome updated to ${recruitingOutcomeLabel(event.target.value)}.`);
      await loadJobs();
    } catch(error) {
      event.target.value = previous;
      notice(error.message, true);
    }
  });

  $('#job-manual-applied')?.addEventListener('click', async (event) => {
    const applied = event.currentTarget.dataset.applied === 'true';
    try {
      await api(`/api/jobs/${id}/manual-applied`, {method:'POST', body:JSON.stringify({applied})});
      notice(applied ? 'Recorded as applied outside Job Radar.' : 'External applied mark removed.');
      await loadJobs();
    } catch(error) { notice(error.message, true); }
  });

  $('#job-detail').querySelector('[data-open-draft]')?.addEventListener('click', async (event) => {
    await showTab('applications');
    await loadApplications(event.currentTarget.dataset.openDraft);
  });

  $('#job-detail').querySelector('[data-prepare]')?.addEventListener('click', async () => {
    const button = $('#job-detail').querySelector('[data-prepare]');
    beginPending(button, 'Checking application method…');
    try {
      const preflight = await api(`/api/jobs/${id}/prepare/preflight`);
      let prepareAnyway = false;
      if (preflight.requires_confirmation) {
        endPending(button);
        prepareAnyway = await confirmPreparationPreflight(preflight);
        if (!prepareAnyway) return;
        beginPending(button, 'Queueing…');
      }
      const result = await api(`/api/jobs/${id}/prepare`, {
        method:'POST',
        body:JSON.stringify({prepare_anyway:prepareAnyway}),
      });
      if (result.status === 'ready' && result.draft_id) {
        notice('A prepared application already exists. Opening it now.');
        await showTab('applications');
        await loadApplications(result.draft_id);
        return;
      }
      notice(result.already_queued
        ? 'Application preparation is already running in the background.'
        : 'Application preparation queued. You can keep browsing; it will appear in Applications when ready.');
      await Promise.all([loadJobs(), loadHomeQueue().catch(() => {})]);
    } catch(error) { notice(error.message, true); }
    finally { if (button.isConnected) endPending(button); }
  });
}

function sourceScanStatusLabel(source) {
  const state = source.scan_state;
  return state === 'queued' ? `Queued · #${source.queue_position}` : ({
    scanning:'Scanning', paused:'LinkedIn checks paused', auto_off:'Automatic checks off', needs_refresh:'Needs refresh',
    not_scanned:'Never checked', success:'Last check completed', empty:'Last check found no jobs',
    failed:'Last check failed', auth_required:'Sign-in needed', interrupted:'Interrupted',
  })[state] || state;
}

function sourceStatusLabel(source) {
  return source.coverage?.label || sourceScanStatusLabel(source);
}

function sourceStatusTone(source) {
  const level = source.coverage?.level;
  if (level === 'good') return 'status-badge--success';
  if (level === 'degraded') return 'status-badge--danger';
  if (level === 'moderate') return 'status-badge--info';
  return 'status-badge--warning';
}


function filterSources(sources) {
  const query = $('#source-query').value.trim().toLowerCase();
  const kind = $('#source-kind').value;
  const status = $('#source-status').value;
  const enabled = $('#source-enabled').value;
  const success = $('#source-success').value;
  const filtered = sources.filter((source) => {
    const haystack = `${source.name} ${source.url} ${source.kind}`.toLowerCase();
    if (query && !haystack.includes(query)) return false;
    if (kind && source.kind !== kind) return false;
    if (enabled === 'enabled' && !source.enabled) return false;
    if (enabled === 'paused' && source.enabled) return false;
    if (success === 'has_success' && !source.last_success_at) return false;
    if (success === 'never' && source.last_success_at) return false;
    const coverageLevel = source.coverage?.level || 'unknown';
    if (status === 'attention' && coverageLevel !== 'degraded') return false;
    if (status === 'limited' && !['limited','moderate'].includes(coverageLevel)) return false;
    if (status === 'healthy' && coverageLevel !== 'good') return false;
    if (status === 'unknown' && coverageLevel !== 'unknown') return false;
    if (status === 'auto_off' && coverageLevel !== 'paused') return false;
    return true;
  });
  const sort = $('#source-sort').value;
  filtered.sort((a, b) => {
    if (sort === 'attention') {
      const rank = (source) => ({degraded:0, limited:1, unknown:2, paused:3, moderate:4, good:5})[source.coverage?.level] ?? 6;
      return rank(a) - rank(b) || a.name.localeCompare(b.name);
    }
    if (sort === 'last_success') return new Date(b.last_success_at || 0) - new Date(a.last_success_at || 0) || a.name.localeCompare(b.name);
    if (sort === 'jobs') return (Number(b.new_job_count) || 0) - (Number(a.new_job_count) || 0) || a.name.localeCompare(b.name);
    if (sort === 'status') return sourceStatusLabel(a).localeCompare(sourceStatusLabel(b)) || a.name.localeCompare(b.name);
    return a.name.localeCompare(b.name);
  });
  return filtered;
}

async function loadSources() {
  clearTimeout(window.sourcePoll);
  const {sources, coverage} = await api('/api/sources/overview');
  const visible = filterSources(sources);
  const pageSize = 12;
  const pages = Math.max(1, Math.ceil(visible.length / pageSize));
  sourcePage = Math.min(sourcePage, pages);
  const pageItems = visible.slice((sourcePage - 1) * pageSize, sourcePage * pageSize);
  const running = sources.filter((source) => source.scan_state === 'scanning').length;
  const waiting = sources.filter((source) => source.scan_state === 'queued').length;
  const unscanned = sources.filter((source) => source.enabled && !source.last_success_at && !['queued', 'scanning', 'paused'].includes(source.scan_state)).length;
  const linkedinPaused = sources.some((source) => source.kind === 'linkedin' && source.scan_state === 'paused');
  $('#source-queue-summary').textContent = `${running} scanning · ${waiting} queued · ${unscanned} never successfully checked. ${linkedinPaused ? 'LinkedIn checks are paused after an account warning; other sources continue.' : 'LinkedIn and Facebook share one browser and run serially.'}`;
  $('#source-coverage-summary').textContent = coverage.label;
  $('#source-coverage-status').textContent = coverage.level === 'good' ? 'Looks normal' : coverage.level === 'degraded' ? 'Needs attention' : 'Some uncertainty';
  $('#source-coverage-status').className = `status-badge ${coverage.level === 'good' ? 'status-badge--success' : coverage.level === 'degraded' ? 'status-badge--danger' : 'status-badge--warning'}`;
  $('#source-coverage-grid').innerHTML = [
    ['LinkedIn searches', coverage.counts.linkedin],
    ['Facebook groups', coverage.counts.facebook],
    ['Company sites', coverage.counts.career],
  ].map(([label,value]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join('');
  $('#source-coverage-gaps').textContent = coverage.gaps.length ? `Coverage gaps: ${coverage.gaps.join(' · ')}.` : 'LinkedIn, Facebook, and company sites are configured.';
  $('#source-result-summary').textContent = `${visible.length} of ${sources.length} sources match these filters`;
  $('#source-page-summary').textContent = visible.length ? `Page ${sourcePage} of ${pages} · showing ${(sourcePage - 1) * pageSize + 1}–${Math.min(sourcePage * pageSize, visible.length)}` : 'No pages';
  $('#sources-prev').disabled = sourcePage <= 1;
  $('#sources-next').disabled = sourcePage >= pages;
  $('#source-list').innerHTML = pageItems.length ? pageItems.map((source) => {
    const total = Number(source.job_count) || 0;
    const recent = Number(source.new_job_count) || 0;
    const state = source.scan_state;
    const status = sourceStatusLabel(source);
    const latest = source.latest_observed_count;
    const cap = Number(source.config?.max_results || source.config?.max_posts || 0);
    const latestText = latest == null ? 'No completed check' : `${latest} postings checked${cap && latest >= cap ? ` · collection limit ${cap} reached` : ''}`;
    const attention = ['degraded','limited','unknown'].includes(source.coverage?.level);
    const sourceType = source.kind === 'career' ? 'Company site' : source.kind === 'linkedin' ? 'LinkedIn search · Past 24 hours · Every 12 hours' : 'Facebook group';
    return `<div class="item source-card" data-source-id="${source.id}">
      <div class="source-card-head"><div><div class="item-title">${escapeHtml(source.name)}</div><div class="item-meta">${escapeHtml(sourceType)} · Last checked ${when(source.last_success_at)}</div></div><span class="status-badge ${sourceStatusTone(source)}">${escapeHtml(status)}</span></div>
      ${attention ? `<p class="source-coverage-detail">${escapeHtml(source.coverage?.detail || '')}</p>` : ''}
      <div class="source-card-bottom"><span class="source-health"><strong>${recent}</strong> new · ${total} saved</span><div class="actions"><button data-scan="${source.id}" ${['scanning','queued','paused'].includes(state) ? 'disabled' : ''}>${state === 'scanning' ? 'Checking…' : state === 'queued' ? 'Queued' : state === 'paused' ? 'Paused' : 'Check now'}</button><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open source ${escapeHtml(source.name)}">Open source ↗</a><label class="source-auto"><input type="checkbox" data-toggle="${source.id}" ${source.enabled ? 'checked' : ''}><span>${state === 'paused' ? 'Scheduled · paused' : 'Automatic'}</span><span class="source-auto-status" aria-live="polite"></span></label></div></div>
      <details class="source-diagnostics"><summary>Details</summary><p class="hint">${escapeHtml(source.coverage?.detail || '')}</p><div class="item-meta">Collector state: ${escapeHtml(sourceScanStatusLabel(source))} · ${escapeHtml(latestText)} · Every ${source.interval_minutes / 60} hours</div><div class="item-meta mono">${escapeHtml(source.url)}</div></details>
    </div>`;
  }).join('') : '<div class="empty">No sources match these filters.</div>';
  document.querySelectorAll('[data-toggle]').forEach((input) => input.addEventListener('change', async () => {
    const desired = input.checked;
    const label = input.closest('.source-auto');
    const feedback = label.querySelector('.source-auto-status');
    input.disabled = true;
    label.setAttribute('aria-busy', 'true');
    feedback.textContent = 'Saving…';
    try {
      await api(`/api/sources/${input.dataset.toggle}`, {method:'PATCH', body:JSON.stringify({enabled:desired})});
      feedback.textContent = 'Saved';
      window.setTimeout(() => {
        if ($('#sources').classList.contains('active')) loadSources().catch(() => {});
      }, 1200);
    } catch(error) {
      input.checked = !desired;
      feedback.textContent = 'Could not save';
      notice(error.message, true);
    } finally {
      input.disabled = false;
      label.removeAttribute('aria-busy');
    }
  }));
  document.querySelectorAll('[data-scan]').forEach((button) => button.addEventListener('click', async () => {
    try {
      beginPending(button, 'Queueing…');
      const result = await api(`/api/sources/${button.dataset.scan}/scan`, {method:'POST'});
      await Promise.all([loadSources(), loadHome()]);
      notice(result.status === 'queued' ? `Scan queued${result.position ? ` at position ${result.position}` : ''}. It will run in the background.` : 'This source is already queued or scanning.');
    }
    catch(error) { notice(error.message, true); }
    finally { endPending(button); }
  }));
  if ($('#sources').classList.contains('active')) window.sourcePoll = setTimeout(() => loadSources().catch(() => {}), 15000);
}

async function loadEmployers() {
  const scope = document.querySelector('input[name="employer-scope"]:checked')?.value || 'watching';
  const query = new URLSearchParams({q: $('#employer-query').value, coverage:scope, page: employerPage, page_size: 18});
  const result = await api(`/api/employers/page?${query}`);
  if (employerPage > result.pages) { employerPage = result.pages; return loadEmployers(); }
  const employers = result.items;
  $('#employer-count').textContent = result.total ? `${result.total} ${scope === 'watching' ? 'employers being checked' : 'employers in the directory'}` : 'No employers found';
  $('#employer-page-summary').textContent = result.total ? `Page ${result.page} of ${result.pages}` : 'No pages';
  $('#employers-prev').disabled = employerPage <= 1;
  $('#employers-next').disabled = employerPage >= result.pages;
  const coverageLabel = (state) => ({watching:'Watching', career_page_needed:'Career page needed', temporarily_unavailable:'Temporarily unavailable', manual_only:'Manual only'})[state] || state;
  $('#employer-list').innerHTML = employers.length ? employers.map((employer) =>
    `<div class="employer surface-readonly" data-employer-id="${employer.id}"><strong>${escapeHtml(employer.name)}</strong><small>${escapeHtml(employer.category)} · ${escapeHtml(coverageLabel(employer.live_coverage))}</small>${employer.career_url ? `<small><a href="${escapeHtml(employer.career_url)}" target="_blank" rel="noopener noreferrer">Career page ↗</a></small>` : '<small>No career page configured</small>'}<button type="button" data-employer-source="${employer.id}" aria-label="${employer.career_url ? 'Edit' : 'Add'} career page for ${escapeHtml(employer.name)}">${employer.career_url ? 'Edit career page' : 'Add career page'}</button><form class="employer-career-form" data-employer-form="${employer.id}" hidden><label>Career page URL<input name="career_url" type="url" required placeholder="https://company.example/careers" value="${escapeHtml(employer.career_url || '')}"></label><p class="field-message employer-career-error" role="alert"></p><div class="actions"><button class="primary" type="submit">Save career page</button><button class="secondary" type="button" data-employer-cancel="${employer.id}">Cancel</button></div></form></div>`
  ).join('') : '<div class="empty">No employers match this search.</div>';
  document.querySelectorAll('[data-employer-source]').forEach((button) => button.addEventListener('click', () => {
    const card = button.closest('.employer');
    const form = card.querySelector('.employer-career-form');
    form.hidden = false;
    button.hidden = true;
    form.elements.career_url.focus();
  }));
  document.querySelectorAll('[data-employer-cancel]').forEach((button) => button.addEventListener('click', () => {
    const card = button.closest('.employer');
    const form = card.querySelector('.employer-career-form');
    form.reset();
    form.querySelector('.employer-career-error').textContent = '';
    form.hidden = true;
    card.querySelector('[data-employer-source]').hidden = false;
  }));
  document.querySelectorAll('[data-employer-form]').forEach((form) => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const save = form.querySelector('button[type="submit"]');
    const errorText = form.querySelector('.employer-career-error');
    errorText.textContent = '';
    try {
      beginPending(save, 'Saving…');
      await api(`/api/employers/${form.dataset.employerForm}`, {method:'PATCH', body:JSON.stringify({career_url:form.elements.career_url.value.trim()})});
      await loadEmployers();
      notice('Career page saved and added to four-hour scans');
    } catch(error) {
      errorText.textContent = error.message;
      notice(error.message, true);
    } finally {
      endPending(save);
    }
  }));
}

const PROVIDER_LABELS = {
  codex:'Codex CLI',
  codex_local:'Codex OSS + Ollama',
  agy:'Antigravity CLI',
  claude:'Claude Code CLI',
  chatgpt_web:'ChatGPT Web',
};

function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider || 'Not selected';
}

function providerAvailability(setup) {
  return {
    template:true,
    codex:Boolean(setup.providers?.codex),
    codex_local:Boolean(setup.providers?.codex && setup.providers?.ollama),
    agy:Boolean(setup.providers?.agy),
    claude:Boolean(setup.providers?.claude),
    chatgpt_web:Boolean(setup.providers?.chatgpt_web),
  };
}

function processingCopy(setup, provider, payloadLabel) {
  if (!provider) return 'Choose a provider to see where this data will be processed.';
  const processing = setup.provider_processing?.[provider] || projectProcessing?.[provider];
  if (provider === 'template') return payloadLabel + ' stays on this Mac. A deterministic local template is used; no AI model receives it.';
  if (!processing) return payloadLabel + ' will be processed by ' + providerLabel(provider) + '.';
  return (processing.remote ? 'Remote processing' : 'Local processing') + ' with ' + processing.label + '. ' +
    payloadLabel + (processing.remote ? ' is sent through the selected provider.' : ' stays on this Mac.');
}

function configureProviderSelect(select, setup, preferred = '') {
  const availability = providerAvailability(setup);
  [...select.options].forEach((option) => {
    if (!option.value) return;
    option.disabled = !availability[option.value];
  });
  if (preferred && availability[preferred] && [...select.options].some((option) => option.value === preferred)) select.value = preferred;
  else if (!availability[select.value]) select.value = '';
  return availability;
}

function closeSetupPanels(except = null) {
  for (const id of ['provider-panel','social-sign-in-panel','telegram-panel','smtp-panel']) {
    const panel = document.getElementById(id);
    if (panel) panel.open = id === except;
  }
}

function renderProviderAvailability(availability) {
  const reasons = {
    codex:'Install and sign in to Codex CLI.',
    codex_local:'Requires both Codex CLI and Ollama on this Mac.',
    agy:'Install and sign in to Antigravity CLI.',
    claude:'Install and sign in to Claude Code CLI.',
    chatgpt_web:'Install Google Chrome to use the saved social browser profile.',
  };
  const missing = Object.entries(availability).filter(([, ready]) => !ready);
  $('#provider-availability').innerHTML = missing.length
    ? `<p class="hint">Unavailable options</p><ul>${missing.map(([key]) => `<li><strong>${escapeHtml(providerLabel(key))}</strong> · ${escapeHtml(reasons[key])}</li>`).join('')}</ul>`
    : '<p class="hint">All supported drafting providers are available.</p>';
}

function commaList(value) {
  return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
}

function setMultiSelect(select, values) {
  const selected = new Set(values || []);
  [...select.options].forEach((option) => { option.selected = selected.has(option.value); });
}

function fitClassLabel(value) {
  return ({strong:'Strong match', stretch:'Worth a stretch', uncertain:'Uncertain', outside:'Outside preferences'})[value] || 'Uncertain';
}

function fitClassTone(value) {
  return value === 'strong' ? 'success' : value === 'stretch' ? 'warning' : value === 'outside' ? 'danger' : 'neutral';
}

function syncStrongThresholdUi(intent) {
  const threshold = Number(intent?.strong_match_threshold ?? 80);
  const score = $('#job-score');
  if (score) {
    score.placeholder = `Any score · strong is ${threshold}+`;
    score.title = `Strong matches use the shared ${threshold}+ threshold`;
  }
  const auto = $('#auto-apply-form')?.elements.threshold;
  if (auto) auto.value = threshold;
}

const SEARCH_MODE_FIELDS = ['role_families','seniority_levels','preferred_locations','max_required_experience_years','work_modes'];
const SEARCH_HARD_KEYS = {role_families:'role_family',seniority_levels:'seniority',preferred_locations:'location',max_required_experience_years:'experience',work_modes:'work_mode'};

function preferenceSourceText(name, intent, mode) {
  if (mode === 'custom') return 'Custom · preserved when your profile changes.';
  const inferred = intent.inferred || {};
  if (name === 'role_families') return intent.role_families?.length
    ? 'Automatic · inferred from your documented roles.'
    : 'Automatic · no clear role family yet, so role matching stays broad.';
  if (name === 'seniority_levels') return intent.seniority_levels?.length
    ? 'Automatic · inferred from role titles and used as ranking guidance.'
    : 'Automatic · no reliable seniority label, so seniority stays neutral.';
  if (name === 'preferred_locations') return intent.preferred_locations?.length
    ? 'Automatic · inferred from your profile. Clear remote jobs remain eligible.'
    : 'Automatic · profile location is not set, so no location gate is applied.';
  if (name === 'max_required_experience_years') {
    const years = inferred.documented_experience_years;
    return intent.max_required_experience_years == null
      ? 'Automatic · dated work history is unavailable, so no experience gate is applied.'
      : `Automatic · inferred from ~${years ?? 0} years of documented experience.`;
  }
  if (name === 'work_modes') return 'Automatic · neutral until you choose a work-mode preference.';
  return 'Automatic';
}

function syncPreferenceModeField(form, name, intent) {
  const mode = intent.preference_modes?.[name] || 'auto';
  const modeControl = form.elements[`auto_${name}`];
  const valueControl = form.elements[name];
  if (modeControl) modeControl.checked = mode === 'auto';
  if (valueControl) valueControl.disabled = mode === 'auto';
  const source = form.querySelector(`[data-preference-source="${name}"]`);
  if (source) source.textContent = preferenceSourceText(name, intent, mode);
}

function renderSearchIntentForm(intent) {
  const form = $('#search-intent-form');
  if (!form) return;
  currentSearchIntent = intent;
  for (const name of ['role_families','preferred_locations','preferred_employers','excluded_employers','negative_keywords']) {
    form.elements[name].value = (intent[name] || []).join(', ');
  }
  setMultiSelect(form.elements.seniority_levels, intent.seniority_levels);
  setMultiSelect(form.elements.work_modes, intent.work_modes);
  form.elements.max_required_experience_years.value = intent.max_required_experience_years ?? '';
  form.elements.minimum_salary.value = intent.minimum_salary ?? '';
  form.elements.salary_currency.value = intent.salary_currency || 'VND';
  form.elements.salary_unknown_ok.checked = intent.salary_unknown_ok !== false;
  const hard = intent.hard_constraints || {};
  for (const key of ['role_family','seniority','location','work_mode','experience','employer','minimum_salary']) {
    form.elements[`hard_${key}`].checked = Boolean(hard[key]);
  }
  for (const name of SEARCH_MODE_FIELDS) syncPreferenceModeField(form, name, intent);

  const roles = intent.role_families?.length ? intent.role_families.join(' / ') : 'Broad role match';
  const locations = intent.preferred_locations?.length ? intent.preferred_locations.join(' + ') : 'Any location';
  const experience = intent.max_required_experience_years == null
    ? 'experience requirement flexible'
    : `jobs requiring up to ${intent.max_required_experience_years} year${intent.max_required_experience_years === 1 ? '' : 's'} experience`;
  const summary = $('#search-intent-summary');
  if (summary) summary.innerHTML = `<strong>${escapeHtml(roles)} · ${escapeHtml(locations)} · ${escapeHtml(experience)} · ${escapeHtml(intent.strong_match_threshold)}+ = strong match</strong><p class="hint">Fields set to Auto follow your profile. Jobs outside your search are still available in Jobs.</p>`;
  const glanceRole = intent.role_families?.[0] || 'Broad role match';
  const glanceExperience = intent.max_required_experience_years == null ? 'Flexible experience' : `Up to ${intent.max_required_experience_years} years required`;
  $('#search-intent-glance').textContent = `${glanceRole} · ${locations} · ${glanceExperience} · Strong ${intent.strong_match_threshold}+`;

  const customCount = SEARCH_MODE_FIELDS.filter((name) => intent.preference_modes?.[name] === 'custom').length;
  setStepStatus('#search-intent-status', customCount ? 'Personalized' : 'Automatic', customCount ? 'success' : 'neutral');
  syncStrongThresholdUi(intent);
}

function renderPreferenceSuggestions(result) {
  const node = $('#preference-suggestions');
  if (!node) return;
  const items = result?.items || [];
  node.innerHTML = items.length ? items.map((item) => `
    <div class="item">
      <div class="item-title">${escapeHtml(item.title)}</div>
      <div class="item-meta">${escapeHtml(item.description)} · based on ${escapeHtml(item.evidence_count)} decisions</div>
      <div class="actions">
        <button type="button" class="primary" data-preference-suggestion="${escapeHtml(item.id)}" data-preference-action="apply">${escapeHtml(item.action_label)}</button>
        <button type="button" class="secondary" data-preference-suggestion="${escapeHtml(item.id)}" data-preference-action="dismiss">Dismiss</button>
      </div>
    </div>`).join('') : '<p class="hint">No repeated preference pattern yet.</p>';
  node.querySelectorAll('[data-preference-suggestion]').forEach((button) => button.addEventListener('click', async () => {
    const id = encodeURIComponent(button.dataset.preferenceSuggestion);
    const action = button.dataset.preferenceAction;
    const pendingButton = beginPending(button, action === 'apply' ? 'Applying…' : 'Dismissing…');
    try {
      const result = await api(`/api/preferences/suggestions/${id}/${action}`, {method:'POST'});
      if (result.preferences) {
        renderSearchIntentForm(result.preferences);
        delete $('#auto-apply-form').dataset.initialized;
        notice('Preference updated from your repeated decisions.');
      } else {
        notice('Suggestion dismissed.');
      }
      renderPreferenceSuggestions(result);
    } catch(error) { notice(error.message, true); }
    finally { if (pendingButton.isConnected) endPending(pendingButton); }
  }));
}

async function loadSettings() {
  const [profile, setup, intent, suggestions] = await Promise.all([
    api('/api/profile'),
    loadSetup(),
    api('/api/search-intent'),
    api('/api/preferences/suggestions'),
  ]);
  renderSearchIntentForm(intent);
  renderPreferenceSuggestions(suggestions);
  const availability = {codex:setup.providers.codex, codex_local:setup.providers.codex && setup.providers.ollama,
    agy:setup.providers.agy, claude:setup.providers.claude, chatgpt_web:setup.providers.chatgpt_web};
  const providerForm = $('#provider-form');
  providerForm.querySelectorAll('option[value]').forEach((option) => { if (option.value) option.disabled = !availability[option.value]; });
  providerForm.elements.provider.value = profile.drafting_provider || '';
  const isChatGpt = providerForm.elements.provider.value === 'chatgpt_web';
  const chatgptSetup = $('#chatgpt-web-setup');
  chatgptSetup.hidden = !isChatGpt;
  const chatgptLoggedIn = Boolean(setup.chatgpt?.logged_in || setup.chatgpt_logged_in);
  const chatgptHint = chatgptSetup.querySelector('.hint');
  const chatgptBtn = $('#chatgpt-web-login');
  if (chatgptLoggedIn) {
    if (chatgptHint) chatgptHint.innerHTML = '<strong>✓ Signed in to ChatGPT.</strong> Job Radar enters prompts and receives replies automatically. Automatic background drafting is paused in this mode.';
    if (chatgptBtn) chatgptBtn.textContent = 'Open ChatGPT in Chrome';
  } else {
    if (chatgptHint) chatgptHint.textContent = 'Click Log in to ChatGPT to open Chrome and sign in. Job Radar will detect your login automatically. Automatic drafting pauses in this mode.';
    if (chatgptBtn) chatgptBtn.textContent = 'Log in to ChatGPT';
  }
  renderProviderAvailability(availability);
  const modelCount = Number(Boolean(profile.drafting_provider)) + Number(Boolean(setup.matching.model));
  setStepStatus('#provider-status', modelCount === 2 ? 'Both configured' : modelCount ? '1 of 2 configured' : 'Choose models', modelCount === 2 ? '' : 'warning');
  setStepStatus('#drafting-status', profile.drafting_provider ? `Using ${providerLabel(profile.drafting_provider)}` : 'Choose a provider', profile.drafting_provider ? '' : 'warning');
  const hasResume = Boolean(profile.name && profile.email);
  const needsSocial = Boolean(profile.drafting_provider && hasResume && (setup.browser.sites.length || setup.browser.connected_sites.length < 2));
  const setupPanels = ['provider-panel','social-sign-in-panel','telegram-panel','smtp-panel'];
  if (!setupPanels.some((id) => $(`#${id}`).open)) {
    closeSetupPanels(modelCount < 2 ? 'provider-panel' : needsSocial ? 'social-sign-in-panel' : null);
  }
  return setup;
}

async function loadProfile() {
  const [profile, setup, cards] = await Promise.all([api('/api/profile'), api('/api/setup'), api('/api/evidence')]);
  renderSocialAuth(setup.browser, setup.linkedin_automation_paused);
  const availability = {codex:setup.providers.codex, codex_local:setup.providers.codex && setup.providers.ollama,
    agy:setup.providers.agy, claude:setup.providers.claude};
  const hasResume = Boolean(profile.name && profile.email);
  $('#resume-panel-label').textContent = hasResume ? 'Your resume details' : 'Import your resume';
  setStepStatus('#resume-status', hasResume ? 'Details ready' : 'Needs details', hasResume ? '' : 'warning');
  $('#resume-panel').open = !hasResume;
  $('#resume-review-actions').hidden = !hasResume;
  $('#resume-panel-help').textContent = hasResume
    ? 'Review the structured details below, or import a newer resume to replace them.'
    : 'Upload a text-based PDF to seed your structured profile. Choose how this import is processed here; application drafting setup is separate.';
  const resumeForm = $('#pdf-resume-form');
  configureProviderSelect(resumeForm.elements.provider, setup, profile.drafting_provider || '');
  $('#resume-processing-disclosure').textContent = processingCopy(setup, resumeForm.elements.provider.value, 'Your resume text');
  resumeForm.elements.provider.onchange = () => {
    $('#resume-processing-disclosure').textContent = processingCopy(setup, resumeForm.elements.provider.value, 'Your resume text');
  };
  const projectCount = cards.filter((card) => card.kind === 'project' && card.approved).length;
  $('#profile-summary').textContent = hasResume
    ? `${profile.name} · ${profile.email}. ${(profile.experience || []).length} previous position${profile.experience?.length === 1 ? '' : 's'} and ${projectCount} selected project${projectCount === 1 ? '' : 's'}.`
    : 'Import a resume PDF or enter your details manually. You can review and edit every field.';
  $('#profile-summary-status').textContent = hasResume ? 'Ready to review' : 'Needs details';
  $('#profile-summary-status').className = statusClass(hasResume ? 'success' : 'warning');
  $('#position-count').textContent = `${(profile.experience || []).length} previous position${profile.experience?.length === 1 ? '' : 's'}`;
  $('#project-count').textContent = `${projectCount} selected project${projectCount === 1 ? '' : 's'}`;
}

function removeRepeatableRow(button) {
  button.closest('.repeatable-row')?.remove();
}

function skillRow(value = '') {
  return `<div class="repeatable-row repeatable-row--simple"><input data-skill value="${escapeHtml(value)}" placeholder="Python"><button type="button" class="text-button danger" data-remove-row aria-label="Remove skill">Remove</button></div>`;
}

function groupRow(label = '', value = '') {
  return `<div class="repeatable-row repeatable-row--group"><input data-skill-group-label value="${escapeHtml(label)}" placeholder="Category, e.g. Programming"><input data-skill-group-values value="${escapeHtml(Array.isArray(value) ? value.join(', ') : value)}" placeholder="Python, C++"><button type="button" class="text-button danger" data-remove-row aria-label="Remove skill category">Remove</button></div>`;
}

function educationRow(item = {}) {
  const value = typeof item === 'string' ? {school:item} : item;
  return `<div class="repeatable-row repeatable-row--education"><input data-education-school value="${escapeHtml(value.school || '')}" placeholder="School"><input data-education-degree value="${escapeHtml(value.degree || '')}" placeholder="Degree"><input data-education-dates value="${escapeHtml(value.dates || '')}" placeholder="Dates"><button type="button" class="text-button danger" data-remove-row aria-label="Remove education">Remove</button></div>`;
}

function simpleRow(attribute, value = '', placeholder = '') {
  return `<div class="repeatable-row repeatable-row--simple"><input ${attribute} value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}"><button type="button" class="text-button danger" data-remove-row>Remove</button></div>`;
}

function bindRepeatableEditor(target) {
  target.querySelectorAll('[data-remove-row]').forEach((button) => button.addEventListener('click', () => removeRepeatableRow(button)));
}

function appendRepeatable(target, html) {
  target.insertAdjacentHTML('beforeend', html);
  bindRepeatableEditor(target);
  target.querySelector('.repeatable-row:last-child input')?.focus();
}

async function loadPersonalDetails() {
  const form = $('#profile-form');
  const loading = $('#personal-loading');
  form.hidden = true;
  loading.hidden = false;
  loading.textContent = 'Loading personal details…';
  try {
    const profile = await api('/api/profile');
    for (const key of ['name','application_name','application_school','given_name','family_name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) form.elements[key].value = profile[key] || '';
    $('#skills-editor').innerHTML = (profile.skills || []).map(skillRow).join('');
    $('#skill-groups-editor').innerHTML = Object.entries(profile.skill_groups || {}).map(([label, value]) => groupRow(label, value)).join('');
    $('#education-editor').innerHTML = (profile.education || []).map(educationRow).join('');
    $('#achievements-editor').innerHTML = (profile.achievements || []).map((value) => simpleRow('data-achievement', value, 'Achievement')).join('');
    $('#links-editor').innerHTML = (profile.links || []).map((value) => simpleRow('data-profile-link type="url"', value, 'https://...')).join('');
    for (const target of [$('#skills-editor'), $('#skill-groups-editor'), $('#education-editor'), $('#achievements-editor'), $('#links-editor')]) bindRepeatableEditor(target);
    form.hidden = false;
    loading.hidden = true;
  } catch (error) {
    loading.textContent = error.message;
    throw error;
  }
}

async function loadMatchingModels() {
  clearTimeout(window.matchingPoll);
  const data = await api('/api/matching/models');
  const select = $('#matching-model-form').elements.model;
  const saved = data.matching.model;
  $('#matching-model-form').dataset.saved = saved || '';
  select.replaceChildren(new Option('Choose an installed model', ''));
  for (const model of data.models) select.add(new Option(`${model.name} · ${(model.size / 1e9).toFixed(1)} GB`, model.name));
  if (saved && !data.models.some((model) => model.name === saved)) select.add(new Option(`${saved} (unavailable)`, saved));
  select.value = saved || '';
  const saveButton = $('#matching-model-form button[type="submit"]');
  saveButton.disabled = !select.value || select.value === saved;
  saveButton.textContent = select.value && select.value === saved ? 'Selected' : 'Use model';
  const state = data.matching.download_state;
  const downloading = state === 'downloading';
  const installed = data.models.some((model) => model.name === data.matching.recommended);
  $('#matching-download').hidden = installed && !downloading;
  $('#matching-download').disabled = downloading;
  $('#matching-download').textContent = downloading ? 'Downloading model…' : state === 'failed' || state === 'cancelled' ? 'Retry model download' : `Download ${data.matching.recommended} (about 2 GB)`;
  $('#matching-download-cancel').hidden = !downloading;
  $('#matching-download-progress').hidden = !downloading;
  $('#matching-download-meter').value = Number(data.matching.download_progress || 0);
  $('#matching-download-label').textContent = downloading
    ? `${Number(data.matching.download_progress || 0)}% · ${data.matching.download_detail || 'Downloading about 2 GB'}`
    : '';
  $('#matching-model-detail').textContent = data.matching.download_error || data.matching.service_error || data.error ||
    (state === 'cancelled' ? 'Download cancelled. You can retry whenever you are ready.' :
     state === 'ready' ? 'Recommended model downloaded and selected.' :
     saved ? '' : 'Choose an installed model to analyze jobs locally.');
  if (downloading && $('#settings').classList.contains('active')) window.matchingPoll = setTimeout(() => loadMatchingModels().then(loadSetup).catch((error) => notice(error.message, true)), 1000);
  return data;
}

async function loadJobAnalysis() {
  const failures = await api('/api/matching/failures');
  $('#matching-failures').hidden = !failures.length;
  $('#matching-failures-title').textContent = `${failures.length} job${failures.length === 1 ? '' : 's'} need attention`;
  $('#matching-retry-all').textContent = `Retry all ${failures.length}`;
  $('#matching-failure-list').innerHTML = failures.map((job) => `<div class="matching-failure-row"><div class="matching-failure-copy"><strong>${escapeHtml(job.title)}</strong><small>${escapeHtml(job.company)} · ${escapeHtml(job.error || 'Local analysis failed')}</small></div><div class="matching-failure-actions"><button type="button" class="secondary" data-matching-open="${job.id}">View job</button><button type="button" class="secondary" data-matching-retry="${job.id}">Retry</button><button type="button" class="secondary" data-matching-dismiss="${job.id}">Dismiss</button></div></div>`).join('');
  $('#matching-failure-list').querySelectorAll('[data-matching-open]').forEach((button) => button.addEventListener('click', async () => {
    try { await openJobInJobs(button.dataset.matchingOpen); scrollNodeIntoView($('#job-detail'), {block:'start'}); }
    catch (error) { notice(error.message, true); }
  }));
  $('#matching-failure-list').querySelectorAll('[data-matching-retry]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try { await api(`/api/jobs/${button.dataset.matchingRetry}/analyze`, {method:'POST'}); await loadJobs(); notice('Job analysis queued.'); }
    catch (error) { button.disabled = false; notice(error.message, true); }
  }));
  $('#matching-failure-list').querySelectorAll('[data-matching-dismiss]').forEach((button) => button.addEventListener('click', () => dismissFailedAnalysis(button.dataset.matchingDismiss)));
  return failures;
}

async function dismissFailedAnalysis(id) {
  try {
    await api(`/api/jobs/${id}/dismiss-analysis`, {method:'POST'});
    await loadJobs();
    notice('Failed analysis dismissed. The job is still available in Jobs.');
  } catch(error) { notice(error.message, true); }
}

$('#matching-model-form select').addEventListener('change', (event) => {
  const button = $('#matching-model-form button[type="submit"]');
  button.disabled = !event.target.value || event.target.value === $('#matching-model-form').dataset.saved;
  button.textContent = button.disabled && event.target.value ? 'Selected' : 'Use model';
});

$('#provider-panel').addEventListener('toggle', (event) => {
  if (event.currentTarget.open) loadMatchingModels().catch((error) => notice(error.message, true));
});

$('#job-analysis-settings').addEventListener('click', () => openSetupPanel('provider-panel'));

$('#matching-retry-all').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = 'Queueing…';
  try {
    const result = await api('/api/matching/retry-failed', {method:'POST'});
    await loadJobs();
    notice(`${result.queued} job${result.queued === 1 ? '' : 's'} queued for match review.`);
  } catch (error) { button.disabled = false; notice(error.message, true); }
});

async function movePosition(positionId, delta) {
  const profile = await api('/api/profile');
  const positions = profile.experience || [];
  const index = positions.findIndex((item) => item.id === positionId);
  const next = index + delta;
  if (index < 0 || next < 0 || next >= positions.length) return;
  [positions[index], positions[next]] = [positions[next], positions[index]];
  profile.experience = positions;
  await api('/api/profile', {method:'PUT', body:JSON.stringify(profile)});
  await loadPositions();
  notice('Work history order updated');
}

async function loadPositions() {
  const positions = await api('/api/positions');
  if (!positions.some((item) => item.id === editingPositionId)) editingPositionId = null;
  $('#position-list').innerHTML = positions.length ? positions.map((item, index) => {
    const editing = item.id === editingPositionId;
    const preview = (item.bullets || []).slice(0, 2);
    return `<div class="item position-card ${editing ? 'is-editing' : ''}" data-position="${item.id}">
      <div class="position-card-head"><div><strong>${escapeHtml(item.role)}</strong><span>${escapeHtml(item.company)} · ${escapeHtml(item.dates)}</span></div>
        <div class="position-order" aria-label="Reorder ${escapeHtml(item.role)}"><button type="button" class="text-button" data-move-position="-1" ${index === 0 ? 'disabled' : ''} aria-label="Move up">↑</button><button type="button" class="text-button" data-move-position="1" ${index === positions.length - 1 ? 'disabled' : ''} aria-label="Move down">↓</button></div></div>
      ${preview.length ? `<ul class="position-preview">${preview.map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join('')}</ul>` : '<p class="hint">No outcome bullets yet.</p>'}
      ${editing ? `<div class="position-editor form-grid"><label>Company<input data-field="company" value="${escapeHtml(item.company)}"></label><label>Role<input data-field="role" value="${escapeHtml(item.role)}"></label><label class="full">Dates<input data-field="dates" value="${escapeHtml(item.dates)}"></label><label class="full">Work and outcomes<textarea data-field="bullets" rows="5">${escapeHtml((item.bullets || []).join('\n'))}</textarea></label></div>` : ''}
      <div class="actions">${editing ? `<button data-save-position="${item.id}" class="primary">Save changes</button><button data-cancel-position="${item.id}" class="secondary">Cancel</button>` : `<button data-edit-position="${item.id}" class="secondary">Edit</button>`}<button data-delete-position="${item.id}" class="secondary danger">Remove</button></div>
    </div>`;
  }).join('') : '<div class="empty">No positions yet. Add your previous jobs above.</div>';

  document.querySelectorAll('[data-edit-position]').forEach((button) => button.addEventListener('click', async () => {
    editingPositionId = button.dataset.editPosition;
    await loadPositions();
    document.querySelector(`[data-position="${editingPositionId}"] input`)?.focus();
  }));
  document.querySelectorAll('[data-cancel-position]').forEach((button) => button.addEventListener('click', async () => {
    editingPositionId = null;
    await loadPositions();
  }));
  document.querySelectorAll('[data-save-position]').forEach((button) => button.addEventListener('click', async () => {
    const row = button.closest('[data-position]');
    const field = (name) => row.querySelector(`[data-field="${name}"]`).value.trim();
    try {
      await api(`/api/positions/${button.dataset.savePosition}`, {method:'PUT', body:JSON.stringify({company:field('company'),role:field('role'),dates:field('dates'),bullets:field('bullets').split('\n').map((x) => x.trim()).filter(Boolean)})});
      editingPositionId = null;
      notice('Position saved');
      await loadPositions();
    } catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-move-position]').forEach((button) => button.addEventListener('click', () => {
    movePosition(button.closest('[data-position]').dataset.position, Number(button.dataset.movePosition)).catch((error) => notice(error.message, true));
  }));
  document.querySelectorAll('[data-delete-position]').forEach((button) => button.addEventListener('click', async () => {
    const row = positions.find((item) => item.id === button.dataset.deletePosition);
    if (!window.confirm(`Remove ${row?.role || 'this position'} at ${row?.company || 'this company'}? This cannot be undone.`)) return;
    try {
      await api(`/api/positions/${button.dataset.deletePosition}`, {method:'DELETE'});
      if (editingPositionId === button.dataset.deletePosition) editingPositionId = null;
      await loadPositions();
      notice('Position removed');
    } catch(error) { notice(error.message, true); }
  }));
}

async function showTab(name, historyMode = 'push') {
  const routeInput = name;
  const [route] = name.split('?');
  const requested = route.split('/');
  const selectedDraft = requested[0] === 'applications' ? requested[1] : null;
  name = requested[0];
  if (name === 'setup') name = 'settings';
  if (name === 'overview') name = 'home';
  if (!document.getElementById(name)?.classList.contains('tab')) name = 'home';
  if (name !== 'settings') clearTimeout(window.setupPoll);
  if (name !== 'settings') clearTimeout(window.matchingPoll);
  if (name !== 'jobs') clearTimeout(window.jobPoll);
  if (name !== 'applications') clearTimeout(window.autoApplyPoll);
  if (name !== 'queue') clearTimeout(window.queuePoll);
  if (name !== 'home') clearTimeout(window.homeQueuePoll);
  if (name !== 'sources') clearTimeout(window.sourcePoll);
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.id === name));
  const target = document.getElementById(name);
  $('#tab-loading').hidden = true;
  const nav = ['personal','experience','projects'].includes(name) ? 'profile' : name;
  document.querySelectorAll('.sidebar [data-tab]').forEach((control) => {
    const active = control.dataset.tab === nav;
    control.classList.toggle('active', active);
    if (active) control.setAttribute('aria-current', 'page');
    else control.removeAttribute('aria-current');
  });
  if (name === 'jobs' && routeInput.includes('?')) readJobsHashState();
  const title = ({home:'Home',guide:'How it works',queue:'Activity',jobs:'Jobs',applications:'Applications',profile:'My profile',settings:'Settings',
    personal:'Personal details',experience:'Work history',projects:'GitHub projects',sources:'Job sources',employers:'Employers'})[name];
  $('#page-title').textContent = title;
  document.title = `${title} · Job Radar`;
  const desiredHash = selectedDraft ? `#applications/${selectedDraft}` : name === 'jobs' ? jobsHash() : `#${name}`;
  if (historyMode === 'replace') history.replaceState({tab:name}, '', desiredHash);
  else if (historyMode === 'push' && location.hash !== desiredHash) history.pushState({tab:name}, '', desiredHash);
  window.scrollTo(0, 0);
  if (!['home', 'settings', 'profile'].includes(name)) refreshSocialAuth().catch((error) => notice(error.message, true));
  const loader = ({home:loadHome,queue:loadQueue,jobs:loadJobs,applications:loadApplications,personal:loadPersonalDetails,
    experience:loadPositions,projects:loadEvidence,sources:loadSources,employers:loadEmployers,profile:loadProfile,settings:loadSettings})[name];
  if (!loader) return;
  setTabLoading(target, true);
  try {
    return await loader(selectedDraft);
  } catch (error) {
    notice(error.message, true);
  } finally {
    setTabLoading(target, false);
  }
}

function providerLabel(value) {
  return ({
    codex:'Codex CLI',
    codex_local:'Codex OSS · local',
    agy:'Antigravity CLI',
    claude:'Claude Code',
    chatgpt_web:'ChatGPT Web',
    template:'Basic template',
    'local template; no model inference':'Basic template',
    'local inference through Codex OSS':'Codex OSS · local',
    'remote inference through local Codex CLI':'Codex CLI',
    'remote inference through local Antigravity CLI':'Antigravity CLI',
    'remote inference through local Claude Code CLI':'Claude Code',
  })[value] || String(value || 'Unknown provider').replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function applicationReviewKey(draft) {
  const outcome = draft.latest_submission?.outcome?.key;
  if (outcome === 'submission_uncertain' || outcome === 'sending') return 'submission_uncertain';
  if (['email_sent','application_submitted'].includes(outcome)) return 'sent';
  if (draft.project_refresh_error || draft.status === 'failed' || draft.review_status === 'failed' ||
      (draft.review_status === 'needs_review' && draft.attempt_detail &&
       (draft.attempt_detail.toLowerCase().includes('failed') || draft.attempt_detail.toLowerCase().includes('stopped')))) {
    return 'failed';
  }
  if (draft.review_status) return draft.review_status;
  if (draft.status === 'sent') return 'sent';
  if (draft.status === 'submission_uncertain') return 'submission_uncertain';
  return 'draft';
}

function applicationReviewLabel(draft) {
  const key = typeof draft === 'string' ? draft : applicationReviewKey(draft);
  return ({
    awaiting_review:'Ready for review',
    needs_review:'Needs changes',
    submission_uncertain:'Submission status uncertain',
    sent:'Sent',
    sending:'Sending',
    regenerating:'Regenerating',
    queued:'Queued',
    preparing:'Preparing',
    needs_confirmation:'Application method needs confirmation',
    failed:'Generation failed',
    draft:'Draft',
  })[key] || String(key || 'Draft').replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function applicationReviewTone(draft) {
  const key = applicationReviewKey(draft);
  if (key === 'sent') return 'success';
  if (key === 'awaiting_review' || key === 'sending' || key === 'preparing' || key === 'regenerating') return 'info';
  if (['needs_review','needs_confirmation','queued','submission_uncertain'].includes(key)) return 'warning';
  if (key === 'failed') return 'danger';
  return 'neutral';
}

function applicationGroup(item) {
  // 1: Generation failed, 2: Need changes, 3: Ready to review, 4: Sent
  if (item.category !== undefined) {
    if (['credits', 'failed', 'quota'].includes(item.category) || item.status === 'failed') return 1;
    if (item.status === 'needs_confirmation' || item.category === 'method' || item.status === 'needs_review') return 2;
    if (['queued', 'preparing'].includes(item.status)) return 3;
    return 3;
  }
  const key = applicationReviewKey(item);
  if (key === 'failed') return 1;
  if (['needs_review', 'needs_confirmation', 'regenerating'].includes(key)) return 2;
  if (['sent', 'submission_uncertain', 'sending'].includes(key) || applicationIsSent(item) || applicationIsUncertain(item)) return 4;
  return 3;
}

function applicationIsSent(draft) {
  return draft.status === 'sent' || draft.review_status === 'sent' ||
    ['email_sent','application_submitted'].includes(draft.latest_submission?.outcome?.key);
}

function applicationIsUncertain(draft) {
  return draft.status === 'submission_uncertain' || draft.review_status === 'submission_uncertain' ||
    draft.latest_submission?.outcome?.key === 'submission_uncertain';
}

function submissionTarget(submission) {
  const destination = submission?.destination || {};
  return destination.email || destination.url || 'No destination recorded';
}

function submissionEvidenceText(submission) {
  if (submission?.receipt) return submission.receipt;
  const key = submission?.outcome?.key;
  if (key === 'submission_uncertain') return 'No confirmation was received. Verify the outcome on the employer site.';
  if (key === 'send_failed') return 'The external service rejected the send before accepting it.';
  return submission?.outcome?.guidance || 'No additional receipt recorded.';
}

function renderSubmissionProof(submission, includePackage = false) {
  if (!submission) return '';
  const outcome = submission.outcome || {};
  const packageData = submission.package || {};
  const message = packageData.message_data || {};
  const formData = packageData.form_data || {};
  const formAnswers = (formData.fields || []).map((field) => {
    const answer = formData.answers?.[String(field.index)] || '';
    if (!answer || field.type === 'file') return '';
    return `<li><strong>${escapeHtml(field.label || field.name || 'Form field')}</strong><span>${escapeHtml(answer)}</span></li>`;
  }).filter(Boolean).join('');
  const attachments = Object.entries(formData.attachments || {}).map(([fieldIndex, assignment]) => {
    if (!assignment || assignment.kind === 'none') return '';
    const field = (formData.fields || []).find((item) => String(item.index) === String(fieldIndex));
    const label = field?.label || field?.name || `Attachment ${fieldIndex}`;
    const name = assignment.kind === 'resume' ? 'Reviewed resume' : assignment.name || 'Uploaded attachment';
    return `<li><strong>${escapeHtml(label)}</strong><span><a href="/api/submissions/${submission.id}/attachments/${encodeURIComponent(fieldIndex)}" target="_blank" rel="noopener noreferrer">${escapeHtml(name)} ↗</a></span></li>`;
  }).filter(Boolean).join('');
  const proof = includePackage ? `<details class="submission-package"><summary>Exact reviewed package</summary>
      <div class="submission-package-grid">
        <div><strong>Resume</strong><p>${submission.resume_available ? `<a href="/api/submissions/${submission.id}/resume" target="_blank" rel="noopener noreferrer">Open exact submitted resume ↗</a>` : 'Exact resume file is unavailable for this older record.'}</p></div>
        <div><strong>Package fingerprint</strong><p class="mono">${escapeHtml((submission.package_hash || '').slice(0, 20))}</p></div>
      </div>
      ${message.subject || message.body ? `<div class="submission-message"><strong>Application message</strong>${message.subject ? `<p><b>Subject:</b> ${escapeHtml(message.subject)}</p>` : ''}${message.body ? `<p class="submission-message-body">${escapeHtml(message.body)}</p>` : ''}</div>` : ''}
      ${formAnswers ? `<div><strong>Reviewed form answers</strong><ul class="submission-form-answers">${formAnswers}</ul></div>` : ''}
      ${attachments ? `<div><strong>Reviewed attachments</strong><ul class="submission-form-answers">${attachments}</ul></div>` : ''}
    </details>` : '';
  return `<section class="submission-proof surface-status">
      <div class="section-head"><div><h4>${escapeHtml(outcome.label || 'Submission recorded')}</h4><p class="hint">${escapeHtml(outcome.guidance || '')}</p></div><span class="status-badge status-badge--${escapeHtml(outcome.tone || 'neutral')}">${escapeHtml(outcome.label || 'Recorded')}</span></div>
      <div class="submission-proof-grid">
        <div><strong>When</strong><span>${escapeHtml(when(submission.sent_at))}</span></div>
        <div><strong>Channel</strong><span>${escapeHtml(outcome.channel || 'Application')}</span></div>
        <div><strong>Destination</strong><span>${escapeHtml(submissionTarget(submission))}</span></div>
        <div><strong>Proof</strong><span>${escapeHtml(submissionEvidenceText(submission))}</span></div>
      </div>
      ${submission.error ? `<details class="submission-technical"><summary>Technical detail</summary><p class="mono">${escapeHtml(submission.error)}</p></details>` : ''}
      ${proof}
    </section>`;
}

function setApplicationWorkspaceView(view) {
  applicationWorkspaceView = ['drafts','automation','activity','history'].includes(view) ? view : 'drafts';
  document.querySelectorAll('[data-app-view]').forEach((button) => {
    const active = button.dataset.appView === applicationWorkspaceView;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-app-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.appPanel !== applicationWorkspaceView;
  });
}

function bindApplicationWorkspace() {
  const root = $('#applications');
  if (!root || root.dataset.workspaceBound === 'true') return;
  root.dataset.workspaceBound = 'true';
  root.querySelectorAll('[data-app-view]').forEach((button) => button.addEventListener('click', () => {
    setApplicationWorkspaceView(button.dataset.appView);
    if (['automation','activity'].includes(button.dataset.appView)) {
      $('#auto-apply-status').textContent = 'Loading…';
      loadAutoApply().catch((error) => notice(error.message, true));
    }
    if (button.dataset.appView === 'history') loadSubmissionHistory().catch((error) => notice(error.message, true));
  }));
  const reloadApplications = () => { applicationsPage = 1; loadApplications().catch((error) => notice(error.message, true)); };
  $('#application-query').addEventListener('input', reloadApplications);
  for (const selector of ['#application-review-filter','#application-company-filter','#application-sent-filter']) {
    $(selector).addEventListener('change', reloadApplications);
  }
  $('#applications-prev').addEventListener('click', () => {
    applicationsPage = Math.max(1, applicationsPage - 1);
    loadApplications().catch((error) => notice(error.message, true));
  });
  $('#applications-next').addEventListener('click', () => {
    applicationsPage = Math.min(applicationsPages, applicationsPage + 1);
    loadApplications().catch((error) => notice(error.message, true));
  });
  $('#submission-history-query').addEventListener('input', () => {
    submissionHistoryPage = 1;
    loadSubmissionHistory().catch((error) => notice(error.message, true));
  });
  $('#submission-history-prev').addEventListener('click', () => {
    submissionHistoryPage = Math.max(1, submissionHistoryPage - 1);
    loadSubmissionHistory().catch((error) => notice(error.message, true));
  });
  $('#submission-history-next').addEventListener('click', () => {
    submissionHistoryPage = Math.min(submissionHistoryPages, submissionHistoryPage + 1);
    loadSubmissionHistory().catch((error) => notice(error.message, true));
  });
}

function populateApplicationCompanyFilter(companies = []) {
  const select = $('#application-company-filter');
  const saved = select.value;
  select.innerHTML = '<option value="">All companies</option>' + companies.map((company) =>
    `<option value="${escapeHtml(company)}">${escapeHtml(company)}</option>`).join('');
  if (companies.includes(saved)) select.value = saved;
}

function visibleApplicationPreparations() {
  if (applicationsPage !== 1) return [];
  const query = $('#application-query').value.trim().toLowerCase();
  const company = $('#application-company-filter').value;
  const review = $('#application-review-filter').value;
  const delivery = $('#application-sent-filter').value;
  if (delivery && delivery !== 'unsent') return [];
  return applicationPreparations.filter((item) => {
    if (review) {
      if (review === 'preparation_failed') {
        if (applicationGroup(item) !== 1) return false;
      } else if (review === 'needs_review' || review === 'needs_confirmation') {
        if (applicationGroup(item) !== 2) return false;
      } else if (['awaiting_review', 'draft', 'preparing', 'queued'].includes(review)) {
        if (applicationGroup(item) !== 3) return false;
      } else {
        return false;
      }
    }
    return (!query || `${item.job_title} ${item.company} ${providerLabel(item.provider)}`.toLowerCase().includes(query)) &&
      (!company || item.company === company);
  });
}

function renderApplicationList(total = applicationsTotal) {
  const preparations = visibleApplicationPreparations();
  const summary = $('#application-list-summary');
  summary.textContent = `${total} prepared application${total === 1 ? '' : 's'}` +
    (preparations.length ? ` · ${preparations.length} waiting for a draft` : '') +
    (total ? ` · page ${applicationsPage} of ${applicationsPages}` : '');
  $('#applications-page-summary').textContent = total ? `Page ${applicationsPage} of ${applicationsPages}` : 'No applications';
  $('#applications-prev').disabled = applicationsPage <= 1;
  $('#applications-next').disabled = applicationsPage >= applicationsPages;
  const list = $('#application-list');
  if (!applicationDrafts.length && !preparations.length) {
    list.innerHTML = total ? '<div class="empty">No applications on this page.</div>' :
      '<div class="panel empty"><p>No applications match these filters.</p><button id="applications-browse-jobs" class="primary">Browse jobs →</button></div>';
    $('#applications-browse-jobs')?.addEventListener('click', () => showTab('jobs'));
    return;
  }
  const combined = [
    ...preparations.map((item) => ({
      type: 'preparation',
      data: item,
      group: applicationGroup(item),
      time: new Date(item.updated_at || item.created_at || 0).getTime(),
    })),
    ...applicationDrafts.map((draft) => ({
      type: 'draft',
      data: draft,
      group: applicationGroup(draft),
      time: new Date(draft.updated_at || draft.created_at || 0).getTime(),
    })),
  ];
  combined.sort((a, b) => {
    if (a.group !== b.group) return a.group - b.group;
    return b.time - a.time;
  });
  list.innerHTML = combined.map((entry) => {
    if (entry.type === 'preparation') {
      const item = entry.data;
      const selected = item.vacancy_id === activePreparationJobId;
      const inProgress = ['queued', 'preparing'].includes(item.status);
      const tone = ['credits','failed','quota'].includes(item.category) ? 'danger' : inProgress ? 'info' : 'warning';
      const statusNote = item.status === 'queued' ? 'Queued in background' : item.status === 'preparing' ? 'Preparing draft' : 'No draft yet';
      return `<button type="button" class="item clickable application-card surface-action ${selected ? 'is-selected' : ''}" data-preparation="${item.vacancy_id}" aria-pressed="${selected ? 'true' : 'false'}">
        <div class="item-title">${escapeHtml(item.job_title)}</div>
        <div class="application-card-status"><span class="status-badge status-badge--${tone}">${escapeHtml(item.label)}</span></div>
        <div class="item-meta">${escapeHtml(item.company)} · ${item.score} match · ${statusNote}</div>
        <div class="item-meta">Updated ${when(item.updated_at)}</div>
      </button>`;
    }
    const draft = entry.data;
    const selected = draft.id === activeApplicationId;
    const tone = applicationReviewTone(draft);
    const delivery = draft.latest_submission?.outcome?.label;
    return `<button type="button" class="item clickable application-card surface-action ${selected ? 'is-selected' : ''}" data-application="${draft.id}" aria-pressed="${selected ? 'true' : 'false'}">
      <div class="item-title">${escapeHtml(draft.job_title)}</div>
      <div class="application-card-status"><span class="status-badge status-badge--${tone}">${escapeHtml(applicationReviewLabel(draft))}</span></div>
      <div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(providerLabel(draft.provider_mode))}</div>
      <div class="item-meta">Updated ${when(draft.updated_at)}${delivery ? ` · ${escapeHtml(delivery)}` : ''}</div>
    </button>`;
  }).join('');
  list.querySelectorAll('[data-application]').forEach((node) => node.addEventListener('click', async () => {
    const draft = applicationDrafts.find((d) => d.id === node.dataset.application);
    const screen = (draft && (applicationIsSent(draft) || applicationIsUncertain(draft))) ? 'package' : 'first-glance';
    activeApplicationScreen = screen;
    await showApplication(node.dataset.application, screen);
    if (window.matchMedia('(max-width: 900px)').matches) scrollNodeIntoView($('#application-detail'), {block:'start'});
    $('#application-detail').focus({preventScroll:true});
  }));
  list.querySelectorAll('[data-preparation]').forEach((node) => node.addEventListener('click', () => {
    const item = applicationPreparations.find((entry) => entry.vacancy_id === node.dataset.preparation);
    if (item) showPreparationIssue(item);
  }));
}

function showApplicationRegenerating(id) {
  const draft = applicationDrafts.find((item) => item.id === id);
  if (draft) {
    draft.review_status = 'regenerating';
    renderApplicationList();
  }
  const detail = $('#application-detail');
  const badge = detail.querySelector('.application-review-header .status-badge');
  if (badge) {
    badge.className = 'status-badge status-badge--info';
    badge.textContent = 'Regenerating';
  }
  const packageStatus = detail.querySelector('.application-status-card span');
  if (packageStatus) packageStatus.textContent = 'Regenerating';
}

async function loadApplications(selectedId = null, preferredScreen = null) {
  bindApplicationWorkspace();
  const query = new URLSearchParams({
    page: applicationsPage,
    page_size: 25,
    q: $('#application-query').value.trim(),
    review: $('#application-review-filter').value,
    company: $('#application-company-filter').value,
    delivery: $('#application-sent-filter').value,
  });
  const [result, preparations] = await Promise.all([
    api(`/api/applications/page?${query}`), api('/api/application-preparations')
  ]);
  applicationDrafts = result.items;
  applicationPreparations = preparations;
  applicationsTotal = result.total;
  applicationsPage = result.page;
  applicationsPages = result.pages;
  populateApplicationCompanyFilter([...new Set([...(result.companies || []), ...preparations.map((item) => item.company)])]);
  if (selectedId) {
    if (applicationDrafts.some((draft) => draft.id === selectedId)) {
      activeApplicationId = selectedId;
    } else {
      activeApplicationId = applicationDrafts[0]?.id || null;
      if (location.hash.startsWith('#applications/')) {
        history.replaceState({tab:'applications'}, '', activeApplicationId ? `#applications/${activeApplicationId}` : '#applications');
      }
    }
    activePreparationJobId = null;
    setApplicationWorkspaceView('drafts');
  } else if (!activeApplicationId || !applicationDrafts.some((draft) => draft.id === activeApplicationId)) {
    activeApplicationId = applicationDrafts[0]?.id || null;
    if (location.hash.startsWith('#applications/')) {
      history.replaceState({tab:'applications'}, '', activeApplicationId ? `#applications/${activeApplicationId}` : '#applications');
    }
  }
  const completedPreparation = activePreparationJobId && applicationDrafts.find((draft) => draft.vacancy_id === activePreparationJobId);
  if (completedPreparation) {
    activePreparationJobId = null;
    activeApplicationId = completedPreparation.id;
  }
  renderApplicationList(result.total);
  if (activeApplicationId) await showApplication(activeApplicationId, preferredScreen);
  else if (completedPreparation) await showApplication(completedPreparation.id, preferredScreen);
  else if (activePreparationJobId) {
    const issue = applicationPreparations.find((item) => item.vacancy_id === activePreparationJobId);
    if (issue) showPreparationIssue(issue);
  } else {
    const detail = $('#application-detail');
    if (detail) detail.innerHTML = '<div class="panel empty"><p>No application drafts to display.</p></div>';
  }
  clearTimeout(window.applicationPreparationPoll);
  if (applicationPreparations.some((item) => ['queued','preparing'].includes(item.status)) &&
      $('#applications').classList.contains('active')) {
    window.applicationPreparationPoll = setTimeout(() => loadApplications().catch((error) => notice(error.message, true)), 3000);
  }
}

async function loadSubmissionHistory() {
  const query = new URLSearchParams({
    page: submissionHistoryPage,
    page_size: 25,
    q: $('#submission-history-query').value.trim(),
  });
  const result = await api(`/api/submissions/page?${query}`);
  submissionHistoryPage = result.page;
  submissionHistoryPages = result.pages;
  $('#submission-history-page-summary').textContent = result.total ? `Page ${result.page} of ${result.pages} · ${result.total} records` : 'No submissions';
  $('#submission-history-prev').disabled = result.page <= 1;
  $('#submission-history-next').disabled = result.page >= result.pages;
  const list = $('#submission-history-list');
  list.innerHTML = result.items.length ? result.items.map((submission) => `
    <article class="item submission-history-card">
      <div class="section-head"><div><div class="item-title">${escapeHtml(submission.job_title)} · ${escapeHtml(submission.company)}</div><div class="item-meta">${escapeHtml(when(submission.sent_at))} · ${escapeHtml(submission.outcome?.channel || 'Application')}</div></div><span class="status-badge status-badge--${escapeHtml(submission.outcome?.tone || 'neutral')}">${escapeHtml(submission.outcome?.label || 'Recorded')}</span></div>
      <p class="item-meta">${escapeHtml(submissionTarget(submission))}</p>
      <p>${escapeHtml(submissionEvidenceText(submission))}</p>
      <div class="actions"><button type="button" data-history-application="${submission.draft_id}" class="secondary">Open application</button>${submission.resume_available ? `<a class="button-link" href="/api/submissions/${submission.id}/resume" target="_blank" rel="noopener noreferrer">Exact resume ↗</a>` : ''}</div>
    </article>`).join('') : '<p class="empty">No submission history matches this search.</p>';
  list.querySelectorAll('[data-history-application]').forEach((button) => button.addEventListener('click', () => loadApplications(button.dataset.historyApplication).catch((error) => notice(error.message, true))));
}

async function loadAutoApply() {
  clearTimeout(window.autoApplyPoll);
  const [data, aiFailures] = await Promise.all([
    api('/api/auto-apply?summary_only=true'), api('/api/ai/failures')
  ]);
  const failedAiTotal = ['preparations','project_briefs','draft_projects','regenerations']
    .reduce((total, key) => total + Number(aiFailures[key] || 0), 0);
  $('#ai-failures-panel').hidden = !failedAiTotal && !aiFailures.running;
  $('#ai-failures-summary').textContent = aiFailures.running
    ? `Retrying failed AI work… ${failedAiTotal} item${failedAiTotal === 1 ? '' : 's'} still need attention.`
    : `${failedAiTotal} retryable item${failedAiTotal === 1 ? '' : 's'}: ${aiFailures.preparations} draft preparations, ${aiFailures.project_briefs} project briefs, ${aiFailures.draft_projects} resume updates, ${aiFailures.regenerations} draft revisions.`;
  $('#ai-retry-all').disabled = !failedAiTotal || aiFailures.running;
  const form = $('#auto-apply-form');
  if (!form.dataset.initialized) {
    form.elements.enabled.checked = data.enabled;
    form.elements.threshold.value = data.threshold;
    form.elements.include_shortlisted.checked = Boolean(data.include_shortlisted);
    form.elements.max_job_age_days.value = data.max_job_age_days;
    form.elements.require_verified_destination.checked = data.require_verified_destination !== false;
    form.elements.require_preferred_location.checked = Boolean(data.require_preferred_location);
    form.elements.max_auto_drafts_per_day.value = data.max_auto_drafts_per_day;
    form.elements.max_review_notifications_per_day.value = data.max_review_notifications_per_day;
    form.dataset.initialized = 'true';
  }
  $('#auto-apply-status').textContent = data.enabled
    ? `On · ${data.threshold}+ · ${data.daily_auto_drafts_used}/${data.max_auto_drafts_per_day} drafts today`
    : 'Off';
  $('#auto-apply-status').className = statusClass(data.enabled ? 'success' : 'neutral');
  const existingButton = $('#queue-existing-drafts');
  existingButton.disabled = !data.enabled;
  $('#existing-draft-count').textContent = !data.enabled ? 'Enable and save automatic drafts first.'
    : `Check saved jobs against the policy when you include them. ${data.daily_auto_drafts_remaining} automatic draft slot${data.daily_auto_drafts_remaining === 1 ? '' : 's'} left today.`;
  const activity = $('#auto-apply-activity');
  activity.innerHTML = data.recent.length ? `${data.recent.map((item) =>
    `<div class="item"><div class="item-title">${escapeHtml(item.title)} · ${escapeHtml(item.company)} <span class="status-badge status-badge--${applicationReviewTone({review_status:item.status})}">${escapeHtml(applicationReviewLabel(item.status))}</span></div><div class="item-meta">${item.requested_by === 'manual' ? 'Requested by you' : item.analysis_status === 'done' && item.score != null ? `${escapeHtml(item.score)}/100 automatic match` : 'Automatic match'} · ${escapeHtml(item.detail || '')}</div><div class="actions">${item.draft_id ? `<button type="button" data-auto-draft="${item.draft_id}">Open application</button>` : `<button type="button" data-auto-job="${item.vacancy_id}">Open job</button>`}</div></div>`
  ).join('')}` : '<p class="hint">No preparation activity yet.</p>';
  activity.querySelectorAll('[data-auto-draft]').forEach((button) => button.addEventListener('click', () => loadApplications(button.dataset.autoDraft).catch((error) => notice(error.message, true))));
  activity.querySelectorAll('[data-auto-job]').forEach((button) => button.addEventListener('click', async () => { await openJobInJobs(button.dataset.autoJob); }));
  const preparationRunning = data.recent.some((item) => ['queued','preparing','needs_confirmation'].includes(item.status));
  if ((data.enabled || preparationRunning || aiFailures.running) && $('#applications').classList.contains('active') && ['automation','activity'].includes(applicationWorkspaceView)) {
    window.autoApplyPoll = setTimeout(() => loadAutoApply().catch((error) => notice(error.message, true)), 5000);
  }
}

$('#ai-retry-all').addEventListener('click', async (event) => {
  const button = beginPending(event.currentTarget, 'Queuing retries…');
  try {
    const result = await api('/api/ai/retry-failed', {method:'POST'});
    await loadAutoApply();
    notice(result.queued ? `Retrying ${result.queued} failed AI item${result.queued === 1 ? '' : 's'}. Review drafts before sending.` : 'No failed AI work remains.');
  } catch (error) { notice(error.message, true); }
  finally { if (button.isConnected) endPending(button); }
});

$('#auto-apply-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  const form = event.target;
  try {
    const payload = {
      enabled: form.elements.enabled.checked,
      threshold: Number(form.elements.threshold.value),
      include_shortlisted: form.elements.include_shortlisted.checked,
      max_job_age_days: Number(form.elements.max_job_age_days.value),
      require_verified_destination: form.elements.require_verified_destination.checked,
      require_preferred_location: form.elements.require_preferred_location.checked,
      max_auto_drafts_per_day: Number(form.elements.max_auto_drafts_per_day.value),
      max_review_notifications_per_day: Number(form.elements.max_review_notifications_per_day.value),
    };
    const result = await api('/api/auto-apply', {method:'PUT', body:JSON.stringify(payload)});
    delete form.dataset.initialized;
    await loadAutoApply();
    notice(result.enabled
      ? `Automatic drafts enabled with the saved policy. Up to ${result.max_auto_drafts_per_day} drafts per day; every application still waits for approval.`
      : 'Automatic draft preparation paused.');
  } catch (error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#queue-existing-drafts').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api('/api/auto-apply/queue-existing', {method:'POST'});
    await loadAutoApply();
    notice(result.queued ? `${result.queued} existing job${result.queued === 1 ? '' : 's'} queued under the saved automation policy.` : 'No existing jobs currently pass the saved automation policy.');
  } catch (error) { notice(error.message, true); button.disabled = false; }
});

function applicationActionLabel(destination) {
  return ({email:'Email', web_form:'Web form', linkedin_easy_apply:'LinkedIn Easy Apply', manual:'Manual review', unknown:'Manual review'})[destination.action_type]
    || (destination.kind === 'email' ? 'Email' : destination.kind === 'web' ? 'Web form' : 'Manual review');
}

function isLinkedInJobPostingUrl(value) {
  try {
    const url = new URL(value);
    return (url.hostname === 'linkedin.com' || url.hostname.endsWith('.linkedin.com')) && url.pathname.toLowerCase().startsWith('/jobs/');
  } catch { return false; }
}

function applicationActionTarget(destination) {
  return destination.email || destination.url || 'No verified destination';
}

function applicationFormFieldLabel(field) {
  return field.label || field.name || `Field ${field.index}`;
}

function renderApplicationFormField(field, draft) {
  const key = String(field.index);
  const label = applicationFormFieldLabel(field);
  const required = field.required ? ' <span class="required-mark" aria-hidden="true">*</span>' : '';
  const answer = String(draft.form_data.answers?.[key] || '');

  if (field.type === 'file') {
    const assignment = draft.form_data.attachments?.[key] || {};
    const uploaded = assignment.kind === 'uploaded';
    return `<label class="application-form-field">${escapeHtml(label)}${required}
      <select data-attachment="${field.index}" data-draft-field>
        <option value="" ${!assignment.kind ? 'selected' : ''}>Choose an attachment</option>
        <option value="resume" ${assignment.kind === 'resume' ? 'selected' : ''}>Generated resume PDF</option>
        <option value="uploaded" ${uploaded ? 'selected' : ''}>Custom PDF</option>
        ${!field.required ? `<option value="none" ${assignment.kind === 'none' ? 'selected' : ''}>No file</option>` : ''}
      </select>
      <span class="application-attachment-upload" data-attachment-upload="${field.index}" ${uploaded ? '' : 'hidden'}>
        <input type="file" accept="application/pdf,.pdf" data-attachment-file="${field.index}" data-draft-field aria-label="Upload PDF for ${escapeHtml(label)}">
        <span class="hint">${uploaded && assignment.name ? `Current custom file: ${escapeHtml(assignment.name)}. Choose another PDF to replace it.` : 'Choose a PDF smaller than 10 MB.'}</span>
      </span>
    </label>`;
  }

  const options = Array.isArray(field.options) ? field.options.map((option) => {
    if (typeof option === 'string') return {value:option, label:option};
    return {value:String(option.value ?? option.label ?? ''), label:String(option.label ?? option.text ?? option.value ?? '')};
  }).filter((option) => option.value) : [];

  if (options.length) {
    const known = options.some((option) => option.value === answer);
    return `<label class="application-form-field">${escapeHtml(label)}${required}
      <select data-answer="${field.index}" data-draft-field ${field.required ? 'required' : ''}>
        <option value="">Choose an option</option>
        ${!known && answer ? `<option value="${escapeHtml(answer)}" selected>${escapeHtml(answer)}</option>` : ''}
        ${options.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === answer ? 'selected' : ''}>${escapeHtml(option.label || option.value)}</option>`).join('')}
      </select>
    </label>`;
  }

  if (field.type === 'radio' || field.type === 'checkbox') {
    const checked = ['yes','true','checked','1'].includes(answer.toLowerCase());
    const group = field.type === 'radio' ? ` name="review-radio-${escapeHtml(field.name || 'group')}"` : '';
    return `<label class="application-form-choice"><input type="${field.type}"${group} data-answer="${field.index}" data-draft-field value="yes" ${checked ? 'checked' : ''}><span>${escapeHtml(label)}${required}</span></label>`;
  }

  if (field.type === 'textarea' || answer.length > 120) {
    return `<label class="application-form-field">${escapeHtml(label)}${required}<textarea data-answer="${field.index}" data-draft-field rows="3" ${field.required ? 'required' : ''}>${escapeHtml(answer)}</textarea></label>`;
  }

  const inputType = ['email','tel','url','number','date'].includes(field.type) ? field.type : 'text';
  return `<label class="application-form-field">${escapeHtml(label)}${required}<input type="${inputType}" data-answer="${field.index}" data-draft-field value="${escapeHtml(answer)}" ${field.required ? 'required' : ''}></label>`;
}

function applicationAlert(kind, title, items) {
  if (!items?.length) return '';
  const role = kind === 'danger' ? 'alert' : 'status';
  return `<div class="application-alert application-alert--${kind}" role="${role}"><strong>${escapeHtml(title)}</strong><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>`;
}

function confirmApplicationSend(draft) {
  const dialog = $('#application-send-confirm');
  const destination = draft.destination || {};
  const target = `${applicationActionLabel(destination)} · ${applicationActionTarget(destination)}`;
  const fields = draft.form_data?.fields?.length || 0;
  const attachments = Object.keys(draft.form_data?.attachments || {}).length;
  $('#application-send-confirm-target').textContent = target;
  $('#application-send-confirm-summary').textContent = `${draft.job_title} at ${draft.company} · ${providerLabel(draft.provider_mode || draft.provider)} · ${fields} form field${fields === 1 ? '' : 's'} reviewed · ${attachments} attachment${attachments === 1 ? '' : 's'}`;
  if (typeof dialog.showModal !== 'function') {
    return Promise.resolve(window.confirm(`Approve and send to ${applicationActionTarget(destination)}?`));
  }
  if (dialog.open) dialog.close('cancel');
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true});
    dialog.showModal();
  });
}

function applicationPreparationExplanation(draft) {
  const rechecking = ['pending','running'].includes(draft.job_analysis_status);
  const score = draft.job_score == null ? '' : rechecking
    ? ` Its earlier saved match score was ${draft.job_score}/100; a new match review is in progress.`
    : ` Its saved match score is ${draft.job_score}/100.`;
  const skills = draft.job_score_detail?.matched_skills;
  const assessment = Array.isArray(skills) && skills.length ? ` Matching skills: ${skills.join(', ')}.` : '';
  if (draft.preparation_requested_by === 'manual') {
    return `You chose Prepare application for this saved job.${score}${assessment}${draft.preparation_approved_without_destination ? ' You chose to continue after the application method warning.' : ''}`;
  }
  if (draft.preparation_requested_by === 'automation') {
    return `Automatic draft preparation created this from the saved job.${score}${assessment} The policy settings used at the time were not saved with this draft. Nothing is sent until you approve it.`;
  }
  return `Prepared from this saved job. The original preparation trigger was not recorded.${score}${assessment}`;
}

async function openJobInJobs(jobId) {
  applyJobFilterSnapshot({inbox:'all', fit:'all'});
  $('#job-view-select').value = '';
  $('#job-delete-view').disabled = true;
  jobsPage = 1;
  activeJob = jobId;
  activeJobPinned = true;
  await showTab('jobs');
  $('#job-detail').focus({preventScroll:true});
}

async function openApplicationJob(jobId) {
  await openJobInJobs(jobId);
}

function showPreparationIssue(item) {
  if ($('#applications').classList.contains('active') && location.hash !== '#applications') {
    history.replaceState({tab:'applications'}, '', '#applications');
  }
  setApplicationWorkspaceView('drafts');
  activeApplicationId = null;
  activePreparationJobId = item.vacancy_id;
  renderApplicationList();
  const detail = $('#application-detail');
  const inProgress = ['queued', 'preparing'].includes(item.status);
  const tone = ['credits','failed','quota'].includes(item.category) ? 'danger' : inProgress ? 'info' : 'warning';
  const alertTitle = item.status === 'queued' ? 'Preparation queued' : item.status === 'preparing' ? 'Preparation in progress' : 'No draft was created';
  detail.innerHTML = `<div class="application-review-header">
      <div><p class="eyebrow">APPLICATION PREPARATION</p><h2>${escapeHtml(item.job_title)}</h2><p class="item-meta">${escapeHtml(item.company)} · ${item.score} match</p></div>
      <span class="status-badge status-badge--${tone}">${escapeHtml(item.label)}</span>
    </div>
    <div class="application-alert application-alert--${tone}"><strong>${escapeHtml(alertTitle)}</strong><p>${escapeHtml(item.reason)}</p></div>
    <p class="hint">${item.provider ? `Drafting with ${escapeHtml(providerLabel(item.provider))}. ` : ''}No application has been sent.</p>
    <div class="actions">
      ${inProgress ? `<button type="button" class="primary" disabled aria-busy="true">${item.status === 'queued' ? 'Queued in background' : 'Preparing in background'}</button>` : (item.retryable ? '<button type="button" class="primary" data-retry-preparation>Retry preparation</button>' : '')}
      <button type="button" class="secondary" data-preparation-job>View job in Jobs</button>
      <button type="button" class="secondary danger" data-ignore-preparation>Ignore job</button>
    </div>`;
  detail.querySelector('[data-ignore-preparation]')?.addEventListener('click', async () => {
    try {
      await api(`/api/jobs/${item.vacancy_id}/decision`, {method:'POST', body:JSON.stringify({decision:'ignored', reason:'Preparation dismissed'})});
      notice('Job marked as ignored.', false);
      activePreparationJobId = null;
      detail.innerHTML = '<div class="panel empty"><p>Job marked as ignored.</p></div>';
      await loadApplications();
    } catch (error) { notice(error.message, true); }
  });
  detail.querySelector('[data-preparation-job]').addEventListener('click', () =>
    openApplicationJob(item.vacancy_id).catch((error) => notice(error.message, true)));
  detail.querySelector('[data-retry-preparation]')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    beginPending(button, 'Checking application method…');
    try {
      const preflight = await api(`/api/jobs/${item.vacancy_id}/prepare/preflight${item.provider ? `?provider=${encodeURIComponent(item.provider)}` : ''}`);
      let prepareAnyway = false;
      if (preflight.requires_confirmation) {
        endPending(button);
        prepareAnyway = await confirmPreparationPreflight(preflight);
        if (!prepareAnyway) return;
        beginPending(button, 'Queueing…');
      }
      const result = await api(`/api/jobs/${item.vacancy_id}/prepare`, {
        method:'POST', body:JSON.stringify({provider:item.provider || undefined, prepare_anyway:prepareAnyway}),
      });
      if (result.status === 'ready' && result.draft_id) {
        await loadApplications(result.draft_id);
        return;
      }
      notice('Application preparation queued. It will appear here when the draft is ready.');
      await loadApplications();
    } catch (error) { notice(error.message, true); }
    finally { if (button.isConnected) endPending(button); }
  });
  detail.focus({preventScroll:true});
}

let activeApplicationScreen = 'first-glance';

async function showApplication(id, preferredScreen = null) {
  if ($('#applications').classList.contains('active') && location.hash !== `#applications/${id}`) history.replaceState({tab:'applications'}, '', `#applications/${id}`);
  setApplicationWorkspaceView('drafts');
  activeApplicationId = id;
  activePreparationJobId = null;
  renderApplicationList();
  const applicationList = $('#application-list');
  const selectedCard = Array.from(applicationList.querySelectorAll('[data-application]'))
    .find((card) => card.dataset.application === id);
  if (selectedCard) {
    const offset = selectedCard.getBoundingClientRect().top - applicationList.getBoundingClientRect().top;
    applicationList.scrollTop += offset - Math.max(0, (applicationList.clientHeight - selectedCard.clientHeight) / 2);
  }

  let draft, profile;
  try {
    [draft, profile] = await Promise.all([api(`/api/applications/${id}`), api('/api/profile')]);
  } catch (error) {
    if (error.status === 404) {
      activeApplicationId = null;
      if (location.hash.startsWith('#applications/')) {
        history.replaceState({tab:'applications'}, '', '#applications');
      }
      if (applicationDrafts && applicationDrafts.length > 0) {
        const next = applicationDrafts.find((d) => d.id !== id) || applicationDrafts[0];
        if (next && next.id !== id) {
          return await showApplication(next.id, preferredScreen);
        }
      }
      const detail = $('#application-detail');
      if (detail) detail.innerHTML = '<div class="panel empty"><p>This draft is no longer available.</p></div>';
      renderApplicationList();
      return;
    }
    throw error;
  }
  const job = draft.job || await api(`/api/jobs/${draft.vacancy_id}`).catch(() => ({}));
  let currentScreen = preferredScreen || 'first-glance';
  activeApplicationScreen = currentScreen;

  const webGenerator = profile.drafting_provider === 'chatgpt_web' || draft.provider === 'chatgpt_web';
  const resume = draft.resume_data || {};
  const message = draft.message_data || {};
  const destination = draft.destination || {kind:'manual', action_type:'unknown'};
  const formData = draft.form_data || {fields:[], answers:{}, attachments:{}};
  draft.form_data = formData;
  const projects = resume.projects || [];
  const bulletSource = draft.resume_bullet_source || {experience:[], projects:[], achievements:[]};
  const recentChanges = applicationReviewChanges[id] || [];
  const linkedinManual = draft.job_source_kind === 'linkedin' && !['web','email','linkedin_easy_apply'].includes(destination.kind);
  const linkedinPaused = linkedinManual && draft.linkedin_automation_paused;
  const linkedinEasyApply = destination.kind === 'linkedin_easy_apply' || destination.action_type === 'linkedin_easy_apply';
  const sent = applicationIsSent(draft);
  const uncertain = applicationIsUncertain(draft);
  const warnings = sent || uncertain ? [] : (draft.warnings || []).filter((warning) =>
    !linkedinManual || !warning.startsWith('No application destination is known.') && !warning.startsWith('No verified application method was found.'));
  const blockers = sent || uncertain ? [] : (draft.send_blockers || []).map((blocker) =>
    linkedinManual && blocker === 'Choose an email or web application destination'
      ? linkedinEasyApply ? 'Complete this Easy Apply application on LinkedIn.' : 'No verified way to apply was found. Check the LinkedIn posting’s Apply button.'
      : blocker);
  const reviewTone = applicationReviewTone(draft);
  const canSend = !sent && !uncertain && draft.send_ready && draft.review_status === 'awaiting_review';
  const canInspect = !sent && !uncertain && (destination.kind === 'linkedin_easy_apply' ||
    (destination.kind === 'web' && !isLinkedInJobPostingUrl(destination.url)));
  const linkedinStep = Math.max(0, ...(formData.fields || []).map((field) => Number(field.step) || 0));
  const linkedinFormStatus = formData.complete
    ? `All ${formData.steps_total || linkedinStep} LinkedIn steps reviewed`
    : `Step ${linkedinStep || 1} of ${formData.steps_total || '?'} reached${formData.inspection_blockers?.length ? ' · answers needed' : ' · answers saved; more steps to inspect'}`;
  const actionTarget = applicationActionTarget(destination);
  const detail = $('#application-detail');

  const sightings = job.observations || [];
  const preferredSighting = sightings[0] || null;
  const originalPostingUrl = draft.job_posting_url || (preferredSighting?.url && /^https?:\/\//i.test(preferredSighting.url) ? preferredSighting.url : null);
  const links = sightings.length ? sightings.map((source) =>
    `<div class="job-sighting" data-sighting-id="${source.id}"><div><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.name)}</a> <span class="pill muted">${escapeHtml(source.kind)}</span> ${preferredSighting?.id === source.id ? '<span class="status-badge status-badge--success">Preferred source</span>' : ''}<small>${escapeHtml(source.merge_reason_label || '')} · first seen ${relativeWhen(source.first_seen_at)}</small></div></div>`
  ).join('') : '';

  const score = job.score_detail ? (typeof job.score_detail === 'string' ? JSON.parse(job.score_detail) : job.score_detail) : (draft.job_score_detail || null);
  const matchCompleted = (job.analysis_status || draft.job_analysis_status) === 'done';

  const activitiesList = draft.activities || [];
  const activitiesHtml = (recentChanges.length ? `
    <div class="activity-item">
      <span class="activity-item-badge activity-item-badge--warning">Regenerated</span>
      <div class="activity-item-content">
        <strong>Revised section: ${escapeHtml(recentChanges.map(c => c.section).join(', '))}</strong>
        <small>Only this section was rewritten. Other sections preserved their reviewed text.</small>
      </div>
    </div>` : '') + (activitiesList.length ? activitiesList.map((item) => `
    <div class="activity-item">
      <span class="activity-item-badge activity-item-badge--${item.tone || 'info'}">${escapeHtml(item.label)}</span>
      <div class="activity-item-content">
        <strong>${escapeHtml(item.detail || item.label)}</strong>
        <small>${relativeWhen(item.created_at)} · <span ${exactTimeTitle(item.created_at)}>${when(item.created_at)}</span></small>
      </div>
    </div>`).join('') : `<p class="hint">Application draft created ${relativeWhen(draft.created_at)}.</p>`);

  detail.innerHTML = `
    <div class="application-review-header">
      <div>
        <p class="eyebrow" id="application-screen-eyebrow">APPLICATION · ${currentScreen === 'package' ? 'PACKAGE' : 'FIRST GLANCE'}</p>
        <h2>${escapeHtml(draft.job_title)}</h2>
        <p class="item-meta">${escapeHtml(draft.company)}${job.location ? ` · ${escapeHtml(job.location)}` : (draft.job_location ? ` · ${escapeHtml(draft.job_location)}` : '')} · ${escapeHtml(providerLabel(draft.provider_mode))}${job.work_mode ? ` · ${escapeHtml(job.work_mode)}` : (draft.job_work_mode ? ` · ${escapeHtml(draft.job_work_mode)}` : '')}</p>
      </div>
      <div class="application-header-actions" style="display:flex;align-items:center;gap:0.75rem;">
        <span class="status-badge status-badge--${reviewTone}">${escapeHtml(applicationReviewLabel(draft))}</span>
      </div>
    </div>

    <nav class="application-screen-nav application-review-nav" aria-label="Application review screens">
      <button type="button" data-app-screen="first-glance" aria-current="${currentScreen === 'first-glance'}">
        <span class="screen-step-num">1</span>
        <div class="screen-step-text"><strong>First glance</strong><small>Job match &amp; history</small></div>
      </button>
      <button type="button" data-app-screen="package" aria-current="${currentScreen === 'package'}">
        <span class="screen-step-num">2</span>
        <div class="screen-step-text"><strong>Package</strong><small>Resume &amp; submission</small></div>
      </button>
    </nav>

    <div id="screen-first-glance" class="application-screen-view" ${currentScreen === 'first-glance' ? '' : 'hidden'}>
      <section id="application-review-overview" class="application-activity-section surface-status">
        <div class="section-head">
          <div>
            <h3>${sent ? 'Application sent' : uncertain ? 'Submission status uncertain' : 'My last activities with this application'}</h3>
            <p class="hint">${sent ? 'Submission record and delivery confirmation' : uncertain ? 'Submission outcome requires confirmation on employer site' : 'Status, attention items, and recent application activity'}</p>
          </div>
          <div class="activity-header-badges">
            <span class="pill muted">${escapeHtml(destination.kind === 'email' ? 'Email package' : ['web','linkedin_easy_apply'].includes(destination.kind) ? 'Form package' : 'Manual handoff')}</span>
          </div>
        </div>

        <div class="activity-destination-note">
          <small>Destination: <strong>${escapeHtml(applicationActionLabel(destination))}</strong> · ${escapeHtml(actionTarget)}</small>
        </div>

        ${(sent || uncertain) ? renderSubmissionProof(draft.latest_submission, true) : ''}
        ${applicationAlert('danger', 'Sending is blocked', blockers)}
        ${applicationAlert('warning', 'Review before sending', warnings)}

        <div class="activity-timeline">
          ${activitiesHtml}
        </div>
      </section>

      <div class="overview-metrics-row">
        <div class="metric-card surface-status">
          <div class="metric-card-head">
            <span class="eyebrow">MATCH SCORE</span>
            <span class="status-badge status-badge--${matchCompleted ? fitClassTone(job.fit_class) : 'neutral'}">${escapeHtml(matchCompleted ? fitClassLabel(job.fit_class) : 'Review in progress')}</span>
          </div>
          <div class="metric-card-primary">
            <strong class="metric-score-value">${matchCompleted && job.score != null ? `${job.score}/100` : (draft.job_score != null ? `${draft.job_score}/100` : 'Score pending')}</strong>
          </div>
          <div class="metric-source-meta">
            ${matchCompleted ? `<p class="metric-signal"><strong>Evidence confidence:</strong> ${Math.max(0, 100 - Number(job.uncertainty || 0))}%</p>` : '<p class="hint">Review in progress</p>'}
            ${matchCompleted && job.missing_evidence?.length ? `<p class="metric-subtext">Missing evidence: ${escapeHtml(job.missing_evidence.join(', '))}</p>` : ''}
          </div>
        </div>

        <div class="metric-card surface-status">
          <div class="metric-card-head">
            <span class="eyebrow">JOB ORIGINALITY</span>
            ${sightings.length > 1 ? `<span class="pill muted">Seen on ${sightings.length - 1} other source${sightings.length - 1 === 1 ? '' : 's'}</span>` : '<span class="pill muted">Single source</span>'}
          </div>
          <div class="metric-card-primary">
            ${originalPostingUrl
              ? `<a class="metric-source-link button-link" href="${escapeHtml(originalPostingUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(preferredSighting?.name || 'Original posting')} ↗</a>`
              : `<strong class="metric-source-name">${escapeHtml(preferredSighting?.name || draft.job_source_kind || 'Original source')}</strong>`
            }
          </div>
          <div class="metric-source-meta">
            <p class="metric-signal"><strong>Source type:</strong> ${escapeHtml(preferredSighting?.kind || draft.job_source_kind || 'web')}</p>
            <p class="metric-subtext">First seen ${relativeWhen(preferredSighting?.first_seen_at || job.first_seen_at || draft.created_at)}</p>
            <button type="button" class="text-button" id="btn-view-job-in-jobs" style="margin-top:0.35rem;padding:0;">View job in Jobs</button>
          </div>
        </div>
      </div>

      ${renderJobAnalysis(job, score)}

      <details class="detail-disclosure">
        <summary>Original job description</summary>
        <div class="description">${formatDescription(job.description || draft.job_description)}</div>
      </details>
    </div>

    <div id="screen-package" class="application-screen-view" ${currentScreen === 'package' ? '' : 'hidden'}>
      <section id="application-review-resume" class="application-review-section">
        <div class="section-head">
          <div>
            <h3>Resume</h3>
            <p class="hint">Review the PDF before approving this application.</p>
          </div>
          <a href="/api/applications/${id}/resume" target="_blank" rel="noopener noreferrer">Open PDF ↗</a>
        </div>
        <div class="application-resume-preview">
          <button type="button" class="secondary" id="load-resume-preview" disabled>Loading preview…</button>
          <div class="application-preview-pages" hidden></div>
        </div>
        <details class="application-cv-details">
          <summary>Edit resume details</summary>
          <div class="application-cv-fields">
            <p class="hint">Bullet fields show the LaTeX item lines used in the PDF. To make words bold, wrap them in <code>&#92;textbf{...}</code>, then save. This edits the resume directly without using a model.</p>
            <div class="form-grid">
              <label>Name<input id="draft-name" data-draft-field value="${escapeHtml(resume.name || '')}"></label>
              <label>Email<input id="draft-email" data-draft-field value="${escapeHtml(resume.email || '')}"></label>
              <label>Phone<input id="draft-phone" data-draft-field value="${escapeHtml(resume.phone || '')}"></label>
              <label>Links, one per line<textarea id="draft-links" data-draft-field rows="2">${escapeHtml((resume.links || []).join('\n'))}</textarea></label>
            </div>
            <label>Professional summary<textarea id="draft-summary" data-draft-field rows="3">${escapeHtml(resume.summary || '')}</textarea></label>
            <h4>Experience</h4>${(resume.experience || []).map((item, index) => `<div class="review-subsection surface-editable"><div class="form-grid"><label>Company<input data-experience-company="${index}" data-draft-field value="${escapeHtml(item.company || '')}"></label><label>Role<input data-experience-role="${index}" data-draft-field value="${escapeHtml(item.role || '')}"></label><label>Dates<input data-experience-dates="${index}" data-draft-field value="${escapeHtml(item.dates || '')}"></label></div><label>Experience bullets · LaTeX item lines<textarea class="resume-item-editor" data-experience-bullets="${index}" data-draft-field rows="4">${escapeHtml((bulletSource.experience?.[index] || []).join('\n'))}</textarea></label></div>`).join('') || '<p class="hint">No previous positions in this draft.</p>'}
            <h4>Selected projects</h4>${projects.map((project, index) => `<div class="review-subsection surface-editable"><div class="form-grid"><label>Title<input data-project-title="${index}" data-draft-field value="${escapeHtml(project.title || '')}"></label><label>Repository URL<input data-project-url="${index}" data-draft-field value="${escapeHtml(project.repository_url || '')}"></label><label>Technologies<input data-project-stack="${index}" data-draft-field value="${escapeHtml((project.tech_stack || []).join(', '))}"></label></div><label>Project bullets · LaTeX item lines<textarea class="resume-item-editor" data-project-bullets="${index}" data-draft-field rows="4">${escapeHtml((bulletSource.projects?.[index] || []).join('\n'))}</textarea></label></div>`).join('') || '<p class="hint">No projects selected for this draft.</p>'}
            <h4>Education</h4>${(resume.education || []).map((item, index) => { const entry = typeof item === 'string' ? {school:item} : item; return `<div class="form-grid review-subsection surface-editable"><label>School<input data-education-school="${index}" data-draft-field value="${escapeHtml(entry.school || '')}"></label><label>Degree<input data-education-degree="${index}" data-draft-field value="${escapeHtml(entry.degree || '')}"></label><label>Dates<input data-education-dates="${index}" data-draft-field value="${escapeHtml(entry.dates || '')}"></label></div>`; }).join('') || '<p class="hint">No education in this draft.</p>'}
            <label>Achievement bullets · LaTeX item lines<textarea class="resume-item-editor" id="draft-achievements" data-draft-field rows="3">${escapeHtml((bulletSource.achievements || []).join('\n'))}</textarea></label>
            <label>Skills, one per line<textarea id="draft-skills" data-draft-field rows="3">${escapeHtml((resume.skills || []).join('\n'))}</textarea></label>
            <label>Skill groups, one per line as “Group: skills”<textarea id="draft-skill-groups" data-draft-field rows="3">${escapeHtml(Object.entries(resume.skill_groups || {}).map(([group, values]) => `${group}: ${Array.isArray(values) ? values.join(', ') : values}`).join('\n'))}</textarea></label>
          </div>
        </details>
      </section>

      ${destination.kind === 'email' ? `
      <section id="application-review-message" class="application-review-section">
        <div class="section-head">
          <div>
            <h3>Application message (Email)</h3>
            <p class="hint">Email subject and message body to accompany your resume to ${escapeHtml(destination.email || 'the employer')}.</p>
          </div>
        </div>
        <label>Subject<input id="draft-subject" data-draft-field value="${escapeHtml(message.subject || '')}"></label>
        <label>Body<textarea id="draft-body" data-draft-field rows="10">${escapeHtml(message.body || '')}</textarea></label>
      </section>` : ''}

      <section id="application-review-form" class="application-review-section" ${['web','linkedin_easy_apply'].includes(destination.kind) ? '' : 'hidden'}>
        <div class="section-head">
          <div>
            <h3>Form answers and attachments</h3>
            <p class="hint">${formData.action ? `Form submits to ${escapeHtml(formData.action)} (${escapeHtml(formData.method || 'GET')})` : formData.opener ? `Opened through ${escapeHtml(formData.opener.label || 'the career page button')}` : destination.kind === 'linkedin_easy_apply' ? escapeHtml(linkedinFormStatus) : 'Inspect the application page to load its fields here.'}</p>
          </div>
        </div>
        ${!sent && (linkedinManual || destination.kind === 'linkedin_easy_apply') ? `
        <div class="application-method-guidance surface-status">
          <strong>${destination.kind === 'linkedin_easy_apply' ? 'LinkedIn Easy Apply' : 'Check how to apply on LinkedIn'}</strong>
          <p>${destination.kind === 'linkedin_easy_apply' ? 'Review every form answer below. Inspect form reads the current LinkedIn steps without submitting.' : linkedinPaused ? 'Scheduled LinkedIn checks are paused after an account activity warning.' : 'Check what the posting’s application button opens, then review the discovered form.'}</p>
          ${draft.job_posting_url && /^https?:\/\//i.test(draft.job_posting_url) ? `<a class="button-link" href="${escapeHtml(draft.job_posting_url)}" target="_blank" rel="noopener noreferrer">Open LinkedIn posting ↗</a>` : ''}
          ${linkedinManual ? '<button type="button" class="secondary" id="discover-linkedin-apply">Check application button</button>' : ''}
        </div>` : ''}
        ${canInspect ? `
        <div class="application-form-inspection-banner surface-status">
          <p class="hint">Job Radar uses a browser to discover the questions on this form and prefill answers from your profile.</p>
          <button id="inspect-form-inline" type="button" class="secondary">${destination.kind === 'linkedin_easy_apply' && !formData.complete ? 'Inspect remaining steps' : 'Inspect form fields'}</button>
        </div>` : ''}
        ${formData.inspection_blockers?.length ? applicationAlert('warning', 'More answers needed to inspect every step', formData.inspection_blockers) : ''}
        <div class="application-form-fields">${(formData.fields || []).map((field) => renderApplicationFormField(field, draft)).join('') || '<p class="empty">No form fields inspected yet.</p>'}</div>
      </section>

      <div class="application-destination surface-editable">
        <div class="section-head">
          <div>
            <h4>Where to apply</h4>
            <p class="hint">Use an email address from the posting or the actual employer application form URL.</p>
          </div>
        </div>
        ${destination.provenance ? `<p class="hint">Detected from ${escapeHtml(destination.provenance.replace(/_/g, ' '))} · ${escapeHtml(destination.confidence || 'unknown confidence')}</p>` : ''}
        <div class="form-grid">
          <label>Channel<select id="draft-destination-kind" data-draft-field><option value="web" ${destination.kind === 'web' ? 'selected' : ''}>Web form</option><option value="email" ${destination.kind === 'email' ? 'selected' : ''}>Email</option><option value="linkedin_easy_apply" ${destination.kind === 'linkedin_easy_apply' ? 'selected' : ''}>LinkedIn Easy Apply</option><option value="manual" ${!['web','email','linkedin_easy_apply'].includes(destination.kind) ? 'selected' : ''}>Manual review</option></select></label>
          <label>Application form URL or application email<input id="draft-destination" data-draft-field value="${escapeHtml(destination.url || destination.email || '')}"></label>
        </div>
      </div>

      ${!sent && recentChanges.length ? `
      <div class="application-change-list">
        <strong>Changed by your last regeneration</strong>
        ${recentChanges.map((change) => `<div class="application-change-item"><span class="status-badge status-badge--warning">${escapeHtml(change.section)}</span><small>Only this section changed. Untouched sections kept their reviewed content.</small></div>`).join('')}
      </div>` : ''}

      <section id="application-review-regenerate" class="application-review-section">
        <h3>Regenerate only what needs work</h3>
        <p class="hint">${webGenerator ? 'ChatGPT Web enters the prompt in your saved Chrome browser and automatically receives its reply to revise this application.' : 'Choose one section to revise. Resume changes update the PDF. For the application email, AI rewrites only the experience and project fit paragraph.'}</p>
        <label>Section<select id="regenerate-section"><option value="summary">Professional summary</option><option value="experience">Experience bullets</option><option value="projects">Selected projects and bullets</option><option value="education">Education wording</option><option value="achievements">Achievements</option><option value="skills">Skills</option>${destination.kind === 'email' ? '<option value="message">Application experience and project fit</option>' : ''}<option value="all" ${draft.provider === 'chatgpt_web' ? 'selected' : ''}>${webGenerator ? 'Full resume draft' : 'Full draft · uses more quota'}</option></select></label>
        <label>Custom instructions<textarea id="regenerate-prompt" rows="3" placeholder="Example: make the summary shorter and emphasize production search work"></textarea></label>
        <div class="actions"><button id="regenerate-draft" class="secondary" ${webGenerator ? 'hidden' : ''} ${draft.provider === 'template' || sent || uncertain ? 'disabled' : ''}>Regenerate selected section</button><button id="chatgpt-input" class="secondary" ${webGenerator ? '' : 'hidden'} ${sent || uncertain ? 'disabled' : ''}>Regenerate selected section</button></div>
        ${sent ? '<p class="hint">This application has been sent. Its reviewed message and resume are preserved in the submission receipt.</p>' : ''}
        ${draft.provider === 'template' && !webGenerator ? '<p class="hint">This draft used the basic template. Create a new draft with an AI provider to regenerate it with instructions.</p>' : ''}
      </section>

      <details class="application-debug"><summary>Activity and technical details</summary><p class="hint">Telegram review delivery: ${escapeHtml(draft.telegram_status || 'Not configured')}${draft.telegram_error ? ` · ${escapeHtml(draft.telegram_error)}` : ''}</p><p class="hint">Package fingerprint: <span class="mono">${escapeHtml(draft.package_hash.slice(0, 16))}</span></p></details>
    </div>

    <div class="application-sticky-actions">
      <div class="application-sticky-left">
        <button type="button" class="secondary" id="btn-sticky-back-first-glance" ${currentScreen === 'package' ? '' : 'hidden'}>← Back to First glance</button>
        <span id="application-dirty-state" class="status-badge status-badge--neutral" ${currentScreen === 'package' ? '' : 'hidden'}>Saved</span>
        <span id="application-outcome" class="hint" role="status" aria-live="polite" ${currentScreen === 'package' ? '' : 'hidden'}></span>
      </div>
      <div class="actions">
        ${!sent ? `<button type="button" class="secondary danger" id="btn-sticky-discard-draft">Discard draft</button>` : ''}
        <button type="button" class="primary" id="btn-next-to-package" ${currentScreen === 'first-glance' ? '' : 'hidden'}>Next: Review package →</button>
        <button id="save-draft" class="secondary" disabled ${currentScreen === 'package' ? '' : 'hidden'}>Save changes</button>
        <button id="send-draft" class="primary" ${canSend ? '' : 'disabled'} ${currentScreen === 'package' ? '' : 'hidden'}>${sent ? escapeHtml(draft.latest_submission?.outcome?.label || 'Sent') : 'Approve &amp; send'}</button>
      </div>
    </div>`;

  const loadResumePreview = async () => {
    const preview = detail.querySelector('.application-preview-pages');
    const button = detail.querySelector('#load-resume-preview');
    if (!preview || !button) return;
    button.disabled = true;
    button.textContent = 'Loading preview…';
    try {
      const result = await api(`/api/applications/${id}/resume/preview/pages`);
      preview.innerHTML = Array.from({length:result.pages}, (_, index) =>
        `<figure><img src="/api/applications/${id}/resume/preview?page=${index + 1}&v=${encodeURIComponent(draft.resume_hash || draft.updated_at)}" alt="Resume page ${index + 1}" loading="${index ? 'lazy' : 'eager'}"><figcaption>Page ${index + 1} of ${result.pages}</figcaption></figure>`).join('');
      preview.hidden = false;
      button.remove();
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Try PDF preview again';
      notice(error.message, true);
    }
  };
  detail.querySelector('#load-resume-preview')?.addEventListener('click', loadResumePreview);
  loadResumePreview();

  detail.querySelector('#discover-linkedin-apply')?.addEventListener('click', async (event) => {
    const button = beginPending(event.currentTarget, 'Checking LinkedIn…');
    try {
      const result = await api(`/api/applications/${id}/discover-apply`, {method:'POST'});
      if (result.action.kind === 'web') {
        await loadApplications(id, 'package');
        notice(result.inspection_error
          ? `External Apply page found. Its form needs manual review: ${result.inspection_error}`
          : 'External Apply form found and inspected. Review its fields and answers before approving.');
      } else if (result.action.kind === 'linkedin_easy_apply') {
        await loadApplications(id, 'package');
        notice(result.inspection_error ? `LinkedIn Easy Apply found, but inspection needs attention: ${result.inspection_error}` : 'LinkedIn Easy Apply fields are ready for review. Complete missing answers, save, then inspect again.');
      } else {
        await loadApplications(id, 'package');
        notice(result.action.detail || 'No external application form was found.', true);
      }
    } catch(error) { notice(error.message, true); }
    finally { if (button.isConnected) endPending(button); }
  });

  const dirtyState = $('#application-dirty-state');
  const saveButton = $('#save-draft');
  const sendButton = $('#send-draft');
  const dirtySections = new Set();
  const markDirty = (field) => {
    if (sent || uncertain) return;
    const section = ['draft-subject','draft-body'].includes(field.id) ? 'message_data'
      : ['draft-destination-kind','draft-destination'].includes(field.id) ? 'destination'
      : ['answer','attachment','attachmentFile'].some((key) => Object.hasOwn(field.dataset, key)) ? 'form_data'
      : 'resume_data';
    dirtySections.add(section);
    dirtyState.textContent = 'Unsaved changes';
    dirtyState.className = 'status-badge status-badge--warning';
    saveButton.disabled = false;
    sendButton.disabled = true;
    $('#application-outcome').textContent = 'Save your changes before approving this application.';
    field.closest('label')?.classList.add('is-dirty');
    if (['draft-destination-kind','draft-destination'].includes(field.id)) {
      const inspectInline = detail.querySelector('#inspect-form-inline');
      if (inspectInline) {
        inspectInline.disabled = !['web','linkedin_easy_apply'].includes($('#draft-destination-kind').value) ||
          ($('#draft-destination-kind').value === 'web' && isLinkedInJobPostingUrl($('#draft-destination').value.trim()));
      }
    }
  };

  detail.querySelectorAll('[data-attachment]').forEach((select) => {
    const upload = detail.querySelector(`[data-attachment-upload="${select.dataset.attachment}"]`);
    const syncUpload = () => { if (upload) upload.hidden = select.value !== 'uploaded'; };
    syncUpload();
    select.addEventListener('change', syncUpload);
  });

  detail.querySelectorAll('[data-draft-field]').forEach((field) => {
    for (const eventName of ['input','change']) field.addEventListener(eventName, () => markDirty(field));
  });

  $('#regenerate-draft')?.addEventListener('click', async () => {
    const prompt = $('#regenerate-prompt').value.trim();
    if (!prompt) { notice('Enter custom instructions to regenerate the draft.', true); return; }
    const button = $('#regenerate-draft');
    beginPending(button, 'Regenerating…');
    showApplicationRegenerating(id);
    try {
      const section = $('#regenerate-section').value;
      const result = await api(`/api/applications/${id}/regenerate`, {method:'POST', body:JSON.stringify({prompt, section})});
      applicationReviewChanges[id] = result.changes || [];
      await loadApplications(id, 'package');
      notice(section === 'all' ? 'New draft prepared for review.' : 'Selected section regenerated. Untouched content was preserved.');
    } catch(error) {
      try { await loadApplications(id, 'package'); } catch (refreshError) { console.warn('Could not refresh application status', refreshError); }
      notice(error.message, true);
    } finally {
      if (button.isConnected) endPending(button);
    }
  });

  $('#chatgpt-input')?.addEventListener('click', async (event) => {
    if (dirtySections.size) { notice('Save your application changes before sending its context to ChatGPT.', true); return; }
    const button = beginPending(event.currentTarget, 'Regenerating…');
    showApplicationRegenerating(id);
    try {
      const section = $('#regenerate-section').value;
      const result = await api(`/api/applications/${id}/chatgpt-input`, {
        method:'POST',
        body:JSON.stringify({section, instruction:$('#regenerate-prompt').value.trim()}),
      });
      await loadApplications(id, 'package');
      notice(result.detail || (section === 'all' ? 'New draft prepared for review.' : 'Selected section regenerated. Untouched content was preserved.'),
        result.status !== 'entered' && result.status !== 'received');
    } catch(error) {
      try { await loadApplications(id, 'package'); } catch (refreshError) { console.warn('Could not refresh application status', refreshError); }
      notice(error.message, true);
    }
    finally { if (button.isConnected) endPending(button); }
  });

  $('#save-draft').addEventListener('click', async (event) => {
    const button = beginPending(event.currentTarget, 'Saving…');
    let savedInline = false;
    try {
      const messageOnly = dirtySections.size === 1 && dirtySections.has('message_data');
      const saved = await saveApplication(id, draft, dirtySections);
      if (messageOnly) {
        Object.assign(draft, saved);
        dirtySections.clear();
        dirtyState.textContent = 'Saved';
        dirtyState.className = 'status-badge status-badge--neutral';
        sendButton.disabled = !(saved.send_ready && saved.review_status === 'awaiting_review');
        $('#application-outcome').textContent = '';
        detail.querySelectorAll('.application-review-header .status-badge').forEach((badge) => {
          badge.textContent = applicationReviewLabel(saved);
          badge.className = `status-badge status-badge--${applicationReviewTone(saved)}`;
        });
        detail.querySelectorAll('.is-dirty').forEach((field) => field.classList.remove('is-dirty'));
        savedInline = true;
      } else {
        await loadApplications(id, 'package');
      }
      notice(saved.form_data?.kind === 'linkedin_easy_apply' && !saved.form_data.complete
        ? saved.form_data.inspection_blockers?.length
          ? 'Answers saved. Complete the remaining required fields, then inspect again.'
          : 'Answers saved. Inspect remaining LinkedIn steps to continue.'
        : 'Application saved');
    } catch(error) {
      notice(error.message, true);
    } finally {
      if (button.isConnected) {
        endPending(button);
        if (savedInline) button.disabled = true;
      }
    }
  });

  const inspectFormButton = detail.querySelector('#inspect-form-inline');
  if (inspectFormButton) {
    inspectFormButton.addEventListener('click', async (event) => {
      const button = beginPending(event.currentTarget, 'Inspecting…');
      try {
        if (!saveButton.disabled) await saveApplication(id, draft, dirtySections);
        await api(`/api/applications/${id}/inspect`, {method:'POST'});
        await loadApplications(id, 'package');
        notice('Application form inspected.');
      } catch(error) {
        notice(error.message, true);
      } finally {
        if (button.isConnected) endPending(button);
      }
    });
  }

  $('#send-draft').addEventListener('click', async (event) => {
    const button = beginPending(event.currentTarget, 'Sending…');
    try {
      const result = await api(`/api/applications/${id}/approve`, {method:'POST', body:JSON.stringify({package_hash:draft.package_hash})});
      const outcome = result.outcome || {};
      await loadApplications(id, 'first-glance');
      const message = `${outcome.label || 'Application updated'}${result.receipt || result.error ? `: ${result.receipt || result.error}` : ''}`;
      $('#application-outcome').textContent = message;
      notice(message, outcome.key === 'submission_uncertain');
    } catch(error) {
      notice(error.message, true);
    } finally {
      if (button.isConnected) endPending(button);
    }
  });

  const switchScreen = (screen) => {
    activeApplicationScreen = screen;
    detail.querySelectorAll('[data-app-screen]').forEach((btn) => {
      btn.setAttribute('aria-current', btn.dataset.appScreen === screen ? 'true' : 'false');
    });
    const eyebrow = detail.querySelector('#application-screen-eyebrow');
    if (eyebrow) eyebrow.textContent = screen === 'package' ? 'APPLICATION · PACKAGE' : 'APPLICATION · FIRST GLANCE';
    const firstGlanceEl = detail.querySelector('#screen-first-glance');
    const packageEl = detail.querySelector('#screen-package');
    if (firstGlanceEl && packageEl) {
      firstGlanceEl.hidden = screen !== 'first-glance';
      packageEl.hidden = screen !== 'package';
    }
    const backBtn = detail.querySelector('#btn-sticky-back-first-glance');
    const dirtyBadge = detail.querySelector('#application-dirty-state');
    const outcomeEl = detail.querySelector('#application-outcome');
    const nextBtn = detail.querySelector('#btn-next-to-package');
    const saveBtn = detail.querySelector('#save-draft');
    const sendBtn = detail.querySelector('#send-draft');
    if (backBtn) backBtn.hidden = screen !== 'package';
    if (dirtyBadge) dirtyBadge.hidden = screen !== 'package';
    if (outcomeEl) outcomeEl.hidden = screen !== 'package' && !(sent || uncertain);
    if (nextBtn) nextBtn.hidden = screen !== 'first-glance';
    if (saveBtn) saveBtn.hidden = screen !== 'package';
    if (sendBtn) sendBtn.hidden = screen !== 'package' && !(sent || uncertain);
    scrollNodeIntoView(detail, {block:'start'});
  };

  detail.querySelectorAll('[data-app-screen]').forEach((btn) => {
    btn.addEventListener('click', () => switchScreen(btn.dataset.appScreen));
  });
  detail.querySelector('#btn-next-to-package')?.addEventListener('click', () => switchScreen('package'));
  detail.querySelector('#btn-sticky-back-first-glance')?.addEventListener('click', () => switchScreen('first-glance'));
  detail.querySelector('#btn-view-job-in-jobs')?.addEventListener('click', () => {
    openApplicationJob(draft.vacancy_id).catch((error) => notice(error.message, true));
  });

  const handleDiscard = async () => {
    if (sent) return;
    const dialog = $('#application-discard-confirm');
    const ignoreCheckbox = $('#application-discard-ignore-job');
    if (ignoreCheckbox) ignoreCheckbox.checked = true;

    let confirmed = false;
    if (dialog && typeof dialog.showModal === 'function') {
      confirmed = await new Promise((resolve) => {
        const onClose = () => {
          dialog.removeEventListener('close', onClose);
          resolve(dialog.returnValue === 'confirm');
        };
        dialog.addEventListener('close', onClose);
        dialog.showModal();
      });
    } else {
      confirmed = window.confirm('Discard this application draft?');
    }

    if (!confirmed) return;

    try {
      await api(`/api/applications/${id}?ignore_job=true`, {method:'DELETE'});
      notice('Application draft discarded and job marked as ignored.', false);
      activeApplicationId = null;
      detail.innerHTML = '<div class="panel empty"><p>Draft discarded.</p></div>';
      await loadApplications();
    } catch (error) {
      notice(error.message, true);
    }
  };

  detail.querySelector('#btn-discard-draft')?.addEventListener('click', handleDiscard);
  detail.querySelector('#btn-sticky-discard-draft')?.addEventListener('click', handleDiscard);

  if (sent || uncertain) {
    detail.querySelectorAll('[data-draft-field],#regenerate-draft,#chatgpt-input,#save-draft,#send-draft,#inspect-form-inline').forEach((control) => { control.disabled = true; });
    dirtyState.textContent = uncertain ? 'Submission status uncertain' : 'Sent';
    dirtyState.className = `status-badge status-badge--${uncertain ? 'warning' : 'success'}`;
    if (uncertain) $('#application-outcome').textContent = draft.latest_submission?.outcome?.guidance ||
      'Verify on the employer site before taking another send action.';
  }

  switchScreen(currentScreen);
  detail.focus({preventScroll:true});
}

async function saveApplication(id, draft, dirtySections) {
  if (!dirtySections.size) return draft;
  const lines = (value) => value.split('\n').map((x) => x.trim()).filter(Boolean);
  const payload = {};
  if (dirtySections.has('message_data')) {
    payload.message_data = {subject:$('#draft-subject').value, body:$('#draft-body').value};
  }
  if (dirtySections.has('form_data')) {
    const answers = {};
    document.querySelectorAll('[data-answer]').forEach((field) => {
      answers[field.dataset.answer] = ['radio','checkbox'].includes(field.type) && !field.checked ? '' : field.value;
    });
    const attachments = {};
    for (const select of document.querySelectorAll('[data-attachment]')) {
      const index = select.dataset.attachment;
      const file = document.querySelector(`[data-attachment-file="${index}"]`).files[0];
      if (file && select.value === 'uploaded') {
        const body = new FormData();
        body.append('file', file);
        attachments[index] = await api(`/api/applications/${id}/attachments`, {method:'POST', body});
      } else if (select.value === 'uploaded') {
        attachments[index] = draft.form_data.attachments?.[index];
      } else if (select.value) {
        attachments[index] = {kind:select.value};
      }
    }
    payload.form_data = {...draft.form_data, answers, attachments};
  }
  if (dirtySections.has('destination')) {
    const kind = $('#draft-destination-kind').value;
    const value = $('#draft-destination').value.trim();
    const editedDestination = kind === 'email' ? {kind, email:value} : {kind, url:value};
    const originalValue = draft.destination.url || draft.destination.email || '';
    const sameDestination = kind === draft.destination.kind && value === originalValue;
    payload.destination = sameDestination
      ? {...draft.destination, ...editedDestination}
      : {...editedDestination, action_type:kind === 'email' ? 'email' : kind === 'web' ? 'web_form' : kind === 'linkedin_easy_apply' ? 'linkedin_easy_apply' : 'manual',
         provenance:'manual_override', confidence:'user_confirmed', evidence:'Destination edited during application review'};
  }
  if (dirtySections.has('resume_data')) {
    const bulletSource = draft.resume_bullet_source || {experience:[], projects:[], achievements:[]};
    const editedBullets = (value, source, originals) => {
      const items = lines(value);
      if (items.join('\n') === (source || []).join('\n')) return originals;
      if (items.some((item) => !item.startsWith('\\item '))) {
        throw new Error('Each LaTeX bullet line must start with \\item followed by its text.');
      }
      return items;
    };
    const experience = (draft.resume_data.experience || []).map((item, index) => ({...item,
      company:document.querySelector(`[data-experience-company="${index}"]`).value,
      role:document.querySelector(`[data-experience-role="${index}"]`).value,
      dates:document.querySelector(`[data-experience-dates="${index}"]`).value,
      bullets:editedBullets(document.querySelector(`[data-experience-bullets="${index}"]`).value,
        bulletSource.experience?.[index], item.bullets || [])}));
    const projects = (draft.resume_data.projects || []).map((project, index) => ({...project,
      title:document.querySelector(`[data-project-title="${index}"]`).value,
      repository_url:document.querySelector(`[data-project-url="${index}"]`).value,
      tech_stack:document.querySelector(`[data-project-stack="${index}"]`).value.split(',').map((x) => x.trim()).filter(Boolean),
      bullets:editedBullets(document.querySelector(`[data-project-bullets="${index}"]`).value,
        bulletSource.projects?.[index], project.bullets || [])}));
    const education = (draft.resume_data.education || []).map((item, index) => ({...(typeof item === 'string' ? {} : item),
      school:document.querySelector(`[data-education-school="${index}"]`).value,
      degree:document.querySelector(`[data-education-degree="${index}"]`).value,
      dates:document.querySelector(`[data-education-dates="${index}"]`).value}));
    const skill_groups = Object.fromEntries(lines($('#draft-skill-groups').value).map((line) => {
      const colon = line.indexOf(':'); return colon < 0 ? [line, ''] : [line.slice(0, colon).trim(), line.slice(colon + 1).trim()];
    }));
    payload.resume_data = {...draft.resume_data, name:$('#draft-name').value, email:$('#draft-email').value,
      phone:$('#draft-phone').value, links:lines($('#draft-links').value), summary:$('#draft-summary').value,
      experience, projects, education, achievements:editedBullets($('#draft-achievements').value,
        bulletSource.achievements, draft.resume_data.achievements || []),
      skills:lines($('#draft-skills').value), skill_groups};
  }
  return api(`/api/applications/${id}`, {method:'PATCH', body:JSON.stringify(payload)});
}
function bindRepositoryButtons(container) {
  container.querySelectorAll('[data-add-repo]').forEach((button) => button.addEventListener('click', async () => {
    const saved = projectCards.find((card) => card.repository_url && normalizeRepoUrl(card.repository_url) === normalizeRepoUrl(button.dataset.addRepo));
    if (saved) {
      selectedProjectId = saved.id;
      await loadEvidence(saved.id);
      scrollNodeIntoView($('#project-editor'), {block:'start'});
      return;
    }
    await inspectSelectedRepository(button.dataset.addRepo, button);
  }));
}

function renderRepositoryResults(filter = null) {
  const target = $('#github-repos');
  if (!discoveredRepos.length) return;
  if (!target.querySelector('#repo-filter')) {
    target.innerHTML = '<div class="project-results-head"><strong>Repositories</strong><span id="repo-result-count" class="hint"></span></div><input id="repo-filter" type="search" aria-label="Filter repositories" placeholder="Filter by name or language"><div class="project-results-list stack"></div>';
    const input = $('#repo-filter');
    input.addEventListener('compositionstart', () => { input.dataset.composing = 'true'; });
    input.addEventListener('compositionend', () => { delete input.dataset.composing; renderRepositoryResults(input.value); });
    input.addEventListener('input', () => { if (input.dataset.composing !== 'true') renderRepositoryResults(input.value); });
  }
  const input = $('#repo-filter');
  if (filter !== null && document.activeElement !== input) input.value = filter;
  const query = (filter === null ? input.value : filter).trim().toLowerCase();
  const visible = discoveredRepos.filter((repo) => `${repo.name} ${repo.description || ''} ${repo.language || ''}`.toLowerCase().includes(query));
  $('#repo-result-count').textContent = `${visible.length} of ${discoveredRepos.length}`;
  const list = target.querySelector('.project-results-list');
  list.innerHTML = visible.length ? visible.map((repo) => {
    const saved = projectCards.find((card) => card.repository_url && normalizeRepoUrl(card.repository_url) === normalizeRepoUrl(repo.url));
    return `<div class="project-repo-row"><div><strong>${escapeHtml(repo.name)}</strong>${repo.fork ? ' <span class="pill muted">Fork</span>' : ''}
      <p class="hint">${escapeHtml(repo.description || 'No description')}${repo.language ? ` · ${escapeHtml(repo.language)}` : ''}</p></div>
      <div class="actions"><button data-add-repo="${escapeHtml(repo.url)}" ${saved ? '' : !projectProviderReady ? 'disabled' : ''}>${saved ? 'Review project' : 'Add project'}</button><a href="${escapeHtml(repo.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open repository ${escapeHtml(repo.name)}">Open ↗</a></div></div>`;
  }).join('') : '<div class="empty">No repositories match that filter.</div>';
  bindRepositoryButtons(list);
}

function normalizeRepoUrl(value) {
  return value.replace(/\.git\/?$/, '').replace(/\/$/, '');
}

async function loadEvidence(focusId = null) {
  const [allCards, profile, setup] = await Promise.all([api('/api/evidence'), api('/api/profile'), api('/api/setup')]);
  projectCards = allCards.filter((card) => card.kind === 'project');
  if (focusId) selectedProjectId = focusId;
  if (!projectCards.some((card) => card.id === selectedProjectId)) selectedProjectId = projectCards.find((card) => !card.approved)?.id || projectCards[0]?.id || null;
  projectProviderAvailability = providerAvailability(setup);
  projectProcessing = setup.provider_processing || {};
  const providerSelect = $('#project-processing-provider');
  configureProviderSelect(providerSelect, setup, providerSelect.value || profile.drafting_provider || 'template');
  if (!providerSelect.value) providerSelect.value = 'template';
  projectProviderReady = Boolean(providerSelect.value && projectProviderAvailability[providerSelect.value]);
  $('#project-provider-status').textContent = processingCopy(setup, providerSelect.value, 'The repository snapshot used to draft project evidence');
  providerSelect.onchange = () => {
    projectProviderReady = Boolean(providerSelect.value && projectProviderAvailability[providerSelect.value]);
    $('#project-provider-status').textContent = processingCopy(setup, providerSelect.value, 'The repository snapshot used to draft project evidence');
    renderRepositoryResults($('#repo-filter')?.value || '');
  };
  const readyCount = projectCards.filter((card) => card.approved).length;
  $('#selected-project-count').textContent = `${readyCount} ready for resumes`;
  $('#evidence-list').innerHTML = projectCards.length ? projectCards.map((card) => `<button class="project-list-row ${card.id === selectedProjectId ? 'is-selected' : ''}" data-open-project="${card.id}" type="button" aria-pressed="${card.id === selectedProjectId ? 'true' : 'false'}">
    <strong>${escapeHtml(card.title)}</strong><span class="status-badge ${card.approved ? 'status-badge--success' : 'status-badge--neutral'}">${card.approved ? 'Included in resumes' : 'Saved only'}</span>
    <small>${escapeHtml(card.repository_url || 'Manual project')}</small></button>`).join('') : '<div class="empty">No projects yet. Enter your GitHub username or a repository URL above.</div>';
  document.querySelectorAll('[data-open-project]').forEach((button) => button.addEventListener('click', async () => {
    selectedProjectId = button.dataset.openProject;
    await loadEvidence(selectedProjectId);
    scrollNodeIntoView($('#project-editor'), {block:'start'});
    $('#project-editor').focus({preventScroll:true});
  }));
  const card = projectCards.find((item) => item.id === selectedProjectId);
  if (!card) {
    $('#project-editor').innerHTML = '<div class="project-editor-empty">Your AI-drafted project will appear here for review.</div>';
    renderRepositoryResults($('#repo-filter')?.value || '');
    return;
  }
  const details = JSON.parse(card.details || '{}');
  const needsOriginalClaim = details.schema_version !== 2 && !details.generated_by && (details.contribution === 'unverified' || ['pending','failed'].includes(details.generation_status));
  const savedResults = details.results || (details.bullets || [card.claim]).filter(Boolean).map((outcome, index) => ({
    id:`legacy-${index + 1}`, area:'General', outcome, source:''
  }));
  const resultRow = (result) => `<div class="project-result-row" data-result-id="${escapeHtml(result.id)}">
    <div class="project-result-head"><strong>Result worth highlighting</strong><button type="button" class="text-button danger" data-remove-project-result>Remove</button></div>
    <label>Focus area<input data-result-area value="${escapeHtml(result.area || '')}" placeholder="e.g. LLM, RAG, computer vision"></label>
    <label>Outcome<textarea data-result-outcome rows="2" placeholder="What worked or was measured? Include the baseline and scope when relevant.">${escapeHtml(result.outcome || '')}</textarea></label>
    <label>Source in repository<input data-result-source value="${escapeHtml(result.source || '')}" placeholder="e.g. README.md: Results"></label>
  </div>`;
  $('#project-editor').innerHTML = `<div class="project-editor-head"><div><p class="eyebrow">REVIEW PROJECT</p><h3>${escapeHtml(card.title)}</h3></div><span class="status-badge ${card.approved ? 'status-badge--success' : 'status-badge--neutral'}">${card.approved ? 'Included in resumes' : 'Saved only'}</span></div>
    <p class="hint">Keep this brief factual and reusable. Job Radar chooses the relevant results for each application after you include the project in resumes.</p>
    ${details.generation_status === 'failed' ? `<p class="hint error-text">Project draft generation failed: ${escapeHtml(details.generation_error || 'Try generating again or write your own project bullet.')}</p>` : ''}
    ${needsOriginalClaim ? '<p class="hint">Describe the project and add a sourced result before including it in resumes.</p>' : ''}
    ${card.repository_url ? `<p class="item-meta"><a href="${escapeHtml(card.repository_url)}" target="_blank" rel="noopener noreferrer" aria-label="Open repository for ${escapeHtml(card.title)}">Open repository ↗</a> · Commit ${escapeHtml(card.commit_sha?.slice(0, 8))}</p>` : ''}
    <div class="project-fields"><label>Project title<input id="project-edit-title" value="${escapeHtml(card.title)}"></label>
      <label>What it does<textarea id="project-edit-what" rows="2" placeholder="The product or research system and its output">${escapeHtml(details.what || details.summary || '')}</textarea></label>
      <label>Why it exists<textarea id="project-edit-why" rows="2" placeholder="The problem it addresses">${escapeHtml(details.why || '')}</textarea></label>
      <label>How it works<textarea id="project-edit-how" rows="3" placeholder="The architecture, methods, and important trade-offs">${escapeHtml(details.how || '')}</textarea></label>
      <label>Technologies<input id="project-edit-stack" value="${escapeHtml((details.tech_stack || []).join(', '))}" placeholder="Python, PyTorch, ..."></label>
      <div class="project-results-head"><div><strong>Results</strong><p class="hint">Keep only the strongest job-relevant outcomes. Separate LLM, vision, and other work, and cite each source.</p></div><button id="project-add-result" type="button" class="secondary">Add result</button></div>
      <div id="project-result-list" class="stack">${savedResults.map(resultRow).join('')}</div></div>
    <p id="project-review-status" class="hint" role="status" aria-live="polite"></p>
    <div class="actions"><button id="project-save" class="primary">Save changes</button>
      <button id="project-approval" class="secondary">${card.approved ? 'Remove from resumes' : 'Include in resumes'}</button>
      ${card.repository_url && !card.approved ? '<button id="project-regenerate" class="secondary">Generate again</button>' : ''}
      <button id="project-delete" class="secondary danger">Delete project</button></div>`;
  $('#project-add-result').addEventListener('click', () => {
    if ($('#project-result-list').children.length >= 8) return notice('Keep at most eight distinct project results.', true);
    $('#project-result-list').insertAdjacentHTML('beforeend', resultRow({id:`r-${crypto.randomUUID()}`, area:'', outcome:'', source:''}));
    $('#project-result-list .project-result-row:last-child [data-result-area]')?.focus();
  });
  $('#project-result-list').addEventListener('click', (event) => {
    if (event.target.closest('[data-remove-project-result]')) event.target.closest('.project-result-row').remove();
  });
  const content = () => {
    const title = $('#project-edit-title').value.trim();
    if (title.length < 2) throw new Error('Add a project title before saving.');
    const results = [...$('#project-result-list').querySelectorAll('.project-result-row')].map((row) => ({
      id:row.dataset.resultId, area:row.querySelector('[data-result-area]').value.trim(),
      outcome:row.querySelector('[data-result-outcome]').value.trim(),
      source:row.querySelector('[data-result-source]').value.trim(),
    })).filter((item) => item.area || item.outcome || item.source);
    const what = $('#project-edit-what').value.trim();
    return {title, claim:results[0]?.outcome || what || card.claim, details:{...details, schema_version:2,
      what, why:$('#project-edit-why').value.trim(), how:$('#project-edit-how').value.trim(), results,
      tech_stack:$('#project-edit-stack').value.split(',').map((item) => item.trim()).filter(Boolean)}};
  };
  $('#project-save').addEventListener('click', async () => {
    const status = $('#project-review-status');
    try {
      status.textContent = 'Saving project…';
      await api(`/api/evidence/${card.id}`, {method:'PATCH', body:JSON.stringify(content())});
      await loadEvidence(card.id);
      $('#project-review-status').textContent = 'Project changes saved. Resume inclusion is unchanged.';
    } catch(error) { status.textContent = error.message; notice(error.message, true); }
  });
  $('#project-approval').addEventListener('click', async () => {
    const status = $('#project-review-status');
    try {
      status.textContent = card.approved ? 'Removing from resumes…' : 'Including in resumes…';
      await api(`/api/evidence/${card.id}`, {method:'PATCH', body:JSON.stringify({approved:!card.approved})});
      await loadEvidence(card.id);
      $('#project-review-status').textContent = card.approved ? 'Project removed from future resumes.' : 'Project included in future resumes.';
    } catch(error) { status.textContent = error.message; notice(error.message, true); }
  });
  $('#project-delete').addEventListener('click', async () => {
    if (!window.confirm(`Delete "${card.title}" from your project library? This cannot be undone.`)) return;
    try {
      await api(`/api/evidence/${card.id}`, {method:'DELETE'});
      selectedProjectId = null;
      await loadEvidence();
      notice('Project deleted');
    } catch(error) { notice(error.message, true); }
  });
  $('#project-regenerate')?.addEventListener('click', async (event) => {
    const button = event.target;
    try {
      button.disabled = true;
      $('#project-review-status').textContent = 'Generating a new draft from the repository…';
      const provider = $('#project-processing-provider').value;
      if (!provider || !projectProviderReady) throw new Error('Choose an available processing option above first.');
      await api(`/api/evidence/${card.id}/generate`, {method:'POST', body:JSON.stringify({provider})});
      await loadEvidence(card.id);
      $('#project-review-status').textContent = 'New draft ready. Review and save it before including it in resumes.';
    } catch(error) { await loadEvidence(card.id); $('#project-review-status').textContent = error.message; }
  });
  renderRepositoryResults($('#repo-filter')?.value || '');
}

document.querySelectorAll('[data-tab]').forEach((control) => control.addEventListener('click', (event) => {
  if (control.matches('a[href^="#"]')) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
  }
  control.dataset.socialAuth === 'true' ? openSocialSignIn() : control.dataset.setupPanel ? openSetupPanel(control.dataset.setupPanel) : showTab(control.dataset.tab);
}));
$('#social-auth-action').addEventListener('click', openSocialSignIn);
setInterval(() => refreshSocialAuth().catch(() => {}), 60000);
function applyJobControls() {
  jobsPage = 1; activeJob = null; activeJobPinned = false; syncJobsHash('push');
  loadJobs().catch((error) => notice(error.message, true));
}
$('#job-search').addEventListener('click', applyJobControls);
$('#job-query').addEventListener('keydown', (event) => { if (event.key === 'Enter') applyJobControls(); });
for (const selector of ['#job-decision','#job-application','#job-outcome','#job-score','#job-freshness','#job-work-mode','#job-source','#job-seniority','#job-fit','#job-sort']) {
  $(selector).addEventListener('change', applyJobControls);
}
for (const selector of ['#job-location','#job-score']) {
  $(selector).addEventListener('keydown', (event) => { if (event.key === 'Enter') applyJobControls(); });
}
document.querySelectorAll('[data-job-inbox]').forEach((button) => button.addEventListener('click', () => {
  setJobsInboxMode(button.dataset.jobInbox);
  applyJobControls();
}));
$('#jobs-clear-filters').addEventListener('click', () => {
  for (const [key, selector] of Object.entries(JOB_FILTERS)) $(selector).value = key === 'sort' ? 'best' : key === 'fit' ? 'eligible' : '';
  applyJobControls();
});
$('#jobs-prev').addEventListener('click', () => { jobsPage = Math.max(1, jobsPage - 1); activeJob = null; activeJobPinned = false; syncJobsHash('push'); loadJobs().catch((error) => notice(error.message, true)); });
$('#jobs-next').addEventListener('click', () => { jobsPage += 1; activeJob = null; activeJobPinned = false; syncJobsHash('push'); loadJobs().catch((error) => notice(error.message, true)); });

$('#job-view-select').addEventListener('change', (event) => {
  const view = savedJobViews.find((item) => item.id === event.target.value);
  $('#job-delete-view').disabled = !view;
  if (!view) return;
  applyJobFilterSnapshot(view.filters || {});
  applyJobControls();
});
$('#job-save-view').addEventListener('click', () => {
  const dialog = $('#job-view-dialog');
  $('#job-view-form').reset();
  if (typeof dialog.showModal === 'function') dialog.showModal();
});
$('#job-view-cancel').addEventListener('click', () => $('#job-view-dialog').close('cancel'));
$('#job-view-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const data = new FormData(event.target);
  try {
    const view = await api('/api/jobs/views', {method:'POST', body:JSON.stringify({
      name:String(data.get('name') || '').trim(),
      filters:currentJobFilters(),
      set_default:data.get('set_default') === 'on',
    })});
    savedJobViews = await api('/api/jobs/views');
    renderSavedJobViews();
    $('#job-view-select').value = view.id;
    $('#job-delete-view').disabled = false;
    $('#job-view-dialog').close('saved');
    notice(`Saved job view “${view.name}”.`);
  } catch(error) { notice(error.message, true); }
});
$('#job-delete-view').addEventListener('click', async () => {
  const id = $('#job-view-select').value;
  if (!id) return;
  const view = savedJobViews.find((item) => item.id === id);
  try {
    await api(`/api/jobs/views/${id}`, {method:'DELETE'});
    savedJobViews = await api('/api/jobs/views');
    renderSavedJobViews();
    notice(`Deleted saved view “${view?.name || 'view'}”.`);
  } catch(error) { notice(error.message, true); }
});
document.querySelectorAll('[data-ignore-reason]').forEach((button) => button.addEventListener('click', async () => {
  if (!pendingIgnoreJobId) return;
  try {
    await api(`/api/jobs/${pendingIgnoreJobId}/decision`, {method:'POST', body:JSON.stringify({
      decision:'ignored',
      reason:button.dataset.ignoreReason,
    })});
    const reason = button.dataset.ignoreReason;
    pendingIgnoreJobId = null;
    $('#job-ignore-reason-dialog').close('reason');
    notice(`Ignore reason saved: ${reason}.`);
    await loadJobs();
  } catch(error) { notice(error.message, true); }
}));
$('#job-ignore-reason-dialog').addEventListener('close', () => { pendingIgnoreJobId = null; });
for (const selector of ['#source-kind', '#source-status', '#source-enabled', '#source-success', '#source-sort']) {
  $(selector).addEventListener('change', () => { sourcePage = 1; loadSources().catch((error) => notice(error.message, true)); });
}
$('#source-query').addEventListener('input', () => {
  sourcePage = 1;
  clearTimeout(window.sourceFilterTimer);
  window.sourceFilterTimer = setTimeout(() => loadSources().catch((error) => notice(error.message, true)), 150);
});
$('#sources-prev').addEventListener('click', () => { sourcePage = Math.max(1, sourcePage - 1); loadSources().catch((error) => notice(error.message, true)); });
$('#sources-next').addEventListener('click', () => { sourcePage += 1; loadSources().catch((error) => notice(error.message, true)); });
$('#queue-refresh').addEventListener('click', async (event) => {
  const button = beginPending(event.currentTarget, 'Refreshing…');
  try { await loadQueue({reason:'manual'}); }
  catch(error) { notice(error.message, true); }
  finally { endPending(button); }
});
$('#employer-search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if ($('#employer-query').value.trim()) document.querySelector('input[name="employer-scope"][value="all"]').checked = true;
  employerPage = 1;
  loadEmployers().catch((error) => notice(error.message, true));
});
document.querySelectorAll('input[name="employer-scope"]').forEach((input) => input.addEventListener('change', () => {
  employerPage = 1;
  loadEmployers().catch((error) => notice(error.message, true));
}));
$('#employers-prev').addEventListener('click', () => { employerPage = Math.max(1, employerPage - 1); loadEmployers().catch((error) => notice(error.message, true)); });
$('#employers-next').addEventListener('click', () => { employerPage += 1; loadEmployers().catch((error) => notice(error.message, true)); });

for (const site of ['linkedin', 'facebook']) {
  $(`#setup-${site}-start`).addEventListener('click', async () => {
    try { await api('/api/setup/browser/start', {method:'POST', body:JSON.stringify({site})}); await loadSetup(); }
    catch(error) { notice(error.message, true); }
  });
}

$('#setup-smtp-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  try {
    const data = Object.fromEntries(new FormData(event.target));
    data.port = Number(data.port);
    await api('/api/setup/smtp', {method:'POST', body:JSON.stringify(data)});
    event.target.elements.password.value = '';
    delete event.target.dataset.dirty;
    await loadSetup(); notice('Email settings saved');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#smtp-gmail-preset').addEventListener('click', async () => {
  try {
    const profile = await api('/api/profile');
    const form = $('#setup-smtp-form');
    form.elements.host.value = 'smtp.gmail.com';
    form.elements.port.value = '465';
    form.elements.user.value = profile.email || '';
    form.elements.from_address.value = profile.email || '';
    form.elements.password.value = '';
    form.dataset.dirty = 'true';
    $('#smtp-send-test').disabled = true;
    $('#smtp-test-result').textContent = 'Save your changes before sending a test.';
    form.elements.user.focus();
  } catch (error) { notice(error.message, true); }
});

$('#setup-smtp-remove').addEventListener('click', async () => {
  if (!window.confirm('Remove saved email settings? You will need to enter the SMTP credentials again to send email applications.')) return;
  try { await api('/api/setup/smtp', {method:'DELETE'}); $('#setup-smtp-form').reset(); delete $('#setup-smtp-form').dataset.initialized; delete $('#setup-smtp-form').dataset.dirty; await loadSetup(); notice('Email settings removed'); }
  catch(error) { notice(error.message, true); }
});

for (const eventName of ['input', 'change']) $('#setup-smtp-form').addEventListener(eventName, () => {
  $('#setup-smtp-form').dataset.dirty = 'true';
  $('#smtp-send-test').disabled = true;
  $('#smtp-test-result').textContent = 'Save your changes before sending a test.';
});

$('#smtp-send-test').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  beginPending(button, 'Sending test…');
  $('#smtp-test-result').textContent = 'Connecting to your email server…';
  try {
    await api('/api/setup/smtp/test', {method:'POST'});
    await loadSetup();
  } catch (error) {
    await loadSetup();
    notice(error.message, true);
  } finally {
    endPending(button);
  }
});

$('#setup-telegram-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  try {
    const form = event.target;
    const data = {
      token: form.elements.token.value,
      chat_id: form.elements.chat_id.value,
      application_reviews: form.elements.application_reviews.checked,
      strong_job_alerts: form.elements.strong_job_alerts.checked,
      daily_digest: form.elements.daily_digest.checked,
      digest_time: form.elements.digest_time.value || '18:00',
      quiet_start: form.elements.quiet_start.value,
      quiet_end: form.elements.quiet_end.value,
    };
    await api('/api/setup/telegram', {method:'POST', body:JSON.stringify(data)});
    form.elements.token.value = '';
    delete form.dataset.initialized;
    $('#setup-telegram-message').textContent = 'Telegram notification preferences saved.';
    await loadSetup(); notice('Telegram notification preferences saved');
  } catch(error) { $('#setup-telegram-message').textContent = error.message; notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#telegram-find-chat').addEventListener('click', async () => {
  const form = $('#setup-telegram-form');
  const results = $('#telegram-chat-results');
  results.textContent = 'Looking for chats…';
  try {
    const data = await api('/api/setup/telegram/chats', {method:'POST', body:JSON.stringify({token:form.elements.token.value})});
    if (!data.chats.length) { results.textContent = 'No chats yet. Send your bot a message in Telegram, then try again.'; return; }
    results.innerHTML = `<p class="hint">Choose where alerts should go:</p>${data.chats.map((chat) => `<button type="button" class="secondary" data-chat-id="${escapeHtml(chat.id)}">${escapeHtml(chat.name)} · ${escapeHtml(chat.id)}</button>`).join('')}`;
    results.querySelectorAll('[data-chat-id]').forEach((button) => button.addEventListener('click', () => {
      form.elements.chat_id.value = button.dataset.chatId;
      results.textContent = `Selected ${button.dataset.chatId}. Save Telegram to finish.`;
    }));
  } catch(error) { results.textContent = error.message; notice(error.message, true); }
});

$('#setup-telegram-remove').addEventListener('click', async () => {
  if (!window.confirm('Remove Telegram notification settings? You will need the bot token and chat configuration to reconnect it.')) return;
  try { await api('/api/setup/telegram', {method:'DELETE'}); $('#setup-telegram-form').reset(); delete $('#setup-telegram-form').dataset.initialized; await loadSetup(); notice('Telegram notifications removed'); }
  catch(error) { notice(error.message, true); }
});

$('#add-skill').addEventListener('click', () => appendRepeatable($('#skills-editor'), skillRow()));
$('#add-skill-group').addEventListener('click', () => appendRepeatable($('#skill-groups-editor'), groupRow()));
$('#add-education').addEventListener('click', () => appendRepeatable($('#education-editor'), educationRow()));
$('#add-achievement').addEventListener('click', () => appendRepeatable($('#achievements-editor'), simpleRow('data-achievement', '', 'Achievement')));
$('#add-link').addEventListener('click', () => appendRepeatable($('#links-editor'), simpleRow('data-profile-link type="url"', '', 'https://...')));

$('#profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  try {
    const profile = await api('/api/profile');
    const form = event.target;
    for (const key of ['name','application_name','application_school','given_name','family_name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) profile[key] = form.elements[key].value.trim();
    profile.skills = [...form.querySelectorAll('[data-skill]')].map((input) => input.value.trim()).filter(Boolean);
    profile.links = [...form.querySelectorAll('[data-profile-link]')].map((input) => input.value.trim()).filter(Boolean);
    profile.achievements = [...form.querySelectorAll('[data-achievement]')].map((input) => input.value.trim()).filter(Boolean);
    profile.education = [...form.querySelectorAll('.repeatable-row--education')].map((row) => ({
      school:row.querySelector('[data-education-school]').value.trim(),
      degree:row.querySelector('[data-education-degree]').value.trim(),
      dates:row.querySelector('[data-education-dates]').value.trim(),
    })).filter((item) => item.school || item.degree || item.dates);
    profile.skill_groups = Object.fromEntries([...form.querySelectorAll('.repeatable-row--group')].map((row) => [
      row.querySelector('[data-skill-group-label]').value.trim(),
      row.querySelector('[data-skill-group-values]').value.trim(),
    ]).filter(([label]) => label));
    await api('/api/profile', {method:'PUT', body:JSON.stringify(profile)});
    await loadPersonalDetails();
    notice('Personal details saved');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#search-intent-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const pendingButton = beginPending(event.submitter || form.querySelector('button[type="submit"]'), 'Saving…');
  const selected = (name) => [...form.elements[name].selectedOptions].map((option) => option.value);
  const payload = {
    role_families: commaList(form.elements.role_families.value),
    seniority_levels: selected('seniority_levels'),
    preferred_locations: commaList(form.elements.preferred_locations.value),
    work_modes: selected('work_modes'),
    preferred_employers: commaList(form.elements.preferred_employers.value),
    excluded_employers: commaList(form.elements.excluded_employers.value),
    negative_keywords: commaList(form.elements.negative_keywords.value),
    max_required_experience_years: form.elements.max_required_experience_years.value ? Number(form.elements.max_required_experience_years.value) : null,
    minimum_salary: form.elements.minimum_salary.value ? Number(form.elements.minimum_salary.value) : null,
    salary_currency: form.elements.salary_currency.value.trim() || 'VND',
    salary_unknown_ok: form.elements.salary_unknown_ok.checked,
    strong_match_threshold: currentSearchIntent?.strong_match_threshold ?? 80,
    preference_modes: {
      ...(currentSearchIntent?.preference_modes || {}),
      ...Object.fromEntries(SEARCH_MODE_FIELDS.map((name) => [name, form.elements[`auto_${name}`].checked ? 'auto' : 'custom'])),
    },
    hard_constraints: Object.fromEntries(['role_family','seniority','location','work_mode','experience','employer','minimum_salary']
      .map((key) => [key, form.elements[`hard_${key}`].checked])),
  };
  try {
    const saved = await api('/api/search-intent', {method:'PUT', body:JSON.stringify(payload)});
    renderSearchIntentForm(saved);
    delete $('#auto-apply-form').dataset.initialized;
    $('#search-intent-message').textContent = 'Saved. Existing jobs are being rescored against these preferences.';
    notice('Search preferences saved');
  } catch(error) { $('#search-intent-message').textContent = error.message; notice(error.message, true); }
  finally { endPending(pendingButton); }
});

for (const name of SEARCH_MODE_FIELDS) {
  const control = $('#search-intent-form').elements[`auto_${name}`];
  control?.addEventListener('change', () => {
    const custom = !control.checked;
    const valueControl = $('#search-intent-form').elements[name];
    if (valueControl) valueControl.disabled = !custom;
    const source = $('#search-intent-form').querySelector(`[data-preference-source="${name}"]`);
    if (source) source.textContent = custom ? 'Custom · saved when you choose Save preferences.' : 'Auto · follows your profile after saving.';
  });
}

$('#matching-model-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  try {
    await api('/api/matching/model', {method:'PUT', body:JSON.stringify({model:event.target.elements.model.value})});
    await loadSettings();
    notice('Matching model saved. Existing jobs are being reviewed.');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); const model = event.target.elements.model.value; pendingButton.disabled = !model || model === event.target.dataset.saved; }
});

$('#matching-download').addEventListener('click', async () => {
  try {
    await api('/api/matching/model/download', {method:'POST'});
    await loadMatchingModels();
    notice('Model download started. Progress is shown in Settings.');
  } catch(error) { notice(error.message, true); }
});

$('#matching-download-cancel').addEventListener('click', async () => {
  try {
    await api('/api/matching/model/download', {method:'DELETE'});
    await loadMatchingModels();
    notice('Model download cancelled');
  } catch(error) { notice(error.message, true); }
});

$('#matching-refresh').addEventListener('click', () => loadMatchingModels().catch((error) => notice(error.message, true)));

$('#provider-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  try {
    const provider = event.target.elements.provider.value;
    const result = await api('/api/profile/provider', {method:'PUT', body:JSON.stringify({provider})});
    await loadSettings();
    notice(result.automatic_drafts_paused
      ? 'ChatGPT Web saved. Automatic drafts paused; prepare jobs manually and review the browser reply.'
      : 'Application writing provider saved.');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#provider-form').elements.provider.addEventListener('change', (event) => {
  $('#chatgpt-web-setup').hidden = event.target.value !== 'chatgpt_web';
});

$('#chatgpt-web-login').addEventListener('click', async (event) => {
  const button = beginPending(event.currentTarget, 'Opening Chrome…');
  try {
    const result = await api('/api/chatgpt/login', {method:'POST', body:'{}'});
    if (result.status === 'already_logged_in' || result.status === 'saved' || result.logged_in) {
      $('#provider-form').elements.provider.value = 'chatgpt_web';
      await loadSettings();
    }
    notice(result.detail);
  } catch(error) { notice(error.message, true); }
  finally { endPending(button); }
});

$('#pdf-resume-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Extracting…');
  const provider = event.target.elements.provider.value;
  $('#pdf-import-status').textContent = provider
    ? `Reading the PDF with ${providerLabel(provider)} using the processing choice shown above.`
    : 'Choose how this resume should be processed.';
  try {
    const result = await api('/api/profile/resume/pdf', {method:'POST', body:new FormData(event.target)});
    event.target.reset();
    $('#pdf-import-status').textContent = `Extracted ${result.positions} positions, ${result.education} education entries, and ${result.achievements} achievements. Review Personal details and Work history.`;
    await showTab('personal');
    notice('Resume details extracted. Review them before applying.');
  } catch(error) { $('#pdf-import-status').textContent = error.message; notice(error.message, true); }
  finally { endPending(button); }
});

$('#latex-import-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Importing…');
  try {
    const latex = event.target.elements.latex.value;
    const result = await api('/api/profile/import-latex', {method:'POST', body:JSON.stringify({latex})});
    event.target.reset();
    await showTab('personal');
    notice(`Imported ${result.positions} positions, ${result.education} education entries, and ${result.achievements} achievements`);
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#source-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Adding…');
  try {
    const data = Object.fromEntries(new FormData(event.target));
    const result = await api('/api/sources', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); syncSourceNameField(); await loadSources(); notice(`${result.name} added`);
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

function syncSourceNameField() {
  const form = $('#source-form');
  const kind = form.elements.kind.value;
  const needsName = kind === 'career';
  const label = $('#source-name-label');
  label.hidden = !needsName;
  form.elements.name.required = needsName;
  const hint = $('#source-url-hint');
  hint.hidden = needsName;
  hint.textContent = kind === 'linkedin'
    ? 'Paste a LinkedIn Jobs search link. Job Radar names it from the search terms and location.'
    : 'Paste a Facebook group link. Job Radar names it from the page or link.';
  form.elements.url.placeholder = kind === 'linkedin' ? 'https://www.linkedin.com/jobs/search/?keywords=...'
    : kind === 'facebook' ? 'https://www.facebook.com/groups/...' : 'https://example.com/careers';
  if (!needsName) form.elements.name.value = '';
}
$('#source-form [name="kind"]').addEventListener('change', syncSourceNameField);
syncSourceNameField();

$('#employer-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Adding…');
  try {
    const data = Object.fromEntries(new FormData(event.target));
    if (!data.career_url) data.career_url = null;
    await api('/api/employers', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadEmployers(); notice('Employer added');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#job-import').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Adding…');
  try {
    const data = Object.fromEntries(new FormData(event.target));
    if (!data.apply_url) data.apply_url = null;
    const result = await api('/api/jobs/import', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadJobs(); await showJob(result.id); notice('Job added');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#position-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Adding…');
  try {
    const data = Object.fromEntries(new FormData(event.target));
    data.bullets = data.bullets.split('\n').map((x) => x.trim()).filter(Boolean);
    await api('/api/positions', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadPositions(); notice('Position added');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

async function inspectSelectedRepository(url, button = null) {
  const provider = $('#project-processing-provider').value;
  if (!provider || !projectProviderReady) {
    $('#project-add-status').textContent = 'Choose an available processing option for this project first.';
    return;
  }
  if (button) button.disabled = true;
  $('#project-add-status').textContent = `Reading the repository and drafting project evidence with ${providerLabel(provider)}…`;
  try {
    const result = await api('/api/repositories/inspect', {method:'POST', body:JSON.stringify({url, provider})});
    await loadEvidence(result.evidence_id);
    $('#project-add-status').textContent = result.generation_warning
      ? `Repository added, but the draft needs attention: ${result.generation_warning}`
      : 'Project draft ready. Review the claims below, then save it and choose whether to include it in resumes.';
    scrollNodeIntoView($('#project-editor'), {block:'start'});
  } catch(error) {
    $('#project-add-status').textContent = error.message;
    notice(error.message, true);
  } finally { if (button && button.isConnected) button.disabled = false; }
}

function parseGitHubEntry(input) {
  const value = input.trim().replace(/^@/, '');
  if (/^[A-Za-z0-9][A-Za-z0-9-]*$/.test(value)) return {username:value};
  let url;
  try { url = new URL(value); } catch { throw new Error('Enter a GitHub username or an HTTPS repository URL.'); }
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.search || url.hash) throw new Error('Use a github.com profile or HTTPS repository URL.');
  const segments = url.pathname.split('/').filter(Boolean);
  if (segments.length === 1) return {username:segments[0]};
  if (segments.length === 2) return {repository:url.href};
  throw new Error('Use a GitHub profile or repository URL.');
}

$('#project-add-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  beginPending(button, 'Working…');
  try {
    const entry = parseGitHubEntry($('#project-github-input').value);
    if (entry.repository) {
      const saved = projectCards.find((card) => card.repository_url && normalizeRepoUrl(card.repository_url) === normalizeRepoUrl(entry.repository));
      if (saved) {
        await loadEvidence(saved.id);
        $('#project-add-status').textContent = 'This repository is already in your projects. Review it below.';
        scrollNodeIntoView($('#project-editor'), {block:'start'});
        return;
      }
      await inspectSelectedRepository(entry.repository, button);
      return;
    }
    button.disabled = true;
    $('#project-add-status').textContent = `Finding public repositories for ${entry.username}…`;
    discoveredRepos = await api(`/api/github/${encodeURIComponent(entry.username)}/repositories`);
    $('#project-add-status').textContent = discoveredRepos.length
      ? `Choose a repository below. Job Radar will draft its description for your review.`
      : 'No public repositories found for this username.';
    $('#github-repos').innerHTML = discoveredRepos.length ? '' : '<div class="empty">No public repositories found.</div>';
    renderRepositoryResults();
  } catch(error) { $('#project-add-status').textContent = error.message; notice(error.message, true); }
  finally { endPending(button); }
});

$('#scan-now').addEventListener('click', async () => {
  const button = $('#scan-now');
  try {
    beginPending(button, 'Checking…');
    const result = await api('/api/scan/now', {method:'POST'});
    await Promise.all([loadSources(), loadHome()]);
    const blocked = result.sign_in_needed?.length ? ` ${result.sign_in_needed.join(' and ')} need sign-in.` : '';
    notice(result.queued ? `Checking ${result.queued} source${result.queued === 1 ? '' : 's'} now.${blocked}` : `All available sources are already checking or queued.${blocked}`);
  } catch(error) { notice(error.message, true); }
  finally { endPending(button); }
});

$('#scan-due').addEventListener('click', async () => {
  try { const result = await api('/api/scan/due', {method:'POST'}); await loadSources(); notice(result.queued ? `${result.queued} due sources added to the scan queue.` : 'All due sources are already queued or scanning.'); }
  catch(error) { notice(error.message, true); }
});
$('#scan-unscanned').addEventListener('click', async () => {
  try { const result = await api('/api/scan/unscanned', {method:'POST'}); await loadSources(); notice(result.queued || result.prioritized ? `${result.queued} added and ${result.prioritized} moved forward. ${result.waiting} waiting, ${result.scanning} scanning.` : 'All unscanned sources are already first in the queue or need sign-in.'); }
  catch(error) { notice(error.message, true); }
});

window.addEventListener('popstate', () => showTab(location.hash.slice(1) || 'home', 'none'));
function refreshAfterReturn() {
  clearTimeout(window.returnRefresh);
  window.returnRefresh = setTimeout(() => {
    const active = document.querySelector('.tab.active')?.id;
    const refresh = active === 'settings' ? loadSettings : active === 'home' ? loadHome : refreshSocialAuth;
    refresh().catch((error) => notice(error.message, true));
  }, 100);
}
window.addEventListener('focus', refreshAfterReturn);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshAfterReturn(); });
showTab(location.hash.slice(1) || 'home', 'replace');
