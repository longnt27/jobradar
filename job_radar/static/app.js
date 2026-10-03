const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when = (value) => value ? new Date(value).toLocaleString() : 'Never';
let activeJob = null;
let projectCards = [];
let discoveredRepos = [];
let selectedProjectId = null;
let projectProviderReady = false;

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

function notice(message, error = false) {
  const node = $('#notice');
  node.textContent = message;
  node.classList.toggle('error', error);
  clearTimeout(window.noticeTimeout);
  window.noticeTimeout = setTimeout(() => { node.textContent = ''; node.classList.remove('error'); }, 6000);
}

async function loadSetup() {
  clearTimeout(window.setupPoll);
  const data = await api('/api/setup');
  const badge = (selector, label, ready) => { const node = $(selector); node.textContent = label; node.className = `pill ${ready ? '' : 'warning'}`; };
  badge('#setup-smtp-status', data.smtp_configured ? 'Configured' : 'Optional', data.smtp_configured);
  badge('#setup-telegram-status', data.telegram_configured ? 'Configured' : 'Optional', data.telegram_configured);
  $('#setup-browser-start').disabled = ['opening','open'].includes(data.browser.state);
  $('#setup-browser-finish').disabled = data.browser.state !== 'open';
  $('#setup-browser-detail').textContent = data.browser.error || (data.browser.state === 'opening' ? 'Opening the sign-in window…' : data.browser.state === 'open' ? 'Sign in to both sites, then click “I’ve finished signing in”.' : data.browser.state === 'saved' ? 'Session saved. Upcoming scans will verify site access.' : 'Social scans start after you save the sign-in session.');
  const mailForm = $('#setup-smtp-form');
  if (!mailForm.dataset.initialized) {
    mailForm.elements.host.value = data.smtp_host || '';
    mailForm.elements.port.value = data.smtp_port || 587;
    mailForm.elements.user.value = data.smtp_user || '';
    mailForm.elements.from_address.value = data.smtp_from || '';
    mailForm.dataset.initialized = 'true';
  }
  const alertForm = $('#setup-telegram-form');
  if (!alertForm.dataset.initialized) {
    alertForm.elements.chat_id.value = data.telegram_chat_id || '';
    alertForm.dataset.initialized = 'true';
  }
  if (data.browser.state === 'opening' && $('#profile').classList.contains('active')) window.setupPoll = setTimeout(() => loadSetup().catch((error) => notice(error.message, true)), 1000);
  return data;
}

async function loadHome() {
  const [data, profile, setup] = await Promise.all([api('/api/status'), api('/api/profile'), api('/api/setup')]);
  const c = data.counts;
  $('#metrics').innerHTML = [
    ['Jobs found', c.vacancies], ['Company career feeds', c.career_sources_enabled],
    ['Applications', c.application_drafts], ['Sent', c.submissions],
  ].map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
  const socialSources = [
    setup.linkedin_searches ? `${setup.linkedin_searches} LinkedIn searches` : '',
    setup.facebook_groups ? `${setup.facebook_groups} Facebook groups` : '',
  ].filter(Boolean);
  $('#source-summary').textContent = socialSources.length
    ? `${socialSources.join(' and ')} ${setup.browser.last_saved_at ? 'have a saved browser session.' : 'are waiting for browser sign-in before scans can run.'}`
    : 'Company career feeds scan every four hours.';
  const hasProfile = Boolean(profile.name && profile.email);
  const steps = [
    {label:'Choose an AI provider', detail:'One choice for resume import and application drafts', done:!!profile.drafting_provider, tab:'profile'},
    {label:'Add your resume', detail:'Import a PDF or enter details yourself', done:hasProfile, tab:'profile'},
    {label:'Select your projects', detail:'Choose GitHub repositories for tailored applications', done:setup.approved_evidence > 0, tab:'projects'},
    {label:'Review live jobs', detail:`${c.vacancies} job${c.vacancies === 1 ? '' : 's'} found; check original postings`, done:false, tab:'jobs'},
  ];
  const next = steps.find((step) => !step.done) || steps[3];
  $('#home-title').textContent = hasProfile ? 'Your job search is ready to move.' : 'Let’s get your search ready.';
  $('#home-description').textContent = hasProfile ? 'Review live jobs and prepare applications from your own experience.' : 'Start with your provider and resume. Then choose projects and review jobs.';
  $('#home-primary').textContent = `${next.label} →`;
  $('#home-primary').dataset.tab = next.tab;
  $('#home-steps').innerHTML = steps.map((step) =>
    `<button class="step-row" data-home-step="${step.tab}"><span class="step-check ${step.done ? 'done' : ''}">${step.done ? '✓' : '○'}</span><span><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail)}</small></span><span class="step-arrow">→</span></button>`
  ).join('');
  document.querySelectorAll('[data-home-step]').forEach((button) => button.addEventListener('click', () => showTab(button.dataset.homeStep)));
  $('#recent-scans').innerHTML = data.recent_runs.length ? data.recent_runs.map((run) =>
    `<div class="item"><div class="item-title">${escapeHtml(run.source_name)} <span class="pill ${run.status === 'success' ? '' : 'warning'}">${escapeHtml(run.status)}</span></div><div class="item-meta">${when(run.started_at)} · ${run.observed_count} observed · ${run.new_count} new</div>${run.detail ? `<div class="item-meta">${escapeHtml(run.detail)}</div>` : ''}</div>`
  ).join('') : '<div class="empty">No scans yet. Your enabled sources will appear here.</div>';
}

