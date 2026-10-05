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
let employerPage = 1;
let projectCards = [];
let discoveredRepos = [];
let selectedProjectId = null;
let projectProviderReady = false;
let applicationDrafts = [];
let activeApplicationId = null;
let applicationWorkspaceView = 'drafts';
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
    : status === 'dismissed' ? 'Failed analysis dismissed. This job remains in your list without a match score.'
    : ['pending', 'running'].includes(status) ? 'Job Radar is reading the posting and checking the match.'
    : 'Basic match only. Choose a matching model in Settings for a detailed review.';
  const retry = status === 'failed' ? `<div class="actions"><button type="button" data-analyze="${job.id}" class="secondary">Try analysis again</button><button type="button" data-dismiss-analysis="${job.id}" class="secondary">Dismiss failed analysis</button></div>`
    : status === 'dismissed' ? `<button type="button" data-analyze="${job.id}" class="secondary">Run analysis again</button>` : '';
  const completed = status === 'done';
  const facts = completed ? score?.facts : null;
  const list = (label, items) => items?.length ? `<div><strong>${label}</strong><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : '';
  const factsHtml = facts ? `<div class="job-facts"><p class="hint">Extracted by the local model. Check the original posting before applying.</p>
    ${facts.summary ? `<p>${escapeHtml(facts.summary)}</p>` : ''}
    <div class="fact-grid"><div><strong>Role</strong><p>${escapeHtml([facts.role, facts.seniority].filter(Boolean).join(' · ') || 'Not stated')}</p></div>
    <div><strong>Experience</strong><p>${facts.years_required == null ? 'Not stated' : `${escapeHtml(facts.years_required)} years required`}</p></div>
    <div><strong>Location and mode</strong><p>${escapeHtml([facts.location, facts.work_mode].filter(Boolean).join(' · ') || 'Not stated')}</p></div>
    <div><strong>Salary range</strong><p>${escapeHtml(facts.salary_range || 'Not stated')}</p></div></div>
    <div class="fact-grid">${list('Required skills', facts.required_skills)}${list('Preferred skills', facts.preferred_skills)}${list('Responsibilities', facts.responsibilities)}${list('Education', facts.education)}${list('Spoken languages', facts.languages)}</div></div>` : '';
  const labels = {role:'Role', required_skills:'Required skills', preferred_skills:'Preferred skills', experience:'Years of experience',
    responsibilities:'Responsibilities', location:'Location', work_mode:'Work mode', education:'Education', freshness:'Freshness'};
  const weighted = completed && score?.criteria ? Object.entries(labels).map(([key,label]) => {
    const item = score.criteria[key];
    if (!item) return '';
    const weight = Number(score.weights?.[key] || 0);
    return `<div class="criterion-row"><div class="criterion-copy"><strong>${label}</strong><small>${escapeHtml(item.reason)}</small></div><div class="criterion-score"><span>${escapeHtml(item.score)}/10</span><small>${weight}% weight</small></div><div class="criterion-weight"><span style="width:${Math.max(4, Math.min(100, weight))}%"></span></div></div>`;
  }).join('') : '';
  const criteria = weighted ? `<details class="detail-disclosure"><summary>Match breakdown <span>${job.score}/100</span></summary><p>${escapeHtml(score.explanation || '')}</p>
    ${score.hard_exclusions?.length ? `<div class="match-exclusions"><strong>Score is 0 because:</strong><ul>${score.hard_exclusions.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul></div>` : ''}
    <div class="criteria-list">${weighted}<div class="criterion-row criterion-info"><div class="criterion-copy"><strong>Salary range</strong><small>${escapeHtml(facts?.salary_range || 'Not stated in the posting.')} Salary is not included in the match score.</small></div><div class="criterion-score"><span>Info</span></div></div></div></details>` : '';
  return `<div class="review-section"><p class="hint">${stateMessage}</p>${retry}</div>${factsHtml ? `<details class="detail-disclosure" open><summary>Job at a glance</summary>${factsHtml}</details>` : ''}${criteria}`;
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? (options.headers || {}) : {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  const raw = await response.text();
  let body;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = raw; }
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `${response.status} ${response.statusText}`);
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

function notice(message, error = false) {
  const node = $('#notice');
  clearTimeout(window.noticeTimeout);
  node.hidden = false;
  node.className = `toast${error ? ' error' : ''}`;
  node.setAttribute('role', error ? 'alert' : 'status');
  node.setAttribute('aria-live', error ? 'assertive' : 'polite');
  const messageNode = document.createElement('span');
  messageNode.className = 'toast-message';
  messageNode.textContent = message;
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'toast-dismiss';
  dismiss.setAttribute('aria-label', 'Dismiss notification');
  dismiss.textContent = '×';
  dismiss.addEventListener('click', clearNotice);
  node.replaceChildren(messageNode, dismiss);
  if (!error) window.noticeTimeout = setTimeout(clearNotice, 6000);
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
  indicator.hidden = !loading;
  if (loading) indicator.textContent = `Loading ${($('#page-title').textContent || 'page').toLowerCase()}…`;
}

function socialSiteNames(browser) {
  return (browser.sites || []).map((site) => site === 'linkedin' ? 'LinkedIn' : 'Facebook').join(' and ');
}

function setStepStatus(selector, label, tone = '') {
  const node = $(selector);
  node.textContent = label;
  node.className = statusClass(tone);
}

function renderSocialAuth(browser) {
  const expired = (browser.sites || []).length > 0;
  const connected = browser.connected_sites || [];
  $('#social-auth-banner').hidden = !expired;
  $('#social-auth-message').textContent = expired
    ? `${socialSiteNames(browser)} sign-in expired. Sign in again to resume those scans.` : '';
  const socialReady = !expired && connected.length === 2 && !['opening', 'open'].includes(browser.state);
  setStepStatus('#social-sign-in-status', expired ? 'Sign in again'
    : ['opening', 'open'].includes(browser.state) ? 'Waiting for sign-in'
    : socialReady ? 'Both connected' : `${connected.length} of 2 connected`, socialReady ? '' : 'warning');
  for (const site of ['linkedin', 'facebook']) {
    const label = site === 'linkedin' ? 'LinkedIn' : 'Facebook';
    const needsSignIn = !connected.includes(site) || (browser.sites || []).includes(site);
    $(`#${site}-sign-in-status`).textContent = (browser.sites || []).includes(site) ? 'Session expired'
      : connected.includes(site) ? 'Connected' : 'Sign-in needed';
    const button = $(`#setup-${site}-start`);
    button.textContent = needsSignIn ? `Sign in to ${label}` : `Reconnect ${label}`;
    button.disabled = ['opening', 'open'].includes(browser.state);
  }
}

async function refreshSocialAuth() {
  const setup = await api('/api/setup');
  renderSocialAuth(setup.browser);
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
  setStepStatus('#setup-telegram-status', data.telegram_configured ? 'Configured' : 'Optional', data.telegram_configured ? '' : 'muted');
  setStepStatus('#matching-status', data.matching.model ? `Configured · ${data.matching.model}` : 'Choose a model', data.matching.model ? '' : 'warning');
  renderSocialAuth(data.browser);
  $('#setup-browser-detail').textContent = data.browser.error || (data.browser.state === 'opening' ? 'Opening Chrome…' : data.browser.state === 'open' ? `Waiting for ${data.browser.active_site === 'linkedin' ? 'LinkedIn' : 'Facebook'} sign-in in Chrome. The window closes automatically when the account page loads.` : data.browser.state === 'reauth_required' ? `${socialSiteNames(data.browser)} needs a new sign-in. Other sources keep scanning.` : data.browser.last_saved_at ? `Saved session last updated ${when(data.browser.last_saved_at)}. Upcoming scans will verify site access.` : 'Choose a site to begin. Google sign-in opens in regular Chrome.');
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
    alertForm.dataset.initialized = 'true';
  }
  if ((['opening', 'open'].includes(data.browser.state) || data.matching.download_state === 'downloading') && $('#settings').classList.contains('active')) window.setupPoll = setTimeout(() => loadSetup().catch((error) => notice(error.message, true)), 2000);
  return data;
}

