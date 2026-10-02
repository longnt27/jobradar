const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const when = (value) => value ? new Date(value).toLocaleString() : 'Never';
let activeJob = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
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

async function loadOverview() {
  const data = await api('/api/status');
  const c = data.counts;
  $('#metrics').innerHTML = [
    ['Jobs found', c.vacancies], ['Active sources', c.active_sources],
    ['Employers tracked', c.employers], ['Applications', c.submissions],
  ].map(([label, value]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join('');
  $('#recent-scans').innerHTML = data.recent_runs.length ? data.recent_runs.map((run) =>
    `<div class="item"><div class="item-title">${escapeHtml(run.source_name)} <span class="pill ${run.status === 'success' ? '' : 'warning'}">${escapeHtml(run.status)}</span></div><div class="item-meta">${when(run.started_at)} · ${run.observed_count} observed · ${run.new_count} new</div>${run.detail ? `<div class="item-meta">${escapeHtml(run.detail)}</div>` : ''}</div>`
  ).join('') : '<div class="empty">No scans yet. Sign in to the browser profile and run a scan.</div>';
}

async function loadJobs() {
  const query = new URLSearchParams({q: $('#job-query').value, state: $('#job-state').value});
  const jobs = await api(`/api/jobs?${query}`);
  $('#job-list').innerHTML = jobs.length ? jobs.map((job) =>
    `<div class="item clickable" data-job="${job.id}"><span class="score">${job.score ?? '—'}</span><div class="item-title">${escapeHtml(job.title)}</div><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div><div class="item-meta">First seen ${when(job.first_seen_at)} <span class="pill muted">${escapeHtml(job.state)}</span></div></div>`
  ).join('') : '<div class="empty">No jobs found. Run a scan or import a job.</div>';
  document.querySelectorAll('[data-job]').forEach((node) => node.addEventListener('click', () => showJob(node.dataset.job)));
  if (activeJob && jobs.some((job) => job.id === activeJob)) await showJob(activeJob);
}

async function showJob(id) {
  activeJob = id;
  const job = await api(`/api/jobs/${id}`);
  const links = job.observations.length ? job.observations.map((source) =>
    `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(source.kind)}: ${escapeHtml(source.name)}</a>`
  ).join('<br>') : '';
  const score = job.score_detail ? JSON.parse(job.score_detail) : null;
  $('#job-detail').innerHTML = `<h2>${escapeHtml(job.title)}</h2><div class="item-meta">${escapeHtml(job.company)} · ${escapeHtml(job.location || 'Location unknown')}</div>
    <div class="item-meta">${escapeHtml(job.work_mode || '')} · First seen ${when(job.first_seen_at)}</div>
    <div class="actions"><button data-state="interesting">Interesting</button><button data-state="ignored">Ignore</button></div>
    <div class="review-section"><label>Drafting provider<select id="draft-provider"><option value="codex">Codex CLI (remote model)</option><option value="agy">Antigravity CLI (remote model)</option><option value="claude">Claude Code CLI (remote model)</option><option value="codex_local">Codex OSS + local Ollama model</option><option value="template">Local template (no AI)</option></select></label><div class="actions"><button data-prepare="${id}" class="primary">Prepare application</button></div></div>
    ${job.apply_url ? `<p><a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener noreferrer">Application page ↗</a></p>` : ''}
    ${links ? `<p class="item-meta">${links}</p>` : ''}
    ${score ? `<div class="review-section"><h4>Why it matched</h4><p>${escapeHtml(score.explanation || '')}</p></div>` : ''}
    <div class="description">${escapeHtml(job.description)}</div>`;
  $('#job-detail').querySelectorAll('[data-state]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/jobs/${id}/state`, {method:'POST', body: JSON.stringify({state:button.dataset.state})}); notice('Job updated'); await loadJobs(); }
    catch(error) { notice(error.message, true); }
  }));
  $('#job-detail').querySelector('[data-prepare]').addEventListener('click', async () => {
    try {
      const draft = await api(`/api/jobs/${id}/prepare`, {method:'POST', body:JSON.stringify({provider:$('#draft-provider').value})});
      if (draft.destination.kind === 'web') {
        try { await api(`/api/applications/${draft.id}/inspect`, {method:'POST'}); }
        catch(error) { notice(`Draft ready; form inspection needs attention: ${error.message}`, true); }
      }
      showTab('applications'); await loadApplications(draft.id);
    }
    catch(error) { notice(error.message, true); }
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
    try { notice('Scan started'); await api(`/api/sources/${button.dataset.scan}/scan`, {method:'POST'}); await loadSources(); await loadOverview(); notice('Scan complete'); }
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
  const profile = await api('/api/profile');
  const form = $('#profile-form');
  for (const key of ['name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) form.elements[key].value = profile[key] || '';
  form.elements.alert_min_score.value = profile.alert_min_score ?? 60;
  form.elements.skills.value = (profile.skills || []).join('\n');
  form.elements.links.value = (profile.links || []).join('\n');
}

function showTab(name) {
  document.querySelectorAll('.tab').forEach((tab) => tab.classList.toggle('active', tab.id === name));
  document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === name));
  $('#page-title').textContent = name[0].toUpperCase() + name.slice(1);
  history.replaceState(null, '', `#${name}`);
  ({overview:loadOverview,jobs:loadJobs,applications:loadApplications,evidence:loadEvidence,sources:loadSources,employers:loadEmployers,profile:loadProfile})[name]?.().catch((error) => notice(error.message, true));
}

async function loadApplications(selectedId = null) {
  const drafts = await api('/api/applications');
  $('#application-list').innerHTML = drafts.length ? drafts.map((draft) => `<div class="item clickable" data-application="${draft.id}"><div class="item-title">${escapeHtml(draft.job_title)} <span class="pill ${draft.status === 'sent' ? '' : 'warning'}">${escapeHtml(draft.status)}</span></div><div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(draft.provider_mode)}</div><div class="item-meta">Updated ${when(draft.updated_at)}</div></div>`).join('') : '<div class="empty">No applications yet. Open a job and prepare one.</div>';
  document.querySelectorAll('[data-application]').forEach((node) => node.addEventListener('click', () => showApplication(node.dataset.application)));
  if (selectedId) await showApplication(selectedId);
}

async function showApplication(id) {
  const draft = await api(`/api/applications/${id}`);
  const resume = draft.resume_data;
  const message = draft.message_data;
  const destination = draft.destination;
  const cards = resume.evidence || [];
  $('#application-detail').innerHTML = `<h2>${escapeHtml(draft.job_title)}</h2><div class="item-meta">${escapeHtml(draft.company)} · ${escapeHtml(draft.provider_mode)}</div>
    <div class="review-section"><h4>Destination</h4><label>Channel<select id="draft-destination-kind"><option value="web" ${destination.kind === 'web' ? 'selected' : ''}>Web form</option><option value="email" ${destination.kind === 'email' ? 'selected' : ''}>Email</option></select></label><label>URL or email address<input id="draft-destination" value="${escapeHtml(destination.url || destination.email || '')}"></label></div>
    <div class="review-section"><h4>Resume</h4><p><a href="/api/applications/${id}/resume" target="_blank">Preview or download PDF ↗</a></p><label>Professional summary<textarea id="draft-summary" rows="3">${escapeHtml(resume.summary || '')}</textarea></label><p class="item-meta">Selected evidence: ${cards.map((card) => escapeHtml(card.title)).join(', ')}</p></div>
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
    try { const result = await api(`/api/applications/${id}/send`, {method:'POST', body:JSON.stringify({package_hash:draft.package_hash})}); $('#application-outcome').textContent = `${result.status}: ${result.receipt || result.error || ''}`; await loadApplications(); notice(`Application outcome: ${result.status}`); }
    catch(error) { notice(error.message, true); }
  });
}

async function saveApplication(id, draft) {
  const answers = {};
  document.querySelectorAll('[data-answer]').forEach((field) => { answers[field.dataset.answer] = field.value; });
  const kind = $('#draft-destination-kind').value;
  const value = $('#draft-destination').value.trim();
  const destination = kind === 'email' ? {kind, email:value} : {kind, url:value};
  const payload = {resume_data:{...draft.resume_data, summary:$('#draft-summary').value}, message_data:{subject:$('#draft-subject').value, body:$('#draft-body').value}, form_data:{...draft.form_data, answers}, destination};
  await api(`/api/applications/${id}`, {method:'PATCH', body:JSON.stringify(payload)});
  notice('Application saved');
}
async function loadEvidence() {
  const cards = await api('/api/evidence');
  $('#evidence-list').innerHTML = cards.length ? cards.map((card) => `<div class="item">
    <div class="item-title">${escapeHtml(card.title)} <span class="pill ${card.approved ? '' : 'warning'}">${card.approved ? 'Approved' : 'Needs review'}</span></div>
    <div class="item-meta">${escapeHtml(card.kind)} ${card.repository_url ? `· <a href="${escapeHtml(card.repository_url)}" target="_blank" rel="noopener noreferrer">Repository ↗</a>` : ''}</div>
    <label class="full">Claim<textarea data-claim="${card.id}" rows="3">${escapeHtml(card.claim)}</textarea></label>
    <div class="actions"><button data-save-evidence="${card.id}">Save wording</button><button data-approve-evidence="${card.id}" data-approved="${!!card.approved}">${card.approved ? 'Revoke approval' : 'Approve claim'}</button></div>
  </div>`).join('') : '<div class="empty">No evidence yet. Add experience or inspect a repository.</div>';
  document.querySelectorAll('[data-save-evidence]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/evidence/${button.dataset.saveEvidence}`, {method:'PATCH', body:JSON.stringify({claim:$(`[data-claim="${button.dataset.saveEvidence}"]`).value})}); notice('Claim saved'); await loadEvidence(); }
    catch(error) { notice(error.message, true); }
  }));
  document.querySelectorAll('[data-approve-evidence]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/evidence/${button.dataset.approveEvidence}`, {method:'PATCH', body:JSON.stringify({approved:button.dataset.approved !== 'true', claim:$(`[data-claim="${button.dataset.approveEvidence}"]`).value})}); notice('Evidence updated'); await loadEvidence(); }
    catch(error) { notice(error.message, true); }
  }));
}

