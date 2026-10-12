/* First-contact Zalo inquiry review, independent from application drafts. */
(() => {
  const jobs = new Map();
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const endpoint = id => `/api/zalo-inquiries/${encodeURIComponent(id)}`;
  const fields = draft => ({phone:draft.phone, position:draft.position, message:draft.message});

  function accept(state, draft) {
    state.activeId = draft.id;
    state.values = fields(draft);
    state.dirty = false;
    const index = state.context.drafts.findIndex(item => item.id === draft.id);
    if (index < 0) state.context.drafts.unshift(draft); else state.context.drafts[index] = draft;
  }

  async function save(state, skipMessage = false) {
    if (!state.dirty) return;
    const payload = {...state.values};
    if (skipMessage) delete payload.message;
    accept(state, await state.api(endpoint(state.activeId), {method:'PATCH', body:JSON.stringify(payload)}));
  }

  async function perform(state, action) {
    if (state.busy) return;
    state.busy = true;
    state.error = '';
    render(state);
    try { await action(); }
    catch (error) { state.error = error.message; state.notice(error.message, true); }
    finally { state.busy = false; render(state); }
  }

  async function prepare(state, observationId, phone) {
    const existing = state.context.drafts.find(draft => draft.observation_id === observationId && draft.phone === phone);
    if (existing) { accept(state, existing); return; }
    const draft = await state.api(`/api/jobs/${state.vacancyId}/zalo`, {
      method:'POST', body:JSON.stringify({observation_id:observationId, phone}),
    });
    accept(state, draft);
  }

  function render(state) {
    const root = state.root;
    if (!root?.isConnected || !state.context) return;
    const context = state.context;
    if (!context.observations.length) { root.hidden = true; return; }
    root.hidden = false;
    const draft = context.drafts.find(item => item.id === state.activeId);
    const values = state.values || (draft ? fields(draft) : {});
    const attempt = draft?.latest_attempt;
    const sent = attempt?.status === 'sent_confirmed';
    const uncertain = attempt?.status === 'send_uncertain';
    const editable = draft?.editable !== false;
    const blocked = state.busy || !editable;
    const sourceOptions = context.observations.map(row => `<option value="${esc(row.observation_id)}" ${state.observationId === row.observation_id ? 'selected' : ''}>${esc(row.group)} · ${esc(row.position)}</option>`).join('');
    const candidates = context.candidates.map((row, index) => `<button type="button" class="secondary zalo-candidate" data-zalo-candidate="${index}" ${state.busy ? 'disabled' : ''}><strong>${esc(row.phone)}</strong><span>${esc(row.group)} · ${esc(row.position)}</span></button>`).join('');
    const blockedReasons = draft?.blockers || [];
    const canApprove = draft?.send_ready && !state.dirty && !blocked;
    root.innerHTML = `<section class="review-section zalo-contact-section" aria-label="Zalo inquiry">
      <div class="section-head"><div><h3>Contact via Zalo</h3><p class="hint">Ask for the JD and whether you can send your CV in this chat. Review and approve the exact message first.</p></div></div>
      ${!context.connected ? '<button type="button" class="secondary" data-zalo-action="connect">Connect Zalo</button>' : ''}
      ${candidates ? `<div class="zalo-candidates">${candidates}</div>` : '<p class="hint">No explicit Zalo contact was detected. Enter a number from the original post below.</p>'}
      <details class="zalo-manual"><summary>Choose a post and phone number</summary><div class="form-grid">
        <label>Facebook post<select data-zalo-source aria-label="Facebook post"><option value="">Choose a post</option>${sourceOptions}</select></label>
        <label>Phone number from the post<input type="tel" data-zalo-manual-phone value="${esc(state.manualPhone || '')}"></label>
        <button type="button" class="secondary" data-zalo-action="create" ${state.busy ? 'disabled' : ''}>Prepare inquiry</button>
      </div></details>
      ${context.drafts.length > 1 ? `<label>Saved inquiries<select data-zalo-draft>${context.drafts.map(item=>`<option value="${esc(item.id)}" ${item.id === state.activeId ? 'selected' : ''}>${esc(item.phone)} · ${esc(item.group)}</option>`).join('')}</select></label>` : ''}
      ${draft ? `<div class="zalo-inquiry-review">
        <div class="zalo-source"><a href="${esc(draft.post_url)}" target="_blank" rel="noopener noreferrer">${esc(draft.group)} · Original Facebook post ↗</a><blockquote>${esc(draft.evidence)}</blockquote></div>
        <div class="form-grid">
          <label>Zalo phone number<input type="tel" data-zalo-field="phone" value="${esc(values.phone)}" ${blocked ? 'disabled' : ''}></label>
          <label>Position in your message<input data-zalo-field="position" value="${esc(values.position)}" ${blocked ? 'disabled' : ''}></label>
        </div>
        <p class="hint">Correct the position if extraction looks wrong. Regenerate from template to put the corrected position into the text, or edit the message yourself.</p>
        <label for="zalo-message-${esc(draft.id)}">Message to send</label><textarea id="zalo-message-${esc(draft.id)}" rows="6" data-zalo-message data-zalo-field="message" ${blocked ? 'disabled' : ''}>${esc(values.message)}</textarea>
        <div class="actions"><button type="button" class="secondary" data-zalo-action="save" ${blocked || !state.dirty ? 'disabled' : ''}>Save inquiry</button><button type="button" class="secondary" data-zalo-action="regenerate" ${blocked ? 'disabled' : ''}>Regenerate from template</button><button type="button" class="secondary" data-zalo-action="lookup" ${blocked ? 'disabled' : ''}>Look up Zalo recipient</button></div>
        <div class="surface-status zalo-recipient"><strong>${draft.recipient?.display_name ? esc(`${draft.recipient.display_name} · ${draft.phone}`) : 'Recipient not verified yet'}</strong><p class="hint">${esc(draft.recipient?.evidence || 'Look up the number to check who will receive this message.')}</p></div>
        ${state.dirty ? '<p class="hint" role="status">Save your edits before approving.</p>' : ''}
        ${blockedReasons.length && !sent ? `<ul class="hint">${blockedReasons.map(reason=>`<li>${esc(reason)}</li>`).join('')}</ul>` : ''}
        ${sent ? '<p class="status-badge status-badge--success">Contacted, awaiting reply</p><p class="hint">You can prepare an application after the recruiter responds.</p>' : ''}
        ${attempt?.error ? `<p class="hint" role="status">${esc(attempt.error)}</p>` : ''}
        ${uncertain ? '<div class="actions"><button type="button" class="secondary" data-zalo-action="resolve-sent">I checked: message was sent</button><button type="button" class="secondary" data-zalo-action="resolve-not-sent">I checked: message was not sent</button></div>' : ''}
        <div class="actions"><button type="button" class="primary" data-zalo-action="approve" ${canApprove ? '' : 'disabled'}>Approve and send Zalo inquiry</button><a class="button-link secondary" href="https://chat.zalo.me/" target="_blank" rel="noopener noreferrer">Open Zalo Web ↗</a></div>
      </div>` : ''}
      ${state.busy ? '<p class="hint" role="status" aria-live="polite">Working on your inquiry…</p>' : ''}
      ${state.error ? `<p class="error" role="alert">${esc(state.error)}</p>` : ''}
    </section>`;

    root.querySelectorAll('[data-zalo-candidate]').forEach(button => button.addEventListener('click', () => {
      const candidate = context.candidates[Number(button.dataset.zaloCandidate)];
      perform(state, () => prepare(state, candidate.observation_id, candidate.phone));
    }));
    root.querySelector('[data-zalo-source]')?.addEventListener('change', event => { state.observationId = event.target.value; });
    root.querySelector('[data-zalo-manual-phone]')?.addEventListener('input', event => { state.manualPhone = event.target.value; });
    root.querySelector('[data-zalo-draft]')?.addEventListener('change', event => {
      if (state.dirty) { event.target.value = state.activeId; state.notice('Save your message edits before choosing another inquiry.', true); return; }
      accept(state, context.drafts.find(item=>item.id === event.target.value)); render(state);
    });
    root.querySelectorAll('[data-zalo-field]').forEach(field => field.addEventListener('input', () => {
      state.values[field.dataset.zaloField] = field.value;
      state.dirty = true;
      root.querySelector('[data-zalo-action=approve]').disabled = true;
      root.querySelector('[data-zalo-action=save]').disabled = blocked;
    }));
    root.querySelectorAll('[data-zalo-action]').forEach(button => button.addEventListener('click', () => perform(state, async () => {
      const action = button.dataset.zaloAction;
      if (action === 'connect') {
        await state.api('/api/setup/browser/start', {method:'POST', body:JSON.stringify({site:'zalo'})});
        state.notice('Sign in to Zalo in the Chrome window, then look up your contact.');
      } else if (action === 'create') {
        if (!state.observationId || !state.manualPhone) throw new Error('Choose a Facebook post and enter its Zalo phone number.');
        await prepare(state, state.observationId, state.manualPhone);
      } else if (action === 'save') {
        await save(state); state.notice('Zalo inquiry saved');
      } else if (action === 'regenerate') {
        await save(state, true);
        accept(state, await state.api(`${endpoint(state.activeId)}/regenerate`, {method:'POST'}));
      } else if (action === 'lookup') {
        await save(state);
        const result = await state.api(`${endpoint(state.activeId)}/lookup`, {method:'POST'});
        accept(state, result.draft);
        if (result.lookup.status !== 'ready') state.error = result.lookup.detail || result.lookup.error;
        else context.connected = true;
      } else if (action === 'approve') {
        if (state.dirty || !draft.send_ready) throw new Error('Save and review your current message and recipient before approving.');
        const result = await state.api(`${endpoint(state.activeId)}/approve`, {method:'POST', body:JSON.stringify({package_hash:draft.package_hash})});
        accept(state, result.draft);
        state.notice(result.attempt.status === 'sent_confirmed' ? 'Zalo inquiry sent. Awaiting reply.' : result.attempt.error || 'Check the Zalo send outcome.', result.attempt.status !== 'sent_confirmed');
      } else if (action.startsWith('resolve-')) {
        const result = await state.api(`${endpoint(state.activeId)}/attempts/${attempt.id}/resolve`, {method:'POST', body:JSON.stringify({sent:action === 'resolve-sent'})});
        accept(state, result.draft);
      }
    })));
  }

  async function mountJob(root, vacancyId, {api, notice}) {
    if (!root) return;
    let state = jobs.get(vacancyId);
    if (!state) { state = {vacancyId, dirty:false, busy:false, context:null}; jobs.set(vacancyId, state); }
    Object.assign(state, {root, api, notice});
    render(state);
    try {
      const context = await api(`/api/jobs/${vacancyId}/zalo`);
      if (state.root !== root || !root.isConnected) return;
      state.context = context;
      if (!state.observationId && context.observations.length === 1) state.observationId = context.observations[0].observation_id;
      const active = context.drafts.find(item=>item.id === state.activeId) || context.drafts[0];
      if (active && !state.dirty && !state.busy) accept(state, active);
      render(state);
    } catch (error) { if (root.isConnected) { root.textContent = error.message; notice(error.message, true); } }
  }

  let templateDirty = false;
  let templateLoading = false;
  async function mountSettings(root, {api, notice}) {
    if (!root) return;
    if (!root.dataset.bound) {
      root.dataset.bound = 'true';
      root.innerHTML = '<form class="form-grid"><label class="full">Default Zalo inquiry message<textarea rows="6" data-zalo-template></textarea></label><p class="hint full">Use {position} and {group}. The fixed text uses no AI. Changes apply to new inquiries; each message still waits for your approval.</p><button type="submit" class="primary">Save Zalo template</button></form>';
      root.querySelector('textarea').addEventListener('input', () => { templateDirty = true; });
      root.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault(); const button = event.submitter; button.disabled = true;
        try {
          const result = await api('/api/setup/zalo-template', {method:'PUT', body:JSON.stringify({template:root.querySelector('textarea').value})});
          root.querySelector('textarea').value = result.template; templateDirty = false; notice('Zalo message template saved');
        } catch(error) { notice(error.message, true); }
        finally { button.disabled = false; }
      });
    }
    if (templateDirty || templateLoading) return;
    templateLoading = true;
    try { const result = await api('/api/setup/zalo-template'); if (!templateDirty) root.querySelector('textarea').value = result.template; }
    catch(error) { notice(error.message, true); }
    finally { templateLoading = false; }
  }
  window.JobRadarZalo = {mountJob, mountSettings};
})();
