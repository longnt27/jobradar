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
    `<div class="item"><div class="item-title">${escapeHtml(run.source_name)} <span class="pill ${run.status === 'success' ? '' : 'warning'}">${escapeHtml(run.status)}</span></div><div class="item-meta">${when(run.started_at)} · ${run.observed_count} observed · ${run.new_count} new</div></div>`
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
    <div class="actions"><button data-state="interesting">Interesting</button><button data-state="ignored">Ignore</button><button data-prepare="${id}" class="primary">Prepare application</button></div>
    ${job.apply_url ? `<p><a href="${escapeHtml(job.apply_url)}" target="_blank" rel="noopener noreferrer">Application page ↗</a></p>` : ''}
    ${links ? `<p class="item-meta">${links}</p>` : ''}
    ${score ? `<div class="review-section"><h4>Why it matched</h4><p>${escapeHtml(score.explanation || '')}</p></div>` : ''}
    <div class="description">${escapeHtml(job.description)}</div>`;
  $('#job-detail').querySelectorAll('[data-state]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/jobs/${id}/state`, {method:'POST', body: JSON.stringify({state:button.dataset.state})}); notice('Job updated'); await loadJobs(); }
    catch(error) { notice(error.message, true); }
  }));
  $('#job-detail').querySelector('[data-prepare]').addEventListener('click', async () => {
    try { const draft = await api(`/api/jobs/${id}/prepare`, {method:'POST'}); notice('Application draft ready for review'); showTab('applications'); await loadApplications(draft.id); }
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
    `<div class="employer"><strong>${escapeHtml(employer.name)}</strong><small>${escapeHtml(employer.category)} · ${escapeHtml(employer.coverage_status)}</small></div>`
  ).join('');
}

async function loadProfile() {
  const profile = await api('/api/profile');
  const form = $('#profile-form');
  for (const key of ['name','email','phone','location','summary']) form.elements[key].value = profile[key] || '';
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

async function loadApplications() { $('#application-list').innerHTML = '<div class="empty">Application drafting is loading.</div>'; }
async function loadEvidence() { $('#evidence-list').innerHTML = '<div class="empty">Evidence intake is loading.</div>'; }

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
    for (const key of ['name','email','phone','location','summary']) profile[key] = form.elements[key].value.trim();
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

for (const selector of ['#scan-due','#scan-all']) $(selector).addEventListener('click', async () => {
  try { notice('Scanning due sources'); await api('/api/scan/due', {method:'POST'}); await loadOverview(); if ($('#sources').classList.contains('active')) await loadSources(); notice('Due-source scan complete'); }
  catch(error) { notice(error.message, true); }
});

showTab(location.hash.slice(1) || 'overview');