async function loadHome() {
  const [data, profile, setup] = await Promise.all([api('/api/status'), api('/api/profile'), api('/api/setup')]);
  renderSocialAuth(setup.browser);
  const c = data.counts;
  $('#metrics').innerHTML = [
    ['High-fit new jobs', c.high_fit_new], ['New in 24h', c.recent_jobs],
    ['Drafts to review', c.drafts_needing_review], ['Analysis failures', c.analysis_failures],
  ].map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');

  const socialSources = [
    setup.linkedin_searches ? `${setup.linkedin_searches} LinkedIn searches` : '',
    setup.facebook_groups ? `${setup.facebook_groups} Facebook groups` : '',
  ].filter(Boolean);
  $('#source-summary').textContent = socialSources.length
    ? `${socialSources.join(' and ')} ${setup.browser.sites.length ? `need ${socialSiteNames(setup.browser)} sign-in again.` : setup.browser.connected_sites.length ? 'are connected.' : 'are waiting for browser sign-in.'}`
    : 'Company career feeds continue scanning without social sign-in.';

  const hasProfile = Boolean(profile.name && profile.email);
  const experienceCount = (profile.experience || []).length;
  const required = [
    {label:'Set up AI models', detail:!profile.drafting_provider ? 'Choose an application writing provider' : !setup.matching.model ? 'Choose a local job matching model' : 'Application writing and matching are ready', done:!!profile.drafting_provider && !!setup.matching.model, tab:'settings', panel:'provider-panel'},
    {label:'Add personal details', detail:'Name and email are required for applications', done:hasProfile, tab:'personal'},
    {label:'Add work history', detail:experienceCount ? `${experienceCount} position${experienceCount === 1 ? '' : 's'} saved` : 'Add experience for tailored applications', done:experienceCount > 0, tab:'experience'},
    {label:'Select projects', detail:'Choose at least one project for tailored applications', done:setup.approved_evidence > 0, tab:'projects'},
    {label:setup.browser.sites.length ? 'Sign in to social sites again' : 'Connect job-source accounts', detail:setup.browser.sites.length ? `${socialSiteNames(setup.browser)} session expired` : 'Required while LinkedIn or Facebook sources are enabled', done:(!setup.linkedin_searches && !setup.facebook_groups) || (setup.browser.connected_sites.length === 2 && !setup.browser.sites.length), tab:'settings', socialAuth:true},
  ];
  const optional = [
    {label:'Telegram reviews', done:setup.telegram_configured, detail:setup.telegram_configured ? 'Connected' : 'Optional · not configured', tab:'settings', panel:'telegram-panel'},
    {label:'Email sending', done:setup.smtp_test?.status === 'accepted', detail:setup.smtp_test?.status === 'accepted' ? 'Connected and tested' : setup.smtp_configured ? 'Optional · configured, test pending' : 'Optional · not configured', tab:'settings', panel:'smtp-panel'},
  ];
  const remaining = required.filter((step) => !step.done);
  const complete = remaining.length === 0;
  const next = remaining[0];

  $('#home-setup-status').textContent = complete ? 'Complete' : `${remaining.length} left`;
  $('#home-setup-status').className = complete ? 'status-badge status-badge--success' : 'status-badge status-badge--warning';
  $('#home-title').textContent = complete ? 'What needs your attention?' : 'Finish the essentials, then get out of setup mode.';
  $('#home-description').textContent = complete ? 'Prioritize strong jobs, review drafts, and fix anything blocking the pipeline.' : 'Complete the required application basics. Optional integrations can stay optional, as nature intended.';
  $('#home-hero').classList.toggle('is-compact', complete);
  $('#home-primary').textContent = next ? `${next.label} →` : (c.drafts_needing_review ? 'Review drafts →' : 'Review jobs →');
  $('#home-primary').dataset.tab = next?.tab || (c.drafts_needing_review ? 'applications' : 'jobs');
  $('#home-primary').dataset.socialAuth = next?.socialAuth ? 'true' : 'false';
  $('#home-primary').dataset.setupPanel = next?.panel || '';

  $('#home-steps').innerHTML = required.map((step) =>
    `<button class="step-row ${step.done ? 'is-done' : ''}" data-home-step="${step.tab}" data-social-auth="${step.socialAuth ? 'true' : 'false'}" data-setup-panel="${step.panel || ''}"><span class="step-check ${step.done ? 'done' : ''}">${step.done ? '✓' : '○'}</span><span><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail)}</small></span><span class="step-arrow">→</span></button>`
  ).join('');
  $('#home-optional').innerHTML = `<h4>Optional integrations</h4>${optional.map((step) =>
    `<button class="home-optional-row" data-home-step="${step.tab}" data-setup-panel="${step.panel || ''}"><span><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail)}</small></span><span class="status-badge ${step.done ? 'status-badge--success' : 'status-badge--neutral'}">${step.done ? 'Ready' : 'Optional'}</span></button>`
  ).join('')}`;
  document.querySelectorAll('[data-home-step]').forEach((button) => button.addEventListener('click', () => button.dataset.socialAuth === 'true' ? openSocialSignIn() : button.dataset.setupPanel ? openSetupPanel(button.dataset.setupPanel) : showTab(button.dataset.homeStep)));

  const actions = [
    ...(data.attention?.drafts || []).map((item) => ({kind:'draft', id:item.id, title:item.title, company:item.company, meta:item.status === 'needs_review' ? 'Needs changes' : 'Ready for review'})),
    ...(data.attention?.jobs || []).map((item) => ({kind:'job', id:item.id, title:item.title, company:item.company, meta:`${item.score}/100 match`})),
    ...(data.attention?.failures || []).map((item) => ({kind:'job', id:item.id, title:item.title, company:item.company, meta:'Match review failed'})),
  ].slice(0, 8);
  $('#home-action-count').textContent = actions.length ? `${actions.length} item${actions.length === 1 ? '' : 's'} worth opening` : 'Nothing urgent right now';
  $('#home-actions').innerHTML = actions.length ? actions.map((item) =>
    `<button class="home-action-row" data-home-action="${item.kind}" data-home-id="${item.id}"><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.company)} · ${escapeHtml(item.meta)}</small></span><span class="step-arrow">→</span></button>`
  ).join('') : '<p class="queue-empty">No drafts, strong new matches, or failed analyses need attention.</p>';
  $('#home-actions').querySelectorAll('[data-home-action]').forEach((button) => button.addEventListener('click', async () => {
    if (button.dataset.homeAction === 'draft') { await showTab('applications'); await showApplication(button.dataset.homeId); }
    else { await showTab('jobs'); await showJob(button.dataset.homeId); }
  }));
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
  const attention = queue.analysis.failed.map((item) => ({kind:'analysis', label:'Match', item, status:'Needs attention'}));
  const preview = [...running, ...attention, ...waiting].slice(0, 6);
  const total = running.length + waiting.length;
  $('#home-queue-count').textContent = `${total} in queue${queue.analysis.failed.length ? ` · ${queue.analysis.failed.length} need attention` : ''}`;
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
      await showTab('jobs'); await showJob(id);
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
    drafts:[...lane(data.drafts.active, 'active'), ...lane(data.drafts.waiting, 'waiting')],
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
    const blocked = id === 'analysis' ? analysis.failed.length : 0;
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
  queueWaiting($('#queue-draft-waiting'), drafts.waiting, 'draft', () => drafts.enabled ? 'Ready to draft' : 'Automation off');
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
      await showTab('jobs'); activeJob = id; activeJobPinned = true; syncJobsHash(); await showJob(id, true);
      scrollNodeIntoView($('#job-detail'), {block:'start'}); $('#job-detail').focus({preventScroll:true});
    }
  }));
  $('#queue-updated-at').textContent = `Updated ${queueUpdateTime()} · Auto-refresh every 5 seconds`;
  announceQueueUpdate(data, reason, changed);
  restoreQueueFocus(focusToken);
  if ($('#queue').classList.contains('active')) window.queuePoll = setTimeout(() => loadQueue({reason:'poll'}).catch((error) => notice(error.message, true)), 5000);
}

const JOB_FILTERS = {
  q:'#job-query', state:'#job-state', score:'#job-score', freshness:'#job-freshness',
  mode:'#job-work-mode', location:'#job-location', source:'#job-source', seniority:'#job-seniority', sort:'#job-sort',
};

function readJobsHashState() {
  const raw = location.hash.slice(1);
  const [route, search = ''] = raw.split('?');
  if (route !== 'jobs') return;
  const params = new URLSearchParams(search);
  for (const [key, selector] of Object.entries(JOB_FILTERS)) {
    const node = $(selector);
    if (node) node.value = params.get(key) || (key === 'sort' ? 'best' : '');
  }
  jobsPage = Math.max(1, Number(params.get('page') || 1));
  activeJob = params.get('job') || null;
  activeJobPinned = Boolean(activeJob);
}