document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => showTab(button.dataset.tab)));
$('#clock').textContent = new Date().toLocaleDateString(undefined, {weekday:'long', day:'numeric', month:'long'});
$('#job-search').addEventListener('click', () => loadJobs().catch((error) => notice(error.message, true)));
$('#job-query').addEventListener('keydown', (event) => { if (event.key === 'Enter') loadJobs().catch((error) => notice(error.message, true)); });
$('#job-state').addEventListener('change', () => loadJobs().catch((error) => notice(error.message, true)));
$('#source-kind').addEventListener('change', () => loadSources().catch((error) => notice(error.message, true)));
$('#employer-search').addEventListener('click', () => loadEmployers().catch((error) => notice(error.message, true)));

$('#profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const profile = await api('/api/profile');
    const form = event.target;
    for (const key of ['name','email','phone','location','summary','salary_expectation','work_authorization','notice_period','relocation']) profile[key] = form.elements[key].value.trim();
    profile.alert_min_score = Number(form.elements.alert_min_score.value || 60);
    profile.skills = form.elements.skills.value.split('\n').map((x) => x.trim()).filter(Boolean);
    profile.links = form.elements.links.value.split('\n').map((x) => x.trim()).filter(Boolean);
    await api('/api/profile', {method:'PUT', body:JSON.stringify(profile)});
    notice('Profile saved');
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

$('#evidence-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    data.approved = true;
    await api('/api/evidence', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadEvidence(); notice('Experience saved');
  } catch(error) { notice(error.message, true); }
});

