const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when = (value) => value ? new Date(value).toLocaleString() : 'Never';
let activeJob = null;

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
    ['Jobs found', c.vacancies], ['Sources enabled', c.active_sources],
    ['Applications', c.application_drafts], ['Sent', c.submissions],
  ].map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
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
    `<div class="item clickable" data-job="${job.id}"><span class="score">${job.score ?? '—'}</span><div class="item-title">${escapeHtml(job.title)}</div><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div><div class="item-meta">${escapeHtml(sourceLabel(job.source))}${job.source ? ` · Checked ${when(job.source.last_seen_at)} · <a href="${escapeHtml(job.source.url)}" target="_blank" rel="noopener noreferrer">Original posting ↗</a>` : ''}</div><div class="item-meta">First seen ${when(job.first_seen_at)} <span class="pill muted">${escapeHtml(job.state)}</span></div></div>`
  ).join('') : '<div class="empty">No jobs found. Run a scan or import a job.</div>';
  document.querySelectorAll('[data-job]').forEach((node) => node.addEventListener('click', async (event) => {
    if (event.target.closest('a')) return;
    try {
      await showJob(node.dataset.job);
      if (window.matchMedia('(max-width: 900px)').matches) $('#job-detail').scrollIntoView({behavior:'smooth', block:'start'});
    } catch(error) { notice(error.message, true); }
  }));
  if (activeJob && jobs.some((job) => job.id === activeJob)) await showJob(activeJob);
  else if (activeJob) { activeJob = null; $('#job-detail').innerHTML = '<div class="empty">Select a job to see details.</div>'; }
}