function jobsHash() {
  const params = new URLSearchParams();
  for (const [key, selector] of Object.entries(JOB_FILTERS)) {
    const value = $(selector)?.value?.trim();
    if (value && !(key === 'sort' && value === 'best')) params.set(key, value);
  }
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

function topMatchSignals(job) {
  const signals = (job.match_signals || []).filter((item) => Number.isFinite(item.score));
  const positive = signals.filter((item) => item.score >= 8).sort((a,b) => b.score - a.score).slice(0, 1);
  const negative = signals.filter((item) => item.score <= 4).sort((a,b) => a.score - b.score).slice(0, 1);
  return [...positive.map((item) => ({...item, tone:'good'})), ...negative.map((item) => ({...item, tone:'warn'}))];
}

async function loadJobs() {
  clearTimeout(window.jobPoll);
  const query = new URLSearchParams({
    q: $('#job-query').value, state: $('#job-state').value, page: jobsPage, page_size: 25,
    min_score: $('#job-score').value, freshness: $('#job-freshness').value, work_mode: $('#job-work-mode').value,
    location: $('#job-location').value, source: $('#job-source').value, seniority: $('#job-seniority').value,
    sort: $('#job-sort').value,
  });
  [...query.entries()].forEach(([key,value]) => { if (value === '') query.delete(key); });
  const [result, analysis] = await Promise.all([api(`/api/jobs/page?${query}`), loadJobAnalysis()]);
  if (jobsPage > result.pages) { jobsPage = result.pages; syncJobsHash(); return loadJobs(); }
  const jobs = result.items;
  $('#jobs-page-summary').textContent = result.total ? `Page ${result.page} of ${result.pages} · ${result.total} jobs` : 'No jobs';
  $('#jobs-prev').disabled = jobsPage <= 1;
  $('#jobs-next').disabled = jobsPage >= result.pages;
  const sourceLabel = (source) => !source ? 'Manual' : source.kind === 'career' ? 'Career page' : source.kind === 'linkedin' ? 'LinkedIn' : 'Facebook';
  $('#job-list').innerHTML = jobs.length ? jobs.map((job) => {
    const signals = topMatchSignals(job);
    const tags = [job.work_mode, job.seniority, sourceLabel(job.source)].filter(Boolean);
    const dateValue = job.published_at || job.first_seen_at;
    return `<div class="item job-card surface-action"><button type="button" class="card-select" data-job="${job.id}" aria-pressed="${job.id === activeJob ? 'true' : 'false'}" aria-label="Open ${escapeHtml(job.title)} at ${escapeHtml(job.company)}">
      ${scoreBadge(job)}<div class="item-title">${escapeHtml(job.title)}</div>
      <div class="job-card-company">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div>
      <div class="job-card-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join('')}</div>
      <div class="job-card-signals">${signals.map((signal) => `<span class="${signal.tone}" title="${escapeHtml(signal.reason || '')}">${signal.tone === 'good' ? '✓' : '!' } ${escapeHtml(signal.label)}</span>`).join('')}</div>
      <div class="job-card-footer"><span${exactTimeTitle(dateValue)}>${job.published_at ? 'Posted' : 'Found'} ${relativeWhen(dateValue)}</span><span class="status-badge status-badge--neutral">${escapeHtml(job.state)}</span></div>
    </button>${job.source ? `<a href="${escapeHtml(job.source.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open original posting for ${escapeHtml(job.title)} at ${escapeHtml(job.company)}">Original posting ↗</a>` : ''}</div>`;
  }).join('') : '<div class="empty">No jobs match these filters.</div>';
  document.querySelectorAll('[data-job]').forEach((node) => node.addEventListener('click', async () => {
    try {
      activeJob = node.dataset.job; activeJobPinned = true; syncJobsHash('push');
      await showJob(node.dataset.job, true);
      if (window.matchMedia('(max-width: 900px)').matches) scrollNodeIntoView($('#job-detail'), {block:'start'});
      $('#job-detail').focus({preventScroll:true});
    } catch(error) { notice(error.message, true); }
  }));
  if (activeJob && (activeJobPinned || jobs.some((job) => job.id === activeJob))) await showJob(activeJob, activeJobPinned);
  else {
    activeJob = null; activeJobPinned = false; syncJobsHash();
    $('#job-detail').innerHTML = `<div class="empty">${jobs.length ? 'Select a job to see details.' : 'No job matches this search.'}</div>`;
  }
  if ((analysis.pending || jobs.some((job) => ['pending','running'].includes(job.analysis_status))) && $('#jobs').classList.contains('active')) {
    window.jobPoll = setTimeout(() => loadJobs().catch((error) => notice(error.message, true)), 5000);
  }
}

async function showJob(id, pin = false) {
  activeJob = id;
  activeJobPinned = pin;
  document.querySelectorAll('[data-job]').forEach((node) => {
    const selected = node.dataset.job === id;
    node.closest('.item').classList.toggle('is-selected', selected);
    node.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  const job = await api(`/api/jobs/${id}`);
  const profile = await api('/api/profile');
  const provider = profile.drafting_provider || '';
  const links = job.observations.length ? job.observations.map((source) =>
    `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.kind)}: ${escapeHtml(source.name)}</a>`
  ).join('<br>') : '';
  const score = job.score_detail ? JSON.parse(job.score_detail) : null;
  const states = ['new','interesting','prepare','ready','applied','interview','offer','rejected','ignored'];
  $('#job-detail').setAttribute('tabindex', '-1');
  $('#job-detail').innerHTML = `<div class="job-detail-head"><div><h2>${escapeHtml(job.title)}</h2><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div>
    <div class="item-meta">${escapeHtml(job.work_mode || '')}${job.published_at ? ` · Posted <span${exactTimeTitle(job.published_at)}>${relativeWhen(job.published_at)}</span>` : ''} · Found <span${exactTimeTitle(job.first_seen_at)}>${relativeWhen(job.first_seen_at)}</span></div></div>
    <label class="job-state-control">Status<select id="job-state-control">${states.map((state) => `<option value="${state}" ${job.state === state ? 'selected' : ''}>${state.replace(/^./, (x) => x.toUpperCase())}</option>`).join('')}</select></label></div>
    <div class="review-section"><p class="hint">Drafting provider: ${escapeHtml(provider || 'Choose one in Settings first')} · <button class="text-button" data-tab="settings">Change provider</button></p><div class="actions"><button data-prepare="${id}" class="primary" ${provider ? '' : 'disabled'}>Prepare application</button></div></div>
    ${job.apply_url ? `<p><a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener noreferrer">${job.apply_url.startsWith('mailto:') ? 'Application email ↗' : 'Application page ↗'}</a></p>` : ''}
    ${!job.apply_url ? '<p class="hint">No application form has been verified for this posting. Check the original source for its application instructions before sending.</p>' : ''}
    ${links ? `<p class="item-meta">${links}</p>` : ''}
    ${renderJobAnalysis(job, score)}
    <details class="detail-disclosure"><summary>Original description</summary><div class="description">${formatDescription(job.description)}</div></details>`;
  $('#job-detail').querySelector('[data-tab="settings"]').addEventListener('click', () => showTab('settings'));
  $('#job-detail').querySelector('[data-analyze]')?.addEventListener('click', async () => {
    try { await api(`/api/jobs/${id}/analyze`, {method:'POST'}); notice('Match review queued'); await loadJobs(); }
    catch(error) { notice(error.message, true); }
  });
  $('#job-detail').querySelector('[data-dismiss-analysis]')?.addEventListener('click', () => dismissFailedAnalysis(id));
  $('#job-state-control').addEventListener('change', async (event) => {
    const next = event.target.value;
    const previous = job.state;
    if (['ignored','rejected'].includes(next) && !window.confirm(`Move this job to ${next}? You can restore it to New at any time.`)) {
      event.target.value = previous;
      return;
    }
    try {
      await api(`/api/jobs/${id}/state`, {method:'POST', body:JSON.stringify({state:next})});
      notice(`Job status changed to ${next}.`);
      await loadJobs();
    } catch(error) { event.target.value = previous; notice(error.message, true); }
  });
  $('#job-detail').querySelector('[data-prepare]').addEventListener('click', async () => {
    const button = $('#job-detail').querySelector('[data-prepare]');
    beginPending(button, 'Preparing…');
    try {
      const draft = await api(`/api/jobs/${id}/prepare`, {method:'POST', body:'{}'});
      if (draft.destination.kind === 'web') {
        try { await api(`/api/applications/${draft.id}/inspect`, {method:'POST'}); }
        catch(error) { notice(`Draft ready; form inspection needs attention: ${error.message}`, true); }
      }
      showTab('applications'); await loadApplications(draft.id);
    } catch(error) { notice(error.message, true); }
    finally { endPending(button); }
  });
}

function sourceStatusLabel(source) {
  const state = source.scan_state;
  return state === 'queued' ? `Queued · #${source.queue_position}` : ({
    scanning:'Scanning', auto_off:'Automatic scans off', needs_refresh:'Needs refresh',
    not_scanned:'Never scanned', success:'Healthy', empty:'Healthy · no jobs',
    failed:'Scan failed', auth_required:'Sign-in needed', interrupted:'Retry queued soon',
  })[state] || state;
}

function sourceStatusTone(state) {
  if (['scanning', 'success', 'empty', 'queued'].includes(state)) return 'status-badge--success';
  if (['failed', 'auth_required'].includes(state)) return 'status-badge--danger';
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
    if (status === 'attention' && !['failed', 'auth_required', 'needs_refresh', 'interrupted'].includes(source.scan_state)) return false;
    if (status === 'healthy' && !['success', 'empty'].includes(source.scan_state)) return false;
    if (status === 'unscanned' && source.scan_state !== 'not_scanned') return false;
    if (status && !['attention', 'healthy', 'unscanned'].includes(status) && source.scan_state !== status) return false;
    return true;
  });
  const sort = $('#source-sort').value;
  filtered.sort((a, b) => {
    if (sort === 'last_success') return new Date(b.last_success_at || 0) - new Date(a.last_success_at || 0) || a.name.localeCompare(b.name);
    if (sort === 'jobs') return (Number(b.new_job_count) || 0) - (Number(a.new_job_count) || 0) || a.name.localeCompare(b.name);
    if (sort === 'status') return sourceStatusLabel(a).localeCompare(sourceStatusLabel(b)) || a.name.localeCompare(b.name);
    return a.name.localeCompare(b.name);
  });
  return filtered;
}