async function loadJobs() {
  const query = new URLSearchParams({q: $('#job-query').value, state: $('#job-state').value});
  const jobs = await api(`/api/jobs?${query}`);
  const sourceLabel = (source) => !source ? 'Manually added' : source.kind === 'career' ? 'Company career page' : source.kind === 'linkedin' ? 'LinkedIn listing' : 'Facebook group lead';
  $('#job-list').innerHTML = jobs.length ? jobs.map((job) =>
    `<div class="item job-card"><button type="button" class="card-select" data-job="${job.id}" aria-label="Open ${escapeHtml(job.title)} at ${escapeHtml(job.company)}"><span class="score">${job.score ?? '—'}</span><div class="item-title">${escapeHtml(job.title)}</div><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div><div class="item-meta">${escapeHtml(sourceLabel(job.source))}${job.source ? ` · Checked ${when(job.source.last_seen_at)}` : ''}</div><div class="item-meta">First seen ${when(job.first_seen_at)} <span class="pill muted">${escapeHtml(job.state)}</span></div></button>${job.source ? `<a href="${escapeHtml(job.source.url)}" target="_blank" rel="noopener noreferrer">Original posting ↗</a>` : ''}</div>`
  ).join('') : '<div class="empty">No jobs found. Run a scan or import a job.</div>';
  document.querySelectorAll('[data-job]').forEach((node) => node.addEventListener('click', async () => {
    try {
      await showJob(node.dataset.job);
      if (window.matchMedia('(max-width: 900px)').matches) $('#job-detail').scrollIntoView({behavior:'smooth', block:'start'});
    } catch(error) { notice(error.message, true); }
  }));
  if (activeJob && jobs.some((job) => job.id === activeJob)) await showJob(activeJob);
  else {
    activeJob = null;
    $('#job-detail').innerHTML = `<div class="empty">${jobs.length ? 'Select a job to see details.' : 'No job matches this search.'}</div>`;
  }
}

async function showJob(id) {
  activeJob = id;
  document.querySelectorAll('[data-job]').forEach((node) => node.closest('.item').classList.toggle('is-selected', node.dataset.job === id));
  const job = await api(`/api/jobs/${id}`);
  const profile = await api('/api/profile');
  const provider = profile.drafting_provider || '';
  const links = job.observations.length ? job.observations.map((source) =>
    `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.kind)}: ${escapeHtml(source.name)}</a>`
  ).join('<br>') : '';
  const score = job.score_detail ? JSON.parse(job.score_detail) : null;
  $('#job-detail').innerHTML = `<h2>${escapeHtml(job.title)}</h2><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div>
    <div class="item-meta">${escapeHtml(job.work_mode || '')} · First seen ${when(job.first_seen_at)}</div>
    <div class="actions"><button data-state="interesting">Interesting</button><button data-state="ignored">Ignore</button></div>
    <div class="review-section"><p class="hint">Drafting provider: ${escapeHtml(provider || 'Choose one in Profile first')} · <button class="text-button" data-tab="profile">Change provider</button></p><div class="actions"><button data-prepare="${id}" class="primary" ${provider ? '' : 'disabled'}>Prepare application</button></div></div>
    ${job.apply_url ? `<p><a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener noreferrer">${job.apply_url.startsWith('mailto:') ? 'Application email ↗' : 'Application page ↗'}</a></p>` : ''}
    ${!job.apply_url ? '<p class="hint">No application form has been verified for this posting. Check the original source for its application instructions before sending.</p>' : ''}
    ${links ? `<p class="item-meta">${links}</p>` : ''}
    ${score ? `<div class="review-section"><h4>Why it matched</h4><p>${escapeHtml(score.explanation || '')}</p></div>` : ''}
    <div class="description"><h3>Original description</h3>${formatDescription(job.description)}</div>`;
  $('#job-detail').querySelector('[data-tab="profile"]').addEventListener('click', () => showTab('profile'));
  $('#job-detail').querySelectorAll('[data-state]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/jobs/${id}/state`, {method:'POST', body: JSON.stringify({state:button.dataset.state})}); notice('Job updated'); await loadJobs(); }
    catch(error) { notice(error.message, true); }
  }));
  $('#job-detail').querySelector('[data-prepare]').addEventListener('click', async () => {
    const button = $('#job-detail').querySelector('[data-prepare]');
    button.disabled = true;
    button.textContent = 'Preparing…';
    try {
      const draft = await api(`/api/jobs/${id}/prepare`, {method:'POST', body:'{}'});
      if (draft.destination.kind === 'web') {
        try { await api(`/api/applications/${draft.id}/inspect`, {method:'POST'}); }
        catch(error) { notice(`Draft ready; form inspection needs attention: ${error.message}`, true); }
      }
      showTab('applications'); await loadApplications(draft.id);
    }
    catch(error) { notice(error.message, true); }
    finally { button.disabled = false; button.textContent = 'Prepare application'; }
  });
}

async function loadSources() {
  const kind = $('#source-kind').value;
  const sources = await api(`/api/sources${kind ? `?kind=${kind}` : ''}`);
  $('#source-list').innerHTML = sources.length ? sources.map((source) =>
    `<div class="item"><div class="item-title">${escapeHtml(source.name)} <span class="pill muted">${escapeHtml(source.kind)}</span> <span class="pill ${source.last_status === 'success' ? '' : 'warning'}">${escapeHtml(source.last_status || 'not scanned')}</span></div><div class="item-meta">Every ${source.interval_minutes / 60} hours · Last success ${when(source.last_success_at)}</div><div class="actions"><button data-scan="${source.id}">Scan now</button><button data-toggle="${source.id}" data-enabled="${source.enabled}">${source.enabled ? 'Pause' : 'Enable'}</button><a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a></div></div>`
  ).join('') : '<div class="empty">No sources configured for this filter.</div>';
  document.querySelectorAll('[data-toggle]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/sources/${button.dataset.toggle}`, {method:'PATCH', body:JSON.stringify({enabled:button.dataset.enabled !== 'true'})}); await loadSources(); }
    catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-scan]').forEach((button) => button.addEventListener('click', async () => {
    try {
      button.disabled = true;
      notice('Scan started');
      const result = await api(`/api/sources/${button.dataset.scan}/scan`, {method:'POST'});
      await Promise.all([loadSources(), loadHome()]);
      const message = result.status === 'success' ? `Scan complete: ${result.observed} seen, ${result.new} new.`
        : result.status === 'empty' ? 'Scan finished: no jobs found.'
        : result.status === 'already_running' ? 'This source is already scanning.'
        : `Scan ${result.status}: ${result.error || 'Check this source before trying again.'}`;
      notice(message, ['failed', 'auth_required'].includes(result.status));
    }
    catch(error) { notice(error.message, true); }
    finally { button.disabled = false; }
  }));
}