async function showJob(id) {
  activeJob = id;
  document.querySelectorAll('[data-job]').forEach((node) => node.classList.toggle('is-selected', node.dataset.job === id));
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
    ${job.apply_url ? `<p><a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener noreferrer">Application page ↗</a></p>` : ''}
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
    try { notice('Scan started'); await api(`/api/sources/${button.dataset.scan}/scan`, {method:'POST'}); await loadSources(); await loadHome(); notice('Scan complete'); }
    catch(error) { notice(error.message, true); }
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
  const projectCount = cards.filter((card) => card.kind === 'project').length;
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
  $('#application-list').innerHTML = drafts.length ? drafts.map((draft) => `<div class="item clickable" data-application="${draft.id}"><div class="item-title">${escapeHtml(draft.job_title)} <span class="pill ${draft.status === 'sent' ? '' : 'warning'}">${escapeHtml(draft.status)}</span></div><div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(draft.provider_mode)}</div><div class="item-meta">Updated ${when(draft.updated_at)}</div></div>`).join('') : '<div class="panel empty"><p>No applications yet. Start with a job posting.</p><button id="applications-browse-jobs" class="primary">Browse jobs →</button></div>';
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
    <div class="review-section"><h4>Resume</h4><p><a href="/api/applications/${id}/resume" target="_blank">Preview or download PDF ↗</a></p><label>Professional summary<textarea id="draft-summary" rows="3">${escapeHtml(resume.summary || '')}</textarea></label><p class="item-meta">Experience: ${(resume.experience || []).map((item) => escapeHtml(item.company || item.title)).join(', ') || 'None'} · Projects: ${projects.length}</p>${projects.map((project, index) => `<label>${escapeHtml(project.title)} — tailored bullets<textarea data-project-bullets="${index}" rows="4">${escapeHtml((project.bullets || []).join('\n'))}</textarea></label>`).join('')}</div>
    <div class="review-section"><h4>Application message</h4><label>Subject<input id="draft-subject" value="${escapeHtml(message.subject || '')}"></label><label>Body<textarea id="draft-body" rows="10">${escapeHtml(message.body || '')}</textarea></label></div>
    <div id="draft-form-fields" class="review-section"><h4>Form answers</h4>${draft.form_data.action ? `<p class="hint">Form submits to: ${escapeHtml(draft.form_data.action)} (${escapeHtml(draft.form_data.method)})</p>` : ''}${(draft.form_data.fields || []).filter((field) => field.type !== 'file').map((field) => `<label>${escapeHtml(field.label || field.name || `Field ${field.index}`)}${field.required ? ' *' : ''}<textarea data-answer="${field.index}" rows="2">${escapeHtml(draft.form_data.answers?.[String(field.index)] || '')}</textarea>${field.options?.length ? `<span class="hint">Options: ${field.options.map((option) => escapeHtml(option.value)).join(', ')}</span>` : ''}</label>`).join('') || '<p class="hint">No form fields inspected yet. Inspect the final application URL before sending.</p>'}</div>
    ${draft.warnings.length ? `<div class="review-section"><h4>Review notes</h4>${draft.warnings.map((warning) => `<p class="hint">${escapeHtml(warning)}</p>`).join('')}</div>` : ''}
    <div class="actions"><button id="save-draft" class="primary">Save changes</button><button id="inspect-draft">Inspect form</button><button id="send-draft" ${draft.status === 'sent' ? 'disabled' : ''}>Send application</button></div><p class="hint">Package fingerprint: <span class="mono">${escapeHtml(draft.package_hash.slice(0, 16))}</span>. Open the PDF after saving changes.</p><div id="application-outcome" class="hint"></div>`;
  $('#application-detail').querySelectorAll('input,select,textarea').forEach((field) => field.addEventListener('input', () => { $('#send-draft').disabled = true; $('#application-outcome').textContent = 'Save and review your changes before sending.'; }));
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
  const answers = {};
  document.querySelectorAll('[data-answer]').forEach((field) => { answers[field.dataset.answer] = field.value; });
  const kind = $('#draft-destination-kind').value;
  const value = $('#draft-destination').value.trim();
  const destination = kind === 'email' ? {kind, email:value} : {kind, url:value};
  const projects = (draft.resume_data.projects || []).map((project, index) => ({...project, bullets:document.querySelector(`[data-project-bullets="${index}"]`).value.split('\n').map((x) => x.trim()).filter(Boolean)}));
  const payload = {resume_data:{...draft.resume_data, summary:$('#draft-summary').value, projects}, message_data:{subject:$('#draft-subject').value, body:$('#draft-body').value}, form_data:{...draft.form_data, answers}, destination};
  await api(`/api/applications/${id}`, {method:'PATCH', body:JSON.stringify(payload)});
  notice('Application saved');
}
async function loadEvidence() {
  const cards = (await api('/api/evidence')).filter((card) => card.kind === 'project');
  const profile = await api('/api/profile');
  $('#project-provider-status').textContent = profile.drafting_provider ? `Using ${profile.drafting_provider}. Change it in Profile.` : 'Choose a drafting provider in Profile first.';
  $('#repo-form button[type="submit"]').disabled = !profile.drafting_provider;
  $('#evidence-list').innerHTML = cards.length ? cards.map((card) => `<div class="item">
    <div class="item-title">${escapeHtml(card.title)} <span class="pill ${card.approved ? '' : 'warning'}">${card.approved ? 'Approved' : 'Needs review'}</span></div>
    <div class="item-meta">${card.repository_url ? `<a href="${escapeHtml(card.repository_url)}" target="_blank" rel="noopener noreferrer">Repository ↗</a> · Commit ${escapeHtml(card.commit_sha?.slice(0, 8))}` : 'Manual project'}</div>
    <label>Project title<input data-project-title="${card.id}" value="${escapeHtml(card.title)}"></label>
    <label>Technologies<input data-project-stack="${card.id}" value="${escapeHtml((JSON.parse(card.details || '{}').tech_stack || []).join(', '))}"></label>
    <label class="full">Project bullets <span class="hint">One per line. Review contribution and outcome claims before approval.</span><textarea data-project-claims="${card.id}" rows="5">${escapeHtml((JSON.parse(card.details || '{}').bullets || [card.claim]).join('\n'))}</textarea></label>
    <div class="actions"><button data-save-evidence="${card.id}">Save project</button><button data-approve-evidence="${card.id}" data-approved="${!!card.approved}">${card.approved ? 'Revoke approval' : 'Approve project'}</button>${card.repository_url && !card.approved ? `<button data-generate-project="${card.id}">Generate again</button>` : ''}</div>
  </div>`).join('') : '<div class="empty">No selected projects yet. Choose a GitHub repository above.</div>';
  const content = (id) => { const bullets = $(`[data-project-claims="${id}"]`).value.split('\n').map((x) => x.trim()).filter(Boolean); return {title:$(`[data-project-title="${id}"]`).value.trim(), claim:bullets[0] || '', details:{tech_stack:$(`[data-project-stack="${id}"]`).value.split(',').map((x) => x.trim()).filter(Boolean), bullets}}; };
  document.querySelectorAll('[data-save-evidence]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/evidence/${button.dataset.saveEvidence}`, {method:'PATCH', body:JSON.stringify(content(button.dataset.saveEvidence))}); notice('Project saved'); await loadEvidence(); }
    catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-approve-evidence]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/evidence/${button.dataset.approveEvidence}`, {method:'PATCH', body:JSON.stringify({...content(button.dataset.approveEvidence), approved:button.dataset.approved !== 'true'})}); notice('Project updated'); await loadEvidence(); }
    catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-generate-project]').forEach((button) => button.addEventListener('click', async () => {
    try { button.disabled = true; notice('Generating project content'); await api(`/api/evidence/${button.dataset.generateProject}/generate`, {method:'POST',body:'{}'}); await loadEvidence(); notice('Project draft ready for review'); }
    catch(error) { notice(error.message, true); button.disabled = false; }
  }));
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

async function inspectSelectedRepository(url) {
  notice('Inspecting repository and drafting project content…');
  const result = await api('/api/repositories/inspect', {method:'POST', body:JSON.stringify({url})});
  await loadEvidence();
  notice(result.generation_warning ? `Repository inspected. Project writing needs attention: ${result.generation_warning}` : 'Project draft ready. Review and approve it before preparing applications.', !!result.generation_warning);
}

$('#repo-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    await inspectSelectedRepository(data.url);
    event.target.reset();
  } catch(error) { notice(error.message, true); }
});

$('#github-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const username = new FormData(event.target).get('username').trim();
    const repos = await api(`/api/github/${encodeURIComponent(username)}/repositories`);
    $('#github-repos').innerHTML = repos.length ? repos.map((repo) => `<div class="item"><div class="item-title">${escapeHtml(repo.name)} ${repo.fork ? '<span class="pill muted">Fork</span>' : ''}</div><div class="item-meta">${escapeHtml(repo.description || 'No description')} · ${escapeHtml(repo.language || 'Unknown language')}</div><div class="actions"><button data-inspect-repo="${escapeHtml(repo.url)}">Inspect</button><a href="${escapeHtml(repo.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></div></div>`).join('') : '<div class="empty">No public repositories found.</div>';
    document.querySelectorAll('[data-inspect-repo]').forEach((button) => button.addEventListener('click', async () => {
      try { await inspectSelectedRepository(button.dataset.inspectRepo); }
      catch(error) { notice(error.message, true); }
    }));
  } catch(error) { notice(error.message, true); }
});

for (const selector of ['#scan-due','#scan-all']) $(selector).addEventListener('click', async () => {
  try { const result = await api('/api/scan/due', {method:'POST'}); notice(`${result.queued} due sources queued for scanning`); }
  catch(error) { notice(error.message, true); }
});

window.addEventListener('popstate', () => showTab(location.hash.slice(1) || 'home', 'none'));
showTab(location.hash.slice(1) || 'home', 'replace');