async function loadSources() {
  clearTimeout(window.sourcePoll);
  const sources = await api('/api/sources');
  const visible = filterSources(sources);
  const running = sources.filter((source) => source.scan_state === 'scanning').length;
  const waiting = sources.filter((source) => source.scan_state === 'queued').length;
  const unscanned = sources.filter((source) => source.enabled && !source.last_success_at && !['queued', 'scanning'].includes(source.scan_state)).length;
  $('#source-queue-summary').textContent = `${running} scanning · ${waiting} queued · ${unscanned} never successfully scanned. LinkedIn and Facebook share one browser, so queued scans run in order.`;
  $('#source-result-summary').textContent = `Showing ${visible.length} of ${sources.length} configured sources`;
  $('#source-list').innerHTML = visible.length ? visible.map((source) => {
    const total = Number(source.job_count) || 0;
    const recent = Number(source.new_job_count) || 0;
    const state = source.scan_state;
    const status = sourceStatusLabel(source);
    const latest = source.latest_observed_count;
    const cap = Number(source.config?.max_results || source.config?.max_posts || 0);
    const latestText = latest == null ? 'No completed scan' : `${latest} postings checked${cap && latest >= cap ? ` · collection limit ${cap} reached` : ''}`;
    return `<div class="item source-card" data-source-id="${source.id}">
      <div class="source-card-head"><div><div class="item-title">${escapeHtml(source.name)} <span class="pill muted">${escapeHtml(source.kind)}</span></div><div class="item-meta">Last successful scan: ${when(source.last_success_at)}</div></div><span class="status-badge ${sourceStatusTone(state)}">${escapeHtml(status)}</span></div>
      <div class="source-health"><span><strong>${recent}</strong> new in latest scan</span></div>
      <div class="actions"><button data-scan="${source.id}" ${['scanning','queued'].includes(state) ? 'disabled' : ''}>${state === 'scanning' ? 'Scanning…' : state === 'queued' ? 'Queued' : 'Scan now'}</button><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open source ${escapeHtml(source.name)}">Open source ↗</a><label class="source-auto"><input type="checkbox" data-toggle="${source.id}" ${source.enabled ? 'checked' : ''}><span>Automatic every ${source.interval_minutes / 60} hours</span><span class="source-auto-status" aria-live="polite"></span></label></div>
      <details class="source-diagnostics"><summary>Diagnostics</summary><div class="item-meta">${total} jobs attributed · ${escapeHtml(latestText)} · Interval ${source.interval_minutes} minutes</div><div class="item-meta mono">${escapeHtml(source.url)}</div></details>
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
  const query = new URLSearchParams({q: $('#employer-query').value, page: employerPage, page_size: 48});
  const result = await api(`/api/employers/page?${query}`);
  if (employerPage > result.pages) { employerPage = result.pages; return loadEmployers(); }
  const employers = result.items;
  $('#employer-count').textContent = result.total ? `${result.total} employers in this result` : 'No employers found';
  $('#employer-page-summary').textContent = result.total ? `Page ${result.page} of ${result.pages}` : 'No pages';
  $('#employers-prev').disabled = employerPage <= 1;
  $('#employers-next').disabled = employerPage >= result.pages;
  $('#employer-list').innerHTML = employers.length ? employers.map((employer) =>
    `<div class="employer surface-readonly" data-employer-id="${employer.id}"><strong>${escapeHtml(employer.name)}</strong><small>${escapeHtml(employer.category)} · ${escapeHtml(employer.live_coverage)}</small>${employer.career_url ? `<small><a href="${escapeHtml(employer.career_url)}" target="_blank" rel="noopener noreferrer">Career page ↗</a></small>` : '<small>No career page configured</small>'}<button type="button" data-employer-source="${employer.id}" aria-label="${employer.career_url ? 'Edit' : 'Add'} career page for ${escapeHtml(employer.name)}">${employer.career_url ? 'Edit career page' : 'Add career page'}</button><form class="employer-career-form" data-employer-form="${employer.id}" hidden><label>Career page URL<input name="career_url" type="url" required placeholder="https://company.example/careers" value="${escapeHtml(employer.career_url || '')}"></label><p class="field-message employer-career-error" role="alert"></p><div class="actions"><button class="primary" type="submit">Save career page</button><button class="secondary" type="button" data-employer-cancel="${employer.id}">Cancel</button></div></form></div>`
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
};

function providerLabel(provider) {
  return PROVIDER_LABELS[provider] || provider || 'Not selected';
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
  };
  const missing = Object.entries(availability).filter(([, ready]) => !ready);
  $('#provider-availability').innerHTML = missing.length
    ? `<p class="hint">Unavailable options</p><ul>${missing.map(([key]) => `<li><strong>${escapeHtml(providerLabel(key))}</strong> · ${escapeHtml(reasons[key])}</li>`).join('')}</ul>`
    : '<p class="hint">All supported drafting providers are available.</p>';
}

async function loadSettings() {
  const [profile, setup] = await Promise.all([api('/api/profile'), loadSetup()]);
  await loadMatchingModels();
  const availability = {codex:setup.providers.codex, codex_local:setup.providers.codex && setup.providers.ollama,
    agy:setup.providers.agy, claude:setup.providers.claude};
  const providerForm = $('#provider-form');
  providerForm.querySelectorAll('option[value]').forEach((option) => { if (option.value) option.disabled = !availability[option.value]; });
  providerForm.elements.provider.value = profile.drafting_provider || '';
  renderProviderAvailability(availability);
  const modelCount = Number(Boolean(profile.drafting_provider)) + Number(Boolean(setup.matching.model));
  setStepStatus('#provider-status', modelCount === 2 ? 'Both configured' : modelCount ? '1 of 2 configured' : 'Choose models', modelCount === 2 ? '' : 'warning');
  setStepStatus('#drafting-status', profile.drafting_provider ? `Using ${providerLabel(profile.drafting_provider)}` : 'Choose a provider', profile.drafting_provider ? '' : 'warning');
  const hasResume = Boolean(profile.name && profile.email);
  const needsSocial = Boolean(profile.drafting_provider && hasResume && (setup.browser.sites.length || setup.browser.connected_sites.length < 2));
  closeSetupPanels(modelCount < 2 ? 'provider-panel' : needsSocial ? 'social-sign-in-panel' : null);
  return setup;
}

async function loadProfile() {
  const [profile, setup, cards] = await Promise.all([api('/api/profile'), api('/api/setup'), api('/api/evidence')]);
  const availability = {codex:setup.providers.codex, codex_local:setup.providers.codex && setup.providers.ollama,
    agy:setup.providers.agy, claude:setup.providers.claude};
  const hasResume = Boolean(profile.name && profile.email);
  $('#resume-panel-label').textContent = hasResume ? 'Your resume details' : 'Import your resume';
  setStepStatus('#resume-status', hasResume ? 'Details ready' : 'Needs details', hasResume ? '' : 'warning');
  $('#resume-panel').open = !hasResume;
  $('#resume-review-actions').hidden = !hasResume;
  $('#resume-panel-help').textContent = hasResume
    ? 'Review the structured details below, or import a newer resume to replace them.'
    : 'Upload a text-based PDF to seed your structured profile. You can review every extracted field afterward.';
  $('#pdf-resume-form button[type="submit"]').disabled = !profile.drafting_provider || !availability[profile.drafting_provider];
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
    for (const key of ['name','given_name','family_name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) form.elements[key].value = profile[key] || '';
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
    try { await showJob(button.dataset.matchingOpen, true); scrollNodeIntoView($('#job-detail'), {block:'start'}); }
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
  const nav = ['personal','experience','projects'].includes(name) ? 'profile' : name;
  document.querySelectorAll('.sidebar [data-tab]').forEach((control) => {
    const active = control.dataset.tab === nav;
    control.classList.toggle('active', active);
    if (active) control.setAttribute('aria-current', 'page');
    else control.removeAttribute('aria-current');
  });
  if (name === 'jobs' && routeInput.includes('?')) readJobsHashState();
  const title = ({home:'Home',queue:'Activity',jobs:'Jobs',applications:'Applications',profile:'My profile',settings:'Settings',
    personal:'Personal details',experience:'Work history',projects:'GitHub projects',sources:'Job sources',employers:'Employers'})[name];
  $('#page-title').textContent = title;
  document.title = `${title} · Job Radar`;
  const desiredHash = selectedDraft ? `#applications/${selectedDraft}` : name === 'jobs' ? jobsHash() : `#${name}`;
  if (historyMode === 'replace') history.replaceState({tab:name}, '', desiredHash);
  else if (historyMode === 'push' && location.hash !== desiredHash) history.pushState({tab:name}, '', desiredHash);
  window.scrollTo(0, 0);
  if (!['home', 'settings'].includes(name)) refreshSocialAuth().catch((error) => notice(error.message, true));
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
    template:'Basic template',
    'local template; no model inference':'Basic template',
    'local inference through Codex OSS':'Codex OSS · local',
    'remote inference through local Codex CLI':'Codex CLI',
    'remote inference through local Antigravity CLI':'Antigravity CLI',
    'remote inference through local Claude Code CLI':'Claude Code',
  })[value] || String(value || 'Unknown provider').replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function applicationReviewKey(draft) {
  if (draft.review_status) return draft.review_status;
  return draft.status === 'sent' ? 'sent' : 'draft';
}

function applicationReviewLabel(draft) {
  const key = typeof draft === 'string' ? draft : applicationReviewKey(draft);
  return ({
    awaiting_review:'Ready for review',
    needs_review:'Needs changes',
    sent:'Sent',
    sending:'Sending',
    regenerating:'Regenerating',
    queued:'Queued',
    failed:'Needs attention',
    draft:'Draft',
  })[key] || String(key || 'Draft').replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function applicationReviewTone(draft) {
  const key = applicationReviewKey(draft);
  if (key === 'sent') return 'success';
  if (key === 'awaiting_review') return 'info';
  if (['needs_review','queued','regenerating'].includes(key)) return 'warning';
  if (key === 'failed') return 'danger';
  return 'neutral';
}

function applicationIsSent(draft) {
  return draft.status === 'sent' || draft.review_status === 'sent';
}

function setApplicationWorkspaceView(view) {
  applicationWorkspaceView = ['drafts','automation','activity'].includes(view) ? view : 'drafts';
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
  root.querySelectorAll('[data-app-view]').forEach((button) => button.addEventListener('click', () => setApplicationWorkspaceView(button.dataset.appView)));
  $('#application-query').addEventListener('input', renderApplicationList);
  for (const selector of ['#application-review-filter','#application-company-filter','#application-sent-filter']) {
    $(selector).addEventListener('change', renderApplicationList);
  }
}

function populateApplicationCompanyFilter() {
  const select = $('#application-company-filter');
  const saved = select.value;
  const companies = [...new Set(applicationDrafts.map((draft) => draft.company).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  select.innerHTML = '<option value="">All companies</option>' + companies.map((company) =>
    `<option value="${escapeHtml(company)}">${escapeHtml(company)}</option>`).join('');
  if (companies.includes(saved)) select.value = saved;
}

function filteredApplications() {
  const query = $('#application-query').value.trim().toLowerCase();
  const review = $('#application-review-filter').value;
  const company = $('#application-company-filter').value;
  const sent = $('#application-sent-filter').value;
  return applicationDrafts.filter((draft) => {
    const searchable = `${draft.job_title || ''} ${draft.company || ''} ${providerLabel(draft.provider_mode)}`.toLowerCase();
    if (query && !searchable.includes(query)) return false;
    if (review && applicationReviewKey(draft) !== review) return false;
    if (company && draft.company !== company) return false;
    if (sent === 'sent' && !applicationIsSent(draft)) return false;
    if (sent === 'unsent' && applicationIsSent(draft)) return false;
    return true;
  });
}

function renderApplicationList() {
  const visible = filteredApplications();
  const summary = $('#application-list-summary');
  summary.textContent = applicationDrafts.length
    ? `${visible.length} of ${applicationDrafts.length} application${applicationDrafts.length === 1 ? '' : 's'} shown`
    : 'No applications prepared yet.';
  const list = $('#application-list');
  if (!applicationDrafts.length) {
    list.innerHTML = '<div class="panel empty"><p>No applications yet. Start with a job posting.</p><button id="applications-browse-jobs" class="primary">Browse jobs →</button></div>';
    $('#applications-browse-jobs')?.addEventListener('click', () => showTab('jobs'));
    return;
  }
  if (!visible.length) {
    list.innerHTML = '<div class="empty">No applications match these filters.</div>';
    return;
  }
  list.innerHTML = visible.map((draft) => {
    const selected = draft.id === activeApplicationId;
    const tone = applicationReviewTone(draft);
    return `<button type="button" class="item clickable application-card surface-action ${selected ? 'is-selected' : ''}" data-application="${draft.id}" aria-pressed="${selected ? 'true' : 'false'}">
      <div class="item-title">${escapeHtml(draft.job_title)} <span class="status-badge status-badge--${tone}">${escapeHtml(applicationReviewLabel(draft))}</span></div>
      <div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(providerLabel(draft.provider_mode))}</div>
      <div class="item-meta">Updated ${when(draft.updated_at)}${applicationIsSent(draft) ? ' · Sent' : ''}</div>
    </button>`;
  }).join('');
  list.querySelectorAll('[data-application]').forEach((node) => node.addEventListener('click', async () => {
    await showApplication(node.dataset.application);
    if (window.matchMedia('(max-width: 900px)').matches) scrollNodeIntoView($('#application-detail'), {block:'start'});
    $('#application-detail').focus({preventScroll:true});
  }));
}

async function loadApplications(selectedId = null) {
  bindApplicationWorkspace();
  await loadAutoApply();
  applicationDrafts = await api('/api/applications');
  populateApplicationCompanyFilter();
  if (selectedId) {
    activeApplicationId = selectedId;
    setApplicationWorkspaceView('drafts');
  } else if (activeApplicationId && !applicationDrafts.some((draft) => draft.id === activeApplicationId)) {
    activeApplicationId = null;
  }
  renderApplicationList();
  if (selectedId) await showApplication(selectedId);
}

async function loadAutoApply() {
  clearTimeout(window.autoApplyPoll);
  const data = await api('/api/auto-apply');
  const form = $('#auto-apply-form');
  if (!form.dataset.initialized) {
    form.elements.enabled.checked = data.enabled;
    form.elements.threshold.value = data.threshold;
    form.dataset.initialized = 'true';
  }
  $('#auto-apply-status').textContent = data.enabled ? `On · ${data.threshold}+` : 'Off';
  $('#auto-apply-status').className = statusClass(data.enabled ? 'success' : 'neutral');
  const existingButton = $('#queue-existing-drafts');
  existingButton.disabled = !data.enabled || !(data.eligible_existing || data.waiting_existing);
  $('#existing-draft-count').textContent = !data.enabled ? 'Enable and save automatic drafts first.'
    : (data.eligible_existing || data.waiting_existing) ? `${data.eligible_existing} scored match${data.eligible_existing === 1 ? '' : 'es'} at ${data.threshold}+ · ${data.waiting_existing} still being checked · ${(data.counts.queued || 0)} queued.`
    : data.highest_existing_score != null ? `No undrafted jobs score at least ${data.threshold}; the highest is ${data.highest_existing_score}. Lower the minimum and save to include them.`
    : `${data.counts.queued || 0} queued · No undrafted, scored jobs are ready.`;
  const activity = $('#auto-apply-activity');
  activity.innerHTML = data.recent.length ? `${data.recent.map((item) =>
    `<div class="item"><div class="item-title">${escapeHtml(item.title)} · ${escapeHtml(item.company)} <span class="status-badge ${item.status === 'sent' ? 'status-badge--success' : 'status-badge--warning'}">${escapeHtml(item.status.replaceAll('_', ' '))}</span></div><div class="item-meta">${item.analysis_status === 'done' && item.score != null ? `${escapeHtml(item.score)}/100 · ` : 'Checking match · '}${escapeHtml(item.detail || '')}</div><div class="actions">${item.draft_id ? `<button type="button" data-auto-draft="${item.draft_id}">Open application</button>` : `<button type="button" data-auto-job="${item.vacancy_id}">Open job</button>`}</div></div>`
  ).join('')}` : '<p class="hint">No prepared drafts yet.</p>';
  activity.querySelectorAll('[data-auto-draft]').forEach((button) => button.addEventListener('click', () => loadApplications(button.dataset.autoDraft).catch((error) => notice(error.message, true))));
  activity.querySelectorAll('[data-auto-job]').forEach((button) => button.addEventListener('click', async () => { await showTab('jobs'); await showJob(button.dataset.autoJob); }));
  if (data.enabled && $('#applications').classList.contains('active')) window.autoApplyPoll = setTimeout(() => loadAutoApply().catch((error) => notice(error.message, true)), 5000);
}

$('#auto-apply-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const pendingButton = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Saving…');
  const form = event.target;
  try {
    const result = await api('/api/auto-apply', {method:'PUT', body:JSON.stringify({enabled:form.elements.enabled.checked, threshold:Number(form.elements.threshold.value)})});
    delete form.dataset.initialized;
    await loadAutoApply();
    notice(result.enabled ? `Automatic drafts enabled for jobs scoring ${result.threshold} or higher. Every application waits for your approval.` : 'Automatic draft preparation paused.');
  } catch (error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#queue-existing-drafts').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api('/api/auto-apply/queue-existing', {method:'POST'});
    await loadAutoApply();
    notice(result.queued ? `${result.queued} existing job${result.queued === 1 ? '' : 's'} queued. Drafts will be prepared only for scores at or above the saved minimum, then sent to you for review.` : 'No existing jobs are ready to queue.');
  } catch (error) { notice(error.message, true); button.disabled = false; }
});

function applicationActionLabel(destination) {
  return ({email:'Email', web_form:'Web form', linkedin_easy_apply:'LinkedIn Easy Apply', manual:'Manual review', unknown:'Manual review'})[destination.action_type]
    || (destination.kind === 'email' ? 'Email' : destination.kind === 'web' ? 'Web form' : 'Manual review');
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

  if (field.type === 'radio' || field.type === 'checkbox') {
    const checked = ['yes','true','checked','1'].includes(answer.toLowerCase());
    const group = field.type === 'radio' ? ` name="review-radio-${escapeHtml(field.name || 'group')}"` : '';
    return `<label class="application-form-choice"><input type="${field.type}"${group} data-answer="${field.index}" data-draft-field value="yes" ${checked ? 'checked' : ''}><span>${escapeHtml(label)}${required}</span></label>`;
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

async function showApplication(id) {
  if ($('#applications').classList.contains('active') && location.hash !== `#applications/${id}`) history.replaceState({tab:'applications'}, '', `#applications/${id}`);
  setApplicationWorkspaceView('drafts');
  activeApplicationId = id;
  renderApplicationList();

  const draft = await api(`/api/applications/${id}`);
  const resume = draft.resume_data || {};
  const message = draft.message_data || {};
  const destination = draft.destination || {kind:'manual', action_type:'unknown'};
  const formData = draft.form_data || {fields:[], answers:{}, attachments:{}};
  draft.form_data = formData;
  const projects = resume.projects || [];
  const warnings = draft.warnings || [];
  const blockers = draft.send_blockers || [];
  const sent = applicationIsSent(draft);
  const reviewTone = applicationReviewTone(draft);
  const canSend = !sent && draft.send_ready && draft.review_status === 'awaiting_review';
  const canInspect = !sent && destination.kind === 'web';
  const actionTarget = applicationActionTarget(destination);
  const detail = $('#application-detail');

  detail.innerHTML = `<div class="application-review-header">
      <div><p class="eyebrow">APPLICATION REVIEW</p><h2>${escapeHtml(draft.job_title)}</h2><p class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(providerLabel(draft.provider_mode || draft.provider))}</p></div>
      <span class="status-badge status-badge--${reviewTone}">${escapeHtml(applicationReviewLabel(draft))}</span>
    </div>
    <nav class="application-review-nav" aria-label="Application review sections">
      <button type="button" data-review-target="application-review-overview" aria-current="true">Overview</button>
      <button type="button" data-review-target="application-review-resume">Resume</button>
      <button type="button" data-review-target="application-review-message">Message</button>
      <button type="button" data-review-target="application-review-form">Form</button>
      <button type="button" data-review-target="application-review-regenerate">Regenerate</button>
    </nav>

    <section id="application-review-overview" class="application-review-section">
      <h3>Overview</h3>
      ${applicationAlert('danger', 'Sending is blocked', blockers)}
      ${applicationAlert('warning', 'Review before sending', warnings)}
      <div class="application-overview-grid">
        <div class="application-status-card surface-status"><strong>Review state</strong><span>${escapeHtml(applicationReviewLabel(draft))}</span><small>Telegram: ${escapeHtml(draft.telegram_status || 'Not configured')}${draft.telegram_error ? ` · ${escapeHtml(draft.telegram_error)}` : ''}</small></div>
        <div class="application-status-card surface-status"><strong>Application action</strong><span>${escapeHtml(applicationActionLabel(destination))}</span><small>${escapeHtml(actionTarget)}</small></div>
      </div>
      <div class="application-destination surface-editable">
        <div class="section-head"><div><h4>Destination</h4><p class="hint">The detected action comes from the posting. Change it only when you have verified a different destination.</p></div></div>
        ${destination.provenance ? `<p class="hint">Detected from ${escapeHtml(destination.provenance.replace(/_/g, ' '))} · ${escapeHtml(destination.confidence || 'unknown confidence')}</p>` : ''}
        <div class="form-grid">
          <label>Channel<select id="draft-destination-kind" data-draft-field><option value="web" ${destination.kind === 'web' ? 'selected' : ''}>Web form</option><option value="email" ${destination.kind === 'email' ? 'selected' : ''}>Email</option><option value="manual" ${!['web','email'].includes(destination.kind) ? 'selected' : ''}>Manual review</option></select></label>
          <label>URL or email address<input id="draft-destination" data-draft-field value="${escapeHtml(destination.url || destination.email || '')}"></label>
        </div>
      </div>
      <p class="hint">The Telegram approval button applies only to the currently saved version of this draft.</p>
    </section>

    <section id="application-review-resume" class="application-review-section">
      <div class="section-head"><div><h3>Resume</h3><p class="hint">Review the generated PDF and the structured resume data used to build it.</p></div><a href="/api/applications/${id}/resume" target="_blank" rel="noopener noreferrer">Open PDF ↗</a></div>
      <div class="application-resume-preview"><iframe src="/api/applications/${id}/resume#view=FitH" title="Resume PDF preview" loading="lazy"></iframe></div>
      <div class="form-grid"><label>Name<input id="draft-name" data-draft-field value="${escapeHtml(resume.name || '')}"></label><label>Email<input id="draft-email" data-draft-field value="${escapeHtml(resume.email || '')}"></label><label>Phone<input id="draft-phone" data-draft-field value="${escapeHtml(resume.phone || '')}"></label><label>Links, one per line<textarea id="draft-links" data-draft-field rows="2">${escapeHtml((resume.links || []).join('\n'))}</textarea></label></div>
      <label>Professional summary<textarea id="draft-summary" data-draft-field rows="3">${escapeHtml(resume.summary || '')}</textarea></label>
      <h4>Experience</h4>${(resume.experience || []).map((item, index) => `<div class="review-subsection surface-editable"><div class="form-grid"><label>Company<input data-experience-company="${index}" data-draft-field value="${escapeHtml(item.company || '')}"></label><label>Role<input data-experience-role="${index}" data-draft-field value="${escapeHtml(item.role || '')}"></label><label>Dates<input data-experience-dates="${index}" data-draft-field value="${escapeHtml(item.dates || '')}"></label></div><label>Bullets, one per line<textarea data-experience-bullets="${index}" data-draft-field rows="4">${escapeHtml((item.bullets || []).join('\n'))}</textarea></label></div>`).join('') || '<p class="hint">No previous positions in this draft.</p>'}
      <h4>Selected projects</h4>${projects.map((project, index) => `<div class="review-subsection surface-editable"><div class="form-grid"><label>Title<input data-project-title="${index}" data-draft-field value="${escapeHtml(project.title || '')}"></label><label>Repository URL<input data-project-url="${index}" data-draft-field value="${escapeHtml(project.repository_url || '')}"></label><label>Technologies<input data-project-stack="${index}" data-draft-field value="${escapeHtml((project.tech_stack || []).join(', '))}"></label></div><label>Tailored bullets, one per line<textarea data-project-bullets="${index}" data-draft-field rows="4">${escapeHtml((project.bullets || []).join('\n'))}</textarea></label></div>`).join('') || '<p class="hint">No projects selected for this draft.</p>'}
      <h4>Education</h4>${(resume.education || []).map((item, index) => { const entry = typeof item === 'string' ? {school:item} : item; return `<div class="form-grid review-subsection surface-editable"><label>School<input data-education-school="${index}" data-draft-field value="${escapeHtml(entry.school || '')}"></label><label>Degree<input data-education-degree="${index}" data-draft-field value="${escapeHtml(entry.degree || '')}"></label><label>Dates<input data-education-dates="${index}" data-draft-field value="${escapeHtml(entry.dates || '')}"></label></div>`; }).join('') || '<p class="hint">No education in this draft.</p>'}
      <label>Achievements, one per line<textarea id="draft-achievements" data-draft-field rows="3">${escapeHtml((resume.achievements || []).join('\n'))}</textarea></label>
      <label>Skills, one per line<textarea id="draft-skills" data-draft-field rows="3">${escapeHtml((resume.skills || []).join('\n'))}</textarea></label>
      <label>Skill groups, one per line as “Group: skills”<textarea id="draft-skill-groups" data-draft-field rows="3">${escapeHtml(Object.entries(resume.skill_groups || {}).map(([group, values]) => `${group}: ${Array.isArray(values) ? values.join(', ') : values}`).join('\n'))}</textarea></label>
    </section>

    <section id="application-review-message" class="application-review-section">
      <h3>Application message</h3>
      <label>Subject<input id="draft-subject" data-draft-field value="${escapeHtml(message.subject || '')}"></label>
      <label>Body<textarea id="draft-body" data-draft-field rows="10">${escapeHtml(message.body || '')}</textarea></label>
    </section>

    <section id="application-review-form" class="application-review-section">
      <div class="section-head"><div><h3>Form answers and attachments</h3><p class="hint">${formData.action ? `Form submits to ${escapeHtml(formData.action)} (${escapeHtml(formData.method || 'GET')})` : 'Inspect a verified web form to load its fields here.'}</p></div></div>
      <div class="application-form-fields">${(formData.fields || []).map((field) => renderApplicationFormField(field, draft)).join('') || '<p class="empty">No form fields inspected yet.</p>'}</div>
    </section>

    <section id="application-review-regenerate" class="application-review-section">
      <h3>Regenerate draft</h3>
      <label>Custom instructions for regeneration<textarea id="regenerate-prompt" rows="3" placeholder="Example: emphasize production search work and shorten the opening paragraph"></textarea></label>
      <div class="actions"><button id="regenerate-draft" class="secondary" ${draft.provider === 'template' ? 'disabled' : ''}>Regenerate draft</button></div>
      ${draft.provider === 'template' ? '<p class="hint">This draft used the basic template. Create a new draft with an AI provider to regenerate it with instructions.</p>' : ''}
    </section>

    <details class="application-debug"><summary>Technical details</summary><p class="hint">Package fingerprint: <span class="mono">${escapeHtml(draft.package_hash.slice(0, 16))}</span></p></details>

    <div class="application-sticky-actions">
      <div><span id="application-dirty-state" class="status-badge status-badge--neutral">Saved</span><span id="application-outcome" class="hint" role="status" aria-live="polite"></span></div>
      <div class="actions">
        <button id="save-draft" class="secondary" disabled>Save changes</button>
        <button id="inspect-draft" class="secondary" ${canInspect ? '' : 'disabled'}>Inspect form</button>
        <button id="send-draft" class="primary" ${canSend ? '' : 'disabled'}>Approve &amp; send</button>
      </div>
    </div>`;

  detail.querySelectorAll('[data-review-target]').forEach((button) => button.addEventListener('click', () => {
    detail.querySelectorAll('[data-review-target]').forEach((item) => item.removeAttribute('aria-current'));
    button.setAttribute('aria-current', 'true');
    scrollNodeIntoView(detail.querySelector(`#${button.dataset.reviewTarget}`), {block:'start'});
  }));

  const dirtyState = $('#application-dirty-state');
  const saveButton = $('#save-draft');
  const sendButton = $('#send-draft');
  const inspectButton = $('#inspect-draft');
  const markDirty = (field) => {
    if (sent) return;
    dirtyState.textContent = 'Unsaved changes';
    dirtyState.className = 'status-badge status-badge--warning';
    saveButton.disabled = false;
    sendButton.disabled = true;
    $('#application-outcome').textContent = 'Save your changes before approving this application.';
    field.closest('label')?.classList.add('is-dirty');
    if (field.id === 'draft-destination-kind') inspectButton.disabled = field.value !== 'web';
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

  $('#regenerate-draft').addEventListener('click', async () => {
    const prompt = $('#regenerate-prompt').value.trim();
    if (!prompt) { notice('Enter custom instructions to regenerate the draft.', true); return; }
    const button = $('#regenerate-draft');
    beginPending(button, 'Regenerating…');
    try {
      await api(`/api/applications/${id}/regenerate`, {method:'POST', body:JSON.stringify({prompt})});
      await loadApplications(id);
      notice('New draft prepared for review.');
    } catch(error) {
      notice(error.message, true);
    } finally {
      if (button.isConnected) endPending(button);
    }
  });

  $('#save-draft').addEventListener('click', async (event) => {
    const button = beginPending(event.currentTarget, 'Saving…');
    try {
      await saveApplication(id, draft);
      await loadApplications(id);
    } catch(error) {
      notice(error.message, true);
    } finally {
      if (button.isConnected) endPending(button);
    }
  });

  $('#inspect-draft').addEventListener('click', async (event) => {
    const button = beginPending(event.currentTarget, 'Inspecting…');
    try {
      if (!saveButton.disabled) await saveApplication(id, draft);
      await api(`/api/applications/${id}/inspect`, {method:'POST'});
      await loadApplications(id);
      notice('Application form inspected.');
    } catch(error) {
      notice(error.message, true);
    } finally {
      if (button.isConnected) endPending(button);
    }
  });

  $('#send-draft').addEventListener('click', async (event) => {
    const approved = await confirmApplicationSend(draft);
    if (!approved) return;
    const button = beginPending(event.currentTarget, 'Sending…');
    try {
      const result = await api(`/api/applications/${id}/approve`, {method:'POST', body:JSON.stringify({package_hash:draft.package_hash})});
      await loadApplications(id);
      $('#application-outcome').textContent = `${result.status}: ${result.receipt || result.error || ''}`;
      notice(`Application outcome: ${result.status}`);
    } catch(error) {
      notice(error.message, true);
    } finally {
      if (button.isConnected) endPending(button);
    }
  });

  if (sent) {
    detail.querySelectorAll('[data-draft-field],#regenerate-draft,#save-draft,#inspect-draft,#send-draft').forEach((control) => { control.disabled = true; });
    dirtyState.textContent = 'Sent';
    dirtyState.className = 'status-badge status-badge--success';
  }
}

async function saveApplication(id, draft) {
  const lines = (value) => value.split('\n').map((x) => x.trim()).filter(Boolean);
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
  const kind = $('#draft-destination-kind').value;
  const value = $('#draft-destination').value.trim();
  const editedDestination = kind === 'email' ? {kind, email:value} : {kind, url:value};
  const originalValue = draft.destination.url || draft.destination.email || '';
  const sameDestination = kind === draft.destination.kind && value === originalValue;
  const destination = sameDestination
    ? {...draft.destination, ...editedDestination}
    : {...editedDestination, action_type:kind === 'email' ? 'email' : kind === 'web' ? 'web_form' : 'manual',
       provenance:'manual_override', confidence:'user_confirmed', evidence:'Destination edited during application review'};
  const experience = (draft.resume_data.experience || []).map((item, index) => ({...item,
    company:document.querySelector(`[data-experience-company="${index}"]`).value,
    role:document.querySelector(`[data-experience-role="${index}"]`).value,
    dates:document.querySelector(`[data-experience-dates="${index}"]`).value,
    bullets:lines(document.querySelector(`[data-experience-bullets="${index}"]`).value)}));
  const projects = (draft.resume_data.projects || []).map((project, index) => ({...project,
    title:document.querySelector(`[data-project-title="${index}"]`).value,
    repository_url:document.querySelector(`[data-project-url="${index}"]`).value,
    tech_stack:document.querySelector(`[data-project-stack="${index}"]`).value.split(',').map((x) => x.trim()).filter(Boolean),
    bullets:lines(document.querySelector(`[data-project-bullets="${index}"]`).value)}));
  const education = (draft.resume_data.education || []).map((item, index) => ({...(typeof item === 'string' ? {} : item),
    school:document.querySelector(`[data-education-school="${index}"]`).value,
    degree:document.querySelector(`[data-education-degree="${index}"]`).value,
    dates:document.querySelector(`[data-education-dates="${index}"]`).value}));
  const skill_groups = Object.fromEntries(lines($('#draft-skill-groups').value).map((line) => {
    const colon = line.indexOf(':'); return colon < 0 ? [line, ''] : [line.slice(0, colon).trim(), line.slice(colon + 1).trim()];
  }));
  const resume_data = {...draft.resume_data, name:$('#draft-name').value, email:$('#draft-email').value,
    phone:$('#draft-phone').value, links:lines($('#draft-links').value), summary:$('#draft-summary').value,
    experience, projects, education, achievements:lines($('#draft-achievements').value),
    skills:lines($('#draft-skills').value), skill_groups};
  const payload = {resume_data, message_data:{subject:$('#draft-subject').value, body:$('#draft-body').value}, form_data:{...draft.form_data, answers, attachments}, destination};
  await api(`/api/applications/${id}`, {method:'PATCH', body:JSON.stringify(payload)});
  notice('Application saved');
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
  const availability = {codex:setup.providers.codex, codex_local:setup.providers.codex && setup.providers.ollama,
    agy:setup.providers.agy, claude:setup.providers.claude};
  projectProviderReady = Boolean(profile.drafting_provider && availability[profile.drafting_provider]);
  $('#project-provider-status').textContent = projectProviderReady
    ? `Project drafts use ${providerLabel(profile.drafting_provider)}. You can change this in Settings.`
    : 'Choose an available AI provider in Settings before adding a project.';
  $('#project-provider-action').hidden = projectProviderReady;
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
  const needsOriginalClaim = !details.generated_by && (details.contribution === 'unverified' || ['pending','failed'].includes(details.generation_status));
  $('#project-editor').innerHTML = `<div class="project-editor-head"><div><p class="eyebrow">REVIEW PROJECT</p><h3>${escapeHtml(card.title)}</h3></div><span class="status-badge ${card.approved ? 'status-badge--success' : 'status-badge--neutral'}">${card.approved ? 'Included in resumes' : 'Saved only'}</span></div>
    <p class="hint">Save content changes first. Including a project in resumes is a separate choice.</p>
    ${details.generation_status === 'failed' ? `<p class="hint error-text">Project draft generation failed: ${escapeHtml(details.generation_error || 'Try generating again or write your own project bullet.')}</p>` : ''}
    ${needsOriginalClaim ? '<p class="hint">Write and save a specific bullet about your contribution before including this project in resumes.</p>' : ''}
    ${card.repository_url ? `<p class="item-meta"><a href="${escapeHtml(card.repository_url)}" target="_blank" rel="noopener noreferrer" aria-label="Open repository for ${escapeHtml(card.title)}">Open repository ↗</a> · Commit ${escapeHtml(card.commit_sha?.slice(0, 8))}</p>` : ''}
    <div class="project-fields"><label>Project title<input id="project-edit-title" value="${escapeHtml(card.title)}"></label>
      <label>Project summary<textarea id="project-edit-summary" rows="3" placeholder="What the project does">${escapeHtml(details.summary || '')}</textarea></label>
      <label>Technologies<input id="project-edit-stack" value="${escapeHtml((details.tech_stack || []).join(', '))}" placeholder="Python, React, ..."></label>
      <label>What this project demonstrates <span class="hint">One resume bullet per line</span><textarea id="project-edit-bullets" rows="7">${escapeHtml((details.bullets || [card.claim]).join('\n'))}</textarea></label></div>
    <p id="project-review-status" class="hint" role="status" aria-live="polite"></p>
    <div class="actions"><button id="project-save" class="primary">Save changes</button>
      <button id="project-approval" class="secondary">${card.approved ? 'Remove from resumes' : 'Include in resumes'}</button>
      ${card.repository_url && !card.approved ? '<button id="project-regenerate" class="secondary">Generate again</button>' : ''}
      <button id="project-delete" class="secondary danger">Delete project</button></div>`;
  const content = () => {
    const bullets = $('#project-edit-bullets').value.split('\n').map((line) => line.trim()).filter(Boolean);
    const title = $('#project-edit-title').value.trim();
    if (title.length < 2 || !bullets.length || bullets[0].length < 5) throw new Error('Add a project title and at least one specific bullet before saving.');
    if (needsOriginalClaim && (bullets[0] === card.claim || bullets[0].length < 20 || /<[^>]+>|^(project:|repository summary:|describe your contribution)/i.test(bullets[0]))) throw new Error('Replace the repository placeholder with a specific project bullet before approval.');
    return {title, claim:bullets[0], details:{...details, summary:$('#project-edit-summary').value.trim(), tech_stack:$('#project-edit-stack').value.split(',').map((item) => item.trim()).filter(Boolean), bullets}};
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
      await api(`/api/evidence/${card.id}/generate`, {method:'POST', body:'{}'});
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
$('#clock').textContent = new Date().toLocaleDateString(undefined, {weekday:'long', day:'numeric', month:'long'});
function applyJobControls() {
  jobsPage = 1; activeJob = null; activeJobPinned = false; syncJobsHash('push');
  loadJobs().catch((error) => notice(error.message, true));
}
$('#job-search').addEventListener('click', applyJobControls);
$('#job-query').addEventListener('keydown', (event) => { if (event.key === 'Enter') applyJobControls(); });
for (const selector of ['#job-state','#job-score','#job-freshness','#job-work-mode','#job-source','#job-sort']) $(selector).addEventListener('change', applyJobControls);
for (const selector of ['#job-location','#job-seniority']) $(selector).addEventListener('keydown', (event) => { if (event.key === 'Enter') applyJobControls(); });
$('#jobs-clear-filters').addEventListener('click', () => {
  for (const [key, selector] of Object.entries(JOB_FILTERS)) $(selector).value = key === 'sort' ? 'best' : '';
  applyJobControls();
});
$('#jobs-prev').addEventListener('click', () => { jobsPage = Math.max(1, jobsPage - 1); activeJob = null; activeJobPinned = false; syncJobsHash('push'); loadJobs().catch((error) => notice(error.message, true)); });
$('#jobs-next').addEventListener('click', () => { jobsPage += 1; activeJob = null; activeJobPinned = false; syncJobsHash('push'); loadJobs().catch((error) => notice(error.message, true)); });
for (const selector of ['#source-kind', '#source-status', '#source-enabled', '#source-success', '#source-sort']) {
  $(selector).addEventListener('change', () => loadSources().catch((error) => notice(error.message, true)));
}
$('#source-query').addEventListener('input', () => {
  clearTimeout(window.sourceFilterTimer);
  window.sourceFilterTimer = setTimeout(() => loadSources().catch((error) => notice(error.message, true)), 150);
});
$('#queue-refresh').addEventListener('click', async (event) => {
  const button = beginPending(event.currentTarget, 'Refreshing…');
  try { await loadQueue({reason:'manual'}); }
  catch(error) { notice(error.message, true); }
  finally { endPending(button); }
});
$('#employer-search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  employerPage = 1;
  loadEmployers().catch((error) => notice(error.message, true));
});
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
    const data = Object.fromEntries(new FormData(event.target));
    await api('/api/setup/telegram', {method:'POST', body:JSON.stringify(data)});
    event.target.elements.token.value = '';
    $('#setup-telegram-message').textContent = 'Telegram reviews configured.';
    await loadSetup(); notice('Telegram reviews configured');
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
  if (!window.confirm('Remove Telegram review settings? You will need the bot token and chat configuration to reconnect it.')) return;
  try { await api('/api/setup/telegram', {method:'DELETE'}); $('#setup-telegram-form').reset(); delete $('#setup-telegram-form').dataset.initialized; await loadSetup(); notice('Telegram reviews removed'); }
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
    for (const key of ['name','given_name','family_name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) profile[key] = form.elements[key].value.trim();
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
    await api('/api/profile/provider', {method:'PUT', body:JSON.stringify({provider})});
    await loadSettings();
    notice('Application writing provider saved.');
  } catch(error) { notice(error.message, true); }
  finally { endPending(pendingButton); }
});

$('#pdf-resume-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = beginPending(event.submitter || event.target.querySelector('button[type="submit"]'), 'Extracting…');
  $('#pdf-import-status').textContent = 'Reading the PDF and asking your selected provider to extract resume details.';
  try {
    const result = await api('/api/profile/resume/pdf', {method:'POST', body:new FormData(event.target)});
    event.target.reset();
    $('#pdf-import-status').textContent = `Extracted ${result.positions} positions, ${result.education} education entries, and ${result.achievements} achievements. Review Personal details and Work history.`;
    await showTab('personal');
    notice('Resume details extracted. Review them before applying.');
  } catch(error) { $('#pdf-import-status').textContent = error.message; notice(error.message, true); }
  finally { endPending(button); const selected = $('#provider-form').elements.provider.selectedOptions[0]; button.disabled = !selected?.value || selected.disabled; }
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
  if (!projectProviderReady) {
    $('#project-add-status').textContent = 'Choose an available AI provider in Settings first.';
    return;
  }
  if (button) button.disabled = true;
  $('#project-add-status').textContent = 'Reading the repository and drafting a project description. This can take a minute…';
  try {
    const result = await api('/api/repositories/inspect', {method:'POST', body:JSON.stringify({url})});
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

$('#project-provider-action').addEventListener('click', () => {
  showTab('settings');
  $('#provider-panel').open = true;
  scrollNodeIntoView($('#provider-panel'), {block:'start'});
});

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