async function loadEmployers() {
  const q = $('#employer-query').value;
  const employers = await api(`/api/employers?q=${encodeURIComponent(q)}&limit=2000`);
  $('#employer-count').textContent = `${employers.length} employers shown`;
  $('#employer-list').innerHTML = employers.map((employer) =>
    `<div class="employer"><strong>${escapeHtml(employer.name)}</strong><small>${escapeHtml(employer.category)} · ${escapeHtml(employer.live_coverage)}</small>${employer.career_url ? `<small><a href="${escapeHtml(employer.career_url)}" target="_blank" rel="noopener noreferrer">Career page ↗</a></small>` : ''}<button data-employer-source="${employer.id}">Set career page</button></div>`
  ).join('');
  document.querySelectorAll('[data-employer-source]').forEach((button) => button.addEventListener('click', async () => {
    const row = employers.find((employer) => employer.id === button.dataset.employerSource);
    const url = window.prompt(`Career page URL for ${row.name}`, row.career_url || '');
    if (!url) return;
    try { await api(`/api/employers/${row.id}`, {method:'PATCH', body:JSON.stringify({career_url:url})}); await loadEmployers(); notice('Career page added to four-hour scans'); }
    catch(error) { notice(error.message, true); }
  }));
}

async function loadProfile() {
  const [profile, setup, cards] = await Promise.all([api('/api/profile'), loadSetup(), api('/api/evidence')]);
  const availability = {codex:setup.providers.codex, codex_local:setup.providers.codex && setup.providers.ollama,
    agy:setup.providers.agy, claude:setup.providers.claude};
  const providerForm = $('#provider-form');
  providerForm.querySelectorAll('option[value]').forEach((option) => { if (option.value) option.disabled = !availability[option.value]; });
  providerForm.elements.provider.value = profile.drafting_provider || '';
  $('#provider-panel-label').textContent = profile.drafting_provider ? 'AI provider' : 'Choose your AI provider';
  $('#provider-status').textContent = profile.drafting_provider ? `Using ${profile.drafting_provider}` : 'Start here';
  $('#provider-panel').open = !profile.drafting_provider;
  const hasResume = Boolean(profile.name && profile.email);
  $('#resume-panel-label').textContent = hasResume ? 'Your resume details' : 'Import your resume';
  $('#resume-status').textContent = hasResume ? 'Replace or update' : 'PDF or manual entry';
  $('#resume-panel').open = Boolean(profile.drafting_provider && !hasResume);
  $('#pdf-resume-form button[type="submit"]').disabled = !profile.drafting_provider || !availability[profile.drafting_provider];
  const projectCount = cards.filter((card) => card.kind === 'project' && card.approved).length;
  $('#profile-summary').textContent = hasResume
    ? `${profile.name} · ${profile.email}. ${(profile.experience || []).length} previous position${profile.experience?.length === 1 ? '' : 's'} and ${projectCount} selected project${projectCount === 1 ? '' : 's'}.`
    : 'Import a resume PDF or enter your details manually. You can review and edit every field.';
  $('#profile-summary-status').textContent = hasResume ? 'Ready to review' : 'Needs details';
  $('#profile-summary-status').className = `pill ${hasResume ? '' : 'warning'}`;
  $('#position-count').textContent = `${(profile.experience || []).length} previous position${profile.experience?.length === 1 ? '' : 's'}`;
  $('#project-count').textContent = `${projectCount} selected project${projectCount === 1 ? '' : 's'}`;
  const form = $('#profile-form');
  for (const key of ['name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) form.elements[key].value = profile[key] || '';
  form.elements.alert_min_score.value = profile.alert_min_score ?? 60;
  form.elements.skills.value = (profile.skills || []).join('\n');
  form.elements.links.value = (profile.links || []).join('\n');
  form.elements.education.value = (profile.education || []).map((item) => typeof item === 'string' ? item : [item.school || '', item.degree || '', item.dates || ''].join(' | ')).join('\n');
  form.elements.achievements.value = (profile.achievements || []).join('\n');
  form.elements.skill_groups.value = Object.entries(profile.skill_groups || {}).map(([label, value]) => `${label}: ${Array.isArray(value) ? value.join(', ') : value}`).join('\n');
}

async function loadPositions() {
  const positions = await api('/api/positions');
  $('#position-list').innerHTML = positions.length ? positions.map((item) => `<div class="item" data-position="${item.id}">
    <div class="form-grid"><label>Company<input data-field="company" value="${escapeHtml(item.company)}"></label><label>Role<input data-field="role" value="${escapeHtml(item.role)}"></label><label class="full">Dates<input data-field="dates" value="${escapeHtml(item.dates)}"></label><label class="full">Work and outcomes<textarea data-field="bullets" rows="4">${escapeHtml((item.bullets || []).join('\n'))}</textarea></label></div>
    <div class="actions"><button data-save-position="${item.id}">Save position</button><button data-delete-position="${item.id}">Remove</button></div></div>`).join('') : '<div class="empty">No positions yet. Add your previous jobs above.</div>';
  document.querySelectorAll('[data-save-position]').forEach((button) => button.addEventListener('click', async () => {
    const row = button.closest('[data-position]');
    const field = (name) => row.querySelector(`[data-field="${name}"]`).value.trim();
    try { await api(`/api/positions/${button.dataset.savePosition}`, {method:'PUT', body:JSON.stringify({company:field('company'),role:field('role'),dates:field('dates'),bullets:field('bullets').split('\n').map((x) => x.trim()).filter(Boolean)})}); notice('Position saved'); await loadPositions(); }
    catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-delete-position]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/positions/${button.dataset.deletePosition}`, {method:'DELETE'}); await loadPositions(); notice('Position removed'); }
    catch(error) { notice(error.message, true); }
  }));
}

function showTab(name, historyMode = 'push') {
  if (name === 'setup') name = 'profile';
  if (name === 'overview') name = 'home';
  if (!document.getElementById(name)?.classList.contains('tab')) name = 'home';
  if (name !== 'profile') clearTimeout(window.setupPoll);
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.id === name));
  const nav = ['experience','projects'].includes(name) ? 'profile' : ['sources','employers'].includes(name) ? 'jobs' : name;
  document.querySelectorAll('.sidebar nav [data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === nav));
  $('#page-title').textContent = ({home:'Home',jobs:'Jobs',applications:'Applications',profile:'My profile',
    experience:'Work history',projects:'GitHub projects',sources:'Job sources',employers:'Employers'})[name];
  if (historyMode === 'replace') history.replaceState({tab:name}, '', `#${name}`);
  else if (historyMode === 'push' && location.hash !== `#${name}`) history.pushState({tab:name}, '', `#${name}`);
  window.scrollTo(0, 0);
  ({home:loadHome,jobs:loadJobs,applications:loadApplications,experience:loadPositions,projects:loadEvidence,sources:loadSources,employers:loadEmployers,profile:loadProfile})[name]?.().catch((error) => notice(error.message, true));
}

async function loadApplications(selectedId = null) {
  const drafts = await api('/api/applications');
  $('#application-list').innerHTML = drafts.length ? drafts.map((draft) => `<button type="button" class="item clickable application-card" data-application="${draft.id}"><div class="item-title">${escapeHtml(draft.job_title)} <span class="pill ${draft.status === 'sent' ? '' : 'warning'}">${escapeHtml(draft.status)}</span></div><div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(draft.provider_mode)}</div><div class="item-meta">Updated ${when(draft.updated_at)}</div></button>`).join('') : '<div class="panel empty"><p>No applications yet. Start with a job posting.</p><button id="applications-browse-jobs" class="primary">Browse jobs →</button></div>';
  $('#applications-browse-jobs')?.addEventListener('click', () => showTab('jobs'));
  document.querySelectorAll('[data-application]').forEach((node) => node.addEventListener('click', () => showApplication(node.dataset.application)));
  if (selectedId) await showApplication(selectedId);
}

async function showApplication(id) {
  const draft = await api(`/api/applications/${id}`);
  const resume = draft.resume_data;
  const message = draft.message_data;
  const destination = draft.destination;
  const projects = resume.projects || [];
  $('#application-detail').innerHTML = `<h2>${escapeHtml(draft.job_title)}</h2><div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(draft.provider_mode)}</div>
    <div class="review-section"><h4>Destination</h4><label>Channel<select id="draft-destination-kind"><option value="web" ${destination.kind === 'web' ? 'selected' : ''}>Web form</option><option value="email" ${destination.kind === 'email' ? 'selected' : ''}>Email</option></select></label><label>URL or email address<input id="draft-destination" value="${escapeHtml(destination.url || destination.email || '')}"></label></div>
    <div class="review-section"><h4>Resume</h4><p><a href="/api/applications/${id}/resume" target="_blank">Preview or download PDF ↗</a></p>
      <div class="form-grid"><label>Name<input id="draft-name" value="${escapeHtml(resume.name || '')}"></label><label>Email<input id="draft-email" value="${escapeHtml(resume.email || '')}"></label><label>Phone<input id="draft-phone" value="${escapeHtml(resume.phone || '')}"></label><label>Links, one per line<textarea id="draft-links" rows="2">${escapeHtml((resume.links || []).join('\n'))}</textarea></label></div>
      <label>Professional summary<textarea id="draft-summary" rows="3">${escapeHtml(resume.summary || '')}</textarea></label>
      <h4>Experience</h4>${(resume.experience || []).map((item, index) => `<div class="review-subsection"><div class="form-grid"><label>Company<input data-experience-company="${index}" value="${escapeHtml(item.company || '')}"></label><label>Role<input data-experience-role="${index}" value="${escapeHtml(item.role || '')}"></label><label>Dates<input data-experience-dates="${index}" value="${escapeHtml(item.dates || '')}"></label></div><label>Bullets, one per line<textarea data-experience-bullets="${index}" rows="4">${escapeHtml((item.bullets || []).join('\n'))}</textarea></label></div>`).join('') || '<p class="hint">No previous positions in this draft.</p>'}
      <h4>Selected projects</h4>${projects.map((project, index) => `<div class="review-subsection"><div class="form-grid"><label>Title<input data-project-title="${index}" value="${escapeHtml(project.title || '')}"></label><label>Repository URL<input data-project-url="${index}" value="${escapeHtml(project.repository_url || '')}"></label><label>Technologies<input data-project-stack="${index}" value="${escapeHtml((project.tech_stack || []).join(', '))}"></label></div><label>Tailored bullets, one per line<textarea data-project-bullets="${index}" rows="4">${escapeHtml((project.bullets || []).join('\n'))}</textarea></label></div>`).join('') || '<p class="hint">No projects selected for this draft.</p>'}
      <h4>Education</h4>${(resume.education || []).map((item, index) => { const entry = typeof item === 'string' ? {school:item} : item; return `<div class="form-grid"><label>School<input data-education-school="${index}" value="${escapeHtml(entry.school || '')}"></label><label>Degree<input data-education-degree="${index}" value="${escapeHtml(entry.degree || '')}"></label><label>Dates<input data-education-dates="${index}" value="${escapeHtml(entry.dates || '')}"></label></div>`; }).join('') || '<p class="hint">No education in this draft.</p>'}
      <label>Achievements, one per line<textarea id="draft-achievements" rows="3">${escapeHtml((resume.achievements || []).join('\n'))}</textarea></label>
      <label>Skills, one per line<textarea id="draft-skills" rows="3">${escapeHtml((resume.skills || []).join('\n'))}</textarea></label>
      <label>Skill groups, one per line as “Group: skills”<textarea id="draft-skill-groups" rows="3">${escapeHtml(Object.entries(resume.skill_groups || {}).map(([group, values]) => `${group}: ${Array.isArray(values) ? values.join(', ') : values}`).join('\n'))}</textarea></label>
    </div>
    <div class="review-section"><h4>Application message</h4><label>Subject<input id="draft-subject" value="${escapeHtml(message.subject || '')}"></label><label>Body<textarea id="draft-body" rows="10">${escapeHtml(message.body || '')}</textarea></label></div>
    <div id="draft-form-fields" class="review-section"><h4>Form answers and attachments</h4>${draft.form_data.action ? `<p class="hint">Form submits to: ${escapeHtml(draft.form_data.action)} (${escapeHtml(draft.form_data.method)})</p>` : ''}${(draft.form_data.fields || []).map((field) => field.type === 'file' ? (() => {
      const assignment = draft.form_data.attachments?.[String(field.index)] || {};
      return `<label>${escapeHtml(field.label || field.name || `File ${field.index}`)}${field.required ? ' *' : ''}<select data-attachment="${field.index}"><option value="" ${!assignment.kind ? 'selected' : ''}>Choose a file</option><option value="resume" ${assignment.kind === 'resume' ? 'selected' : ''}>Generated resume PDF</option>${!field.required ? `<option value="none" ${assignment.kind === 'none' ? 'selected' : ''}>No file</option>` : ''}${assignment.kind === 'uploaded' ? `<option value="uploaded" selected>${escapeHtml(assignment.name || 'Uploaded PDF')}</option>` : ''}</select><input type="file" accept="application/pdf,.pdf" data-attachment-file="${field.index}" aria-label="Upload PDF for ${escapeHtml(field.label || field.name || `File ${field.index}`)}"><span class="hint">Select the document to attach to this field.</span></label>`;
    })() : `<label>${escapeHtml(field.label || field.name || `Field ${field.index}`)}${field.required ? ' *' : ''}<textarea data-answer="${field.index}" rows="2">${escapeHtml(draft.form_data.answers?.[String(field.index)] || '')}</textarea>${field.options?.length ? `<span class="hint">Options: ${field.options.map((option) => escapeHtml(option.value)).join(', ')}</span>` : ''}</label>`).join('') || '<p class="hint">No form fields inspected yet. Inspect the final application URL before sending.</p>'}</div>
    ${draft.warnings.length ? `<div class="review-section"><h4>Review notes</h4>${draft.warnings.map((warning) => `<p class="hint">${escapeHtml(warning)}</p>`).join('')}</div>` : ''}
    ${draft.send_blockers?.length ? `<div class="review-section"><h4>Before sending</h4>${draft.send_blockers.map((reason) => `<p class="hint">${escapeHtml(reason)}</p>`).join('')}</div>` : ''}
    <div class="actions"><button id="save-draft" class="primary">Save changes</button><button id="inspect-draft">Inspect form</button><button id="send-draft" ${draft.send_ready ? '' : 'disabled'}>Send application</button></div><p class="hint">Package fingerprint: <span class="mono">${escapeHtml(draft.package_hash.slice(0, 16))}</span>. Open the PDF after saving changes.</p><div id="application-outcome" class="hint"></div>`;
  $('#application-detail').querySelectorAll('input,select,textarea').forEach((field) => field.addEventListener('input', () => { $('#send-draft').disabled = true; $('#application-outcome').textContent = 'Save and review your changes before sending.'; }));
  $('#application-detail').querySelectorAll('[data-attachment-file]').forEach((input) => input.addEventListener('change', () => {
    if (input.files.length) {
      const select = document.querySelector(`[data-attachment="${input.dataset.attachmentFile}"]`);
      if (!select.querySelector('[value="uploaded"]')) select.add(new Option(input.files[0].name, 'uploaded'));
      select.value = 'uploaded';
    }
  }));
  $('#save-draft').addEventListener('click', async () => {
    try { await saveApplication(id, draft); await showApplication(id); }
    catch(error) { notice(error.message, true); }
  });
  $('#inspect-draft').addEventListener('click', async () => {
    try { await saveApplication(id, draft); await api(`/api/applications/${id}/inspect`, {method:'POST'}); await showApplication(id); notice('Form fields inspected'); }
    catch(error) { notice(error.message, true); }
  });
  $('#send-draft').addEventListener('click', async () => {
    const button = $('#send-draft');
    button.disabled = true;
    button.textContent = 'Sending…';
    try { const result = await api(`/api/applications/${id}/send`, {method:'POST', body:JSON.stringify({package_hash:draft.package_hash})}); $('#application-outcome').textContent = `${result.status}: ${result.receipt || result.error || ''}`; await loadApplications(); notice(`Application outcome: ${result.status}`); }
    catch(error) { notice(error.message, true); }
    finally { button.textContent = 'Send application'; }
  });
}

async function saveApplication(id, draft) {
  const lines = (value) => value.split('\n').map((x) => x.trim()).filter(Boolean);
  const answers = {};
  document.querySelectorAll('[data-answer]').forEach((field) => { answers[field.dataset.answer] = field.value; });
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
  const destination = kind === 'email' ? {kind, email:value} : {kind, url:value};
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
function renderRepositoryResults(filter = '') {
  const target = $('#github-repos');
  if (!discoveredRepos.length) return;
  const query = filter.trim().toLowerCase();
  const visible = discoveredRepos.filter((repo) => `${repo.name} ${repo.description || ''} ${repo.language || ''}`.toLowerCase().includes(query));
  target.innerHTML = `<div class="project-results-head"><strong>Repositories</strong><span class="hint">${visible.length} of ${discoveredRepos.length}</span></div>
    <input id="repo-filter" type="search" aria-label="Filter repositories" placeholder="Filter by name or language" value="${escapeHtml(filter)}">
    <div class="project-results-list stack">${visible.length ? visible.map((repo) => {
      const saved = projectCards.find((card) => card.repository_url && normalizeRepoUrl(card.repository_url) === normalizeRepoUrl(repo.url));
      return `<div class="project-repo-row"><div><strong>${escapeHtml(repo.name)}</strong>${repo.fork ? ' <span class="pill muted">Fork</span>' : ''}
        <p class="hint">${escapeHtml(repo.description || 'No description')}${repo.language ? ` · ${escapeHtml(repo.language)}` : ''}</p></div>
        <div class="actions"><button data-add-repo="${escapeHtml(repo.url)}" ${saved ? '' : !projectProviderReady ? 'disabled' : ''}>${saved ? 'Review project' : 'Add project'}</button><a href="${escapeHtml(repo.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></div></div>`;
    }).join('') : '<div class="empty">No repositories match that filter.</div>'}</div>`;
  $('#repo-filter').addEventListener('input', (event) => {
    const caret = event.target.selectionStart;
    renderRepositoryResults(event.target.value);
    $('#repo-filter').focus();
    $('#repo-filter').setSelectionRange(caret, caret);
  });
  target.querySelectorAll('[data-add-repo]').forEach((button) => button.addEventListener('click', async () => {
    const saved = projectCards.find((card) => card.repository_url && normalizeRepoUrl(card.repository_url) === normalizeRepoUrl(button.dataset.addRepo));
    if (saved) {
      selectedProjectId = saved.id;
      await loadEvidence(saved.id);
      $('#project-editor').scrollIntoView({behavior:'smooth', block:'start'});
      return;
    }
    await inspectSelectedRepository(button.dataset.addRepo, button);
  }));
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
    ? `Project drafts use ${profile.drafting_provider}. You can change this in My profile.`
    : 'Choose an available AI provider in My profile before adding a project.';
  $('#project-provider-action').hidden = projectProviderReady;
  const readyCount = projectCards.filter((card) => card.approved).length;
  $('#selected-project-count').textContent = `${readyCount} ready for resumes`;
  $('#evidence-list').innerHTML = projectCards.length ? projectCards.map((card) => `<button class="project-list-row ${card.id === selectedProjectId ? 'is-selected' : ''}" data-open-project="${card.id}" type="button">
    <strong>${escapeHtml(card.title)}</strong><span class="pill ${card.approved ? '' : 'warning'}">${card.approved ? 'Ready for resume' : 'Needs review'}</span>
    <small>${escapeHtml(card.repository_url || 'Manual project')}</small></button>`).join('') : '<div class="empty">No projects yet. Enter your GitHub username or a repository URL above.</div>';
  document.querySelectorAll('[data-open-project]').forEach((button) => button.addEventListener('click', async () => {
    selectedProjectId = button.dataset.openProject;
    await loadEvidence(selectedProjectId);
  }));
  const card = projectCards.find((item) => item.id === selectedProjectId);
  if (!card) {
    $('#project-editor').innerHTML = '<div class="project-editor-empty">Your AI-drafted project will appear here for review.</div>';
    renderRepositoryResults($('#repo-filter')?.value || '');
    return;
  }
  const details = JSON.parse(card.details || '{}');
  const needsOriginalClaim = !details.generated_by && (details.contribution === 'unverified' || ['pending','failed'].includes(details.generation_status));
  $('#project-editor').innerHTML = `<div class="project-editor-head"><div><p class="eyebrow">REVIEW PROJECT</p><h3>${escapeHtml(card.title)}</h3></div><span class="pill ${card.approved ? '' : 'warning'}">${card.approved ? 'Ready for resume' : 'Needs review'}</span></div>
    <p class="hint">Check the generated claims against your own work. Only projects marked ready can be used in an application.</p>
    ${details.generation_status === 'failed' ? `<p class="hint error-text">Project draft generation failed: ${escapeHtml(details.generation_error || 'Try generating again or write your own project bullet.')}</p>` : ''}
    ${needsOriginalClaim ? '<p class="hint">Write a specific bullet about your contribution before adding this project to resumes.</p>' : ''}
    ${card.repository_url ? `<p class="item-meta"><a href="${escapeHtml(card.repository_url)}" target="_blank" rel="noopener noreferrer">Open repository ↗</a> · Commit ${escapeHtml(card.commit_sha?.slice(0, 8))}</p>` : ''}
    <div class="project-fields"><label>Project title<input id="project-edit-title" value="${escapeHtml(card.title)}"></label>
      <label>Project summary<textarea id="project-edit-summary" rows="3" placeholder="What the project does">${escapeHtml(details.summary || '')}</textarea></label>
      <label>Technologies<input id="project-edit-stack" value="${escapeHtml((details.tech_stack || []).join(', '))}" placeholder="Python, React, ..."></label>
      <label>What this project demonstrates <span class="hint">One resume bullet per line</span><textarea id="project-edit-bullets" rows="7">${escapeHtml((details.bullets || [card.claim]).join('\n'))}</textarea></label></div>
    <p id="project-review-status" class="hint" role="status" aria-live="polite"></p>
    <div class="actions"><button id="project-save" class="secondary">${card.approved ? 'Save changes' : 'Save draft'}</button>
      <button id="project-approval" class="${card.approved ? 'secondary' : 'primary'}" ${needsOriginalClaim ? 'disabled' : ''}>${card.approved ? 'Remove from resumes' : 'Save and use on resumes'}</button>
      ${card.repository_url && !card.approved ? '<button id="project-regenerate" class="secondary">Generate again</button>' : ''}</div>`;
  const content = () => {
    const bullets = $('#project-edit-bullets').value.split('\n').map((line) => line.trim()).filter(Boolean);
    const title = $('#project-edit-title').value.trim();
    if (title.length < 2 || !bullets.length || bullets[0].length < 5) throw new Error('Add a project title and at least one specific bullet before saving.');
    if (needsOriginalClaim && (bullets[0] === card.claim || bullets[0].length < 20 || /<[^>]+>|^(project:|repository summary:|describe your contribution)/i.test(bullets[0]))) throw new Error('Replace the repository placeholder with a specific project bullet before approval.');
    return {title, claim:bullets[0], details:{...details, summary:$('#project-edit-summary').value.trim(), tech_stack:$('#project-edit-stack').value.split(',').map((item) => item.trim()).filter(Boolean), bullets}};
  };
  const save = async (approved) => {
    const status = $('#project-review-status');
    try {
      status.textContent = 'Saving project…';
      await api(`/api/evidence/${card.id}`, {method:'PATCH', body:JSON.stringify({...content(), approved})});
      await loadEvidence(card.id);
      $('#project-review-status').textContent = approved ? 'Saved. This project can now be used in tailored resumes.' : 'Saved. This project will stay out of resumes until you approve it.';
    } catch(error) { status.textContent = error.message; notice(error.message, true); }
  };
  $('#project-save').addEventListener('click', () => save(Boolean(card.approved)));
  $('#project-edit-bullets').addEventListener('input', () => {
    if (needsOriginalClaim) {
      const first = $('#project-edit-bullets').value.split('\n')[0].trim();
      $('#project-approval').disabled = first === card.claim || first.length < 20 || /<[^>]+>|^(project:|repository summary:|describe your contribution)/i.test(first);
    }
  });
  $('#project-approval').addEventListener('click', () => save(!card.approved));
  $('#project-regenerate')?.addEventListener('click', async (event) => {
    const button = event.target;
    try {
      button.disabled = true;
      $('#project-review-status').textContent = 'Generating a new draft from the repository…';
      await api(`/api/evidence/${card.id}/generate`, {method:'POST', body:'{}'});
      await loadEvidence(card.id);
      $('#project-review-status').textContent = 'New draft ready. Review it before using it in resumes.';
    } catch(error) { await loadEvidence(card.id); $('#project-review-status').textContent = error.message; }
  });
  renderRepositoryResults($('#repo-filter')?.value || '');
}

document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => showTab(button.dataset.tab)));
$('#edit-profile-button').addEventListener('click', () => {
  $('#profile-edit-panel').open = true;
  $('#profile-edit-panel').scrollIntoView({behavior:'smooth', block:'start'});
});
$('#clock').textContent = new Date().toLocaleDateString(undefined, {weekday:'long', day:'numeric', month:'long'});
$('#job-search').addEventListener('click', () => loadJobs().catch((error) => notice(error.message, true)));
$('#job-query').addEventListener('keydown', (event) => { if (event.key === 'Enter') loadJobs().catch((error) => notice(error.message, true)); });
$('#job-state').addEventListener('change', () => loadJobs().catch((error) => notice(error.message, true)));
$('#source-kind').addEventListener('change', () => loadSources().catch((error) => notice(error.message, true)));
$('#employer-search').addEventListener('click', () => loadEmployers().catch((error) => notice(error.message, true)));

$('#setup-browser-start').addEventListener('click', async () => {
  try { await api('/api/setup/browser/start', {method:'POST'}); await loadSetup(); }
  catch(error) { notice(error.message, true); }
});

$('#setup-browser-finish').addEventListener('click', async () => {
  try { await api('/api/setup/browser/finish', {method:'POST'}); await loadSetup(); notice('Browser session saved. LinkedIn and Facebook scans are queued.'); }
  catch(error) { notice(error.message, true); }
});

$('#setup-facebook-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    await api('/api/sources', {method:'POST', body:JSON.stringify({...data, kind:'facebook'})});
    event.target.reset(); await loadSetup(); notice('Facebook group added to four-hour scans');
  } catch(error) { notice(error.message, true); }
});

$('#setup-smtp-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    data.port = Number(data.port);
    await api('/api/setup/smtp', {method:'POST', body:JSON.stringify(data)});
    event.target.elements.password.value = '';
    await loadSetup(); notice('Email settings saved');
  } catch(error) { notice(error.message, true); }
});

$('#setup-smtp-remove').addEventListener('click', async () => {
  try { await api('/api/setup/smtp', {method:'DELETE'}); $('#setup-smtp-form').reset(); delete $('#setup-smtp-form').dataset.initialized; await loadSetup(); notice('Email settings removed'); }
  catch(error) { notice(error.message, true); }
});

$('#setup-telegram-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    await api('/api/setup/telegram', {method:'POST', body:JSON.stringify(data)});
    event.target.elements.token.value = '';
    await loadSetup(); notice('Telegram alerts configured');
  } catch(error) { notice(error.message, true); }
});

$('#setup-telegram-remove').addEventListener('click', async () => {
  try { await api('/api/setup/telegram', {method:'DELETE'}); $('#setup-telegram-form').reset(); delete $('#setup-telegram-form').dataset.initialized; await loadSetup(); notice('Telegram alerts removed'); }
  catch(error) { notice(error.message, true); }
});

$('#profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const profile = await api('/api/profile');
    const form = event.target;
    for (const key of ['name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) profile[key] = form.elements[key].value.trim();
    profile.alert_min_score = Number(form.elements.alert_min_score.value || 60);
    profile.skills = form.elements.skills.value.split('\n').map((x) => x.trim()).filter(Boolean);
    profile.links = form.elements.links.value.split('\n').map((x) => x.trim()).filter(Boolean);
    profile.achievements = form.elements.achievements.value.split('\n').map((x) => x.trim()).filter(Boolean);
    profile.education = form.elements.education.value.split('\n').map((x) => x.trim()).filter(Boolean).map((line) => { const [school, degree, dates] = line.split('|').map((x) => x.trim()); return {school, degree:degree || '', dates:dates || ''}; });
    profile.skill_groups = Object.fromEntries(form.elements.skill_groups.value.split('\n').map((x) => x.trim()).filter(Boolean).map((line) => { const colon = line.indexOf(':'); return colon < 0 ? [line, ''] : [line.slice(0, colon).trim(), line.slice(colon + 1).trim()]; }));
    await api('/api/profile', {method:'PUT', body:JSON.stringify(profile)});
    await loadProfile();
    $('#profile-edit-panel').open = false;
    notice('Personal details saved');
  } catch(error) { notice(error.message, true); }
});

$('#provider-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const provider = event.target.elements.provider.value;
    await api('/api/profile/provider', {method:'PUT', body:JSON.stringify({provider})});
    await loadProfile();
    $('#resume-panel').scrollIntoView({behavior:'smooth', block:'start'});
    notice('Provider saved. Add your resume next.');
  } catch(error) { notice(error.message, true); }
});

$('#pdf-resume-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  button.textContent = 'Extracting…';
  $('#pdf-import-status').textContent = 'Reading the PDF and asking your selected provider to extract resume details.';
  try {
    const result = await api('/api/profile/resume/pdf', {method:'POST', body:new FormData(event.target)});
    event.target.reset();
    await loadProfile();
    $('#profile-edit-panel').open = true;
    $('#profile-edit-panel').scrollIntoView({behavior:'smooth', block:'start'});
    $('#pdf-import-status').textContent = `Extracted ${result.positions} positions, ${result.education} education entries, and ${result.achievements} achievements. Review your details below and positions in Experience.`;
    notice('Resume details extracted. Review them before applying.');
  } catch(error) { $('#pdf-import-status').textContent = error.message; notice(error.message, true); }
  finally { button.textContent = 'Extract resume details'; const selected = $('#provider-form').elements.provider.selectedOptions[0]; button.disabled = !selected?.value || selected.disabled; }
});

$('#latex-import-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const latex = event.target.elements.latex.value;
    const result = await api('/api/profile/import-latex', {method:'POST', body:JSON.stringify({latex})});
    event.target.reset();
    await loadProfile();
    $('#profile-edit-panel').open = true;
    $('#profile-edit-panel').scrollIntoView({behavior:'smooth', block:'start'});
    notice(`Imported ${result.positions} positions, ${result.education} education entries, and ${result.achievements} achievements`);
  } catch(error) { notice(error.message, true); }
});

$('#source-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    await api('/api/sources', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadSources(); notice('Source added');
  } catch(error) { notice(error.message, true); }
});

$('#employer-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    if (!data.career_url) data.career_url = null;
    await api('/api/employers', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadEmployers(); notice('Employer added');
  } catch(error) { notice(error.message, true); }
});

$('#job-import').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    if (!data.apply_url) data.apply_url = null;
    const result = await api('/api/jobs/import', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadJobs(); await showJob(result.id); notice('Job added');
  } catch(error) { notice(error.message, true); }
});

$('#position-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    data.bullets = data.bullets.split('\n').map((x) => x.trim()).filter(Boolean);
    await api('/api/positions', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadPositions(); notice('Position added');
  } catch(error) { notice(error.message, true); }
});

async function inspectSelectedRepository(url, button = null) {
  if (!projectProviderReady) {
    $('#project-add-status').textContent = 'Choose an available AI provider in My profile first.';
    return;
  }
  if (button) button.disabled = true;
  $('#project-add-status').textContent = 'Reading the repository and drafting a project description. This can take a minute…';
  try {
    const result = await api('/api/repositories/inspect', {method:'POST', body:JSON.stringify({url})});
    await loadEvidence(result.evidence_id);
    $('#project-add-status').textContent = result.generation_warning
      ? `Repository added, but the draft needs attention: ${result.generation_warning}`
      : 'Project draft ready. Review the claims below, then choose “Save and use on resumes”.';
    $('#project-editor').scrollIntoView({behavior:'smooth', block:'start'});
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
  showTab('profile');
  $('#provider-panel').open = true;
  $('#provider-panel').scrollIntoView({behavior:'smooth', block:'start'});
});

$('#project-add-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  try {
    const entry = parseGitHubEntry($('#project-github-input').value);
    if (entry.repository) {
      const saved = projectCards.find((card) => card.repository_url && normalizeRepoUrl(card.repository_url) === normalizeRepoUrl(entry.repository));
      if (saved) {
        await loadEvidence(saved.id);
        $('#project-add-status').textContent = 'This repository is already in your projects. Review it below.';
        $('#project-editor').scrollIntoView({behavior:'smooth', block:'start'});
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
  finally { button.disabled = false; }
});

for (const selector of ['#scan-due','#scan-all']) $(selector).addEventListener('click', async () => {
  try { const result = await api('/api/scan/due', {method:'POST'}); notice(`${result.queued} due sources queued for scanning`); }
  catch(error) { notice(error.message, true); }
});

window.addEventListener('popstate', () => showTab(location.hash.slice(1) || 'home', 'none'));
showTab(location.hash.slice(1) || 'home', 'replace');