$('#repo-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = Object.fromEntries(new FormData(event.target));
    notice('Inspecting repository');
    await api('/api/repositories/inspect', {method:'POST', body:JSON.stringify(data)});
    event.target.reset(); await loadEvidence(); notice('Repository inspected. Review and approve its claim.');
  } catch(error) { notice(error.message, true); }
});

$('#github-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const username = new FormData(event.target).get('username').trim();
    const repos = await api(`/api/github/${encodeURIComponent(username)}/repositories`);
    $('#github-repos').innerHTML = repos.length ? repos.map((repo) => `<div class="item"><div class="item-title">${escapeHtml(repo.name)} ${repo.fork ? '<span class="pill muted">Fork</span>' : ''}</div><div class="item-meta">${escapeHtml(repo.description || 'No description')} · ${escapeHtml(repo.language || 'Unknown language')}</div><div class="actions"><button data-inspect-repo="${escapeHtml(repo.url)}">Inspect</button><a href="${escapeHtml(repo.url)}" target="_blank" rel="noopener noreferrer">Open ↗</a></div></div>`).join('') : '<div class="empty">No public repositories found.</div>';
    document.querySelectorAll('[data-inspect-repo]').forEach((button) => button.addEventListener('click', async () => {
      try { notice('Inspecting repository'); await api('/api/repositories/inspect', {method:'POST', body:JSON.stringify({url:button.dataset.inspectRepo})}); await loadEvidence(); notice('Repository inspected. Review its claim before approval.'); }
      catch(error) { notice(error.message, true); }
    }));
  } catch(error) { notice(error.message, true); }
});

for (const selector of ['#scan-due','#scan-all']) $(selector).addEventListener('click', async () => {
  try { const result = await api('/api/scan/due', {method:'POST'}); notice(`${result.queued} due sources queued for scanning`); }
  catch(error) { notice(error.message, true); }
});

showTab(location.hash.slice(1) || 'overview');
