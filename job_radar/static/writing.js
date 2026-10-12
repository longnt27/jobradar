(() => {
  const drafts = new Map();
  let entries = [];
  let selected = 'application_draft';
  let loading = false;

  function mountEditor(root, {api, notice}) {
    if (!root.dataset.bound) {
      root.dataset.bound = 'true';
      root.innerHTML = `<form class="writing-editor">
        <label for="writing-template-choice">Prompt or template</label>
        <select id="writing-template-choice"></select>
        <p id="writing-template-help" class="hint"></p>
        <p id="writing-template-placeholders" class="hint"></p>
        <label id="writing-value-label" for="writing-text">Instructions</label>
        <textarea id="writing-text" rows="14" spellcheck="false" aria-describedby="writing-template-help writing-template-placeholders"></textarea>
        <input id="writing-number" type="number" hidden aria-labelledby="writing-value-label" aria-describedby="writing-template-help">
        <div class="actions"><button type="submit" class="primary">Save customization</button><button id="writing-reset" type="button" class="secondary">Restore default</button><button id="writing-discard" type="button" class="text-button">Discard edits</button></div>
        <p id="writing-status" class="hint" role="status" aria-live="polite"></p>
        <details class="inline-details"><summary>View original default</summary><pre id="writing-default"></pre></details>
      </form>`;
      root.querySelector('select').addEventListener('change', event => {
        selected = event.target.value;
        render(root);
      });
      for (const id of ['writing-text', 'writing-number']) {
        root.querySelector('#' + id).addEventListener('input', event => {
          const entry = entries.find(item => item.key === selected);
          const value = typeof entry.default === 'number' ? Number(event.target.value) : event.target.value;
          if (value === entry.value) drafts.delete(selected);
          else drafts.set(selected, value);
          renderStatus(root, entry);
        });
      }
      root.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        const entry = entries.find(item => item.key === selected);
        const value = drafts.has(selected) ? drafts.get(selected) : entry.value;
        await update(root, api, notice, 'PUT', value);
      });
      root.querySelector('#writing-reset').addEventListener('click', () => update(root, api, notice, 'DELETE'));
      root.querySelector('#writing-discard').addEventListener('click', () => {
        drafts.delete(selected);
        render(root);
      });
    }
  }

  function renderStatus(root, entry) {
    const dirty = drafts.has(selected);
    root.querySelector('#writing-status').textContent = `${dirty ? 'Unsaved edits · ' : ''}${entry.customized ? 'Saved customization' : 'Using the original default'}`;
    root.querySelector('#writing-discard').disabled = !dirty;
    root.querySelector('#writing-reset').disabled = !dirty && !entry.customized;
    const count = entries.filter(item => item.customized).length;
    document.querySelector('#writing-customization-status').textContent = count ? `${count} customized` : 'Original defaults';
  }

  function render(root) {
    const entry = entries.find(item => item.key === selected) || entries[0];
    if (!entry) return;
    selected = entry.key;
    const select = root.querySelector('select');
    select.replaceChildren();
    const groups = new Map();
    for (const item of entries) {
      if (!groups.has(item.group)) {
        const group = document.createElement('optgroup');
        group.label = item.group;
        groups.set(item.group, group);
        select.append(group);
      }
      const option = document.createElement('option');
      option.value = item.key;
      option.textContent = item.label + (item.customized ? ' · Customized' : '');
      groups.get(item.group).append(option);
    }
    select.value = selected;
    const numeric = typeof entry.default === 'number';
    const text = root.querySelector('#writing-text');
    const number = root.querySelector('#writing-number');
    text.hidden = numeric;
    text.disabled = numeric;
    number.hidden = !numeric;
    number.disabled = !numeric;
    root.querySelector('#writing-value-label').htmlFor = numeric ? 'writing-number' : 'writing-text';
    root.querySelector('#writing-value-label').textContent = numeric ? entry.label : 'Your prompt or template';
    const input = numeric ? number : text;
    input.value = drafts.has(selected) ? drafts.get(selected) : entry.value;
    if (numeric) { number.min = entry.min; number.max = entry.max; number.step = 1; }
    else {
      text.maxLength = selected === 'resume_layout' ? 30000 : 20000;
      text.rows = entry.group === 'Message templates' ? 8 : 14;
    }
    root.querySelector('#writing-template-help').textContent = entry.help;
    root.querySelector('#writing-template-placeholders').textContent = entry.placeholders.length
      ? 'Available placeholders: ' + entry.placeholders.map(key => `{${key}}`).join(', ') : '';
    root.querySelector('#writing-default').textContent = entry.default;
    renderStatus(root, entry);
  }

  async function update(root, api, notice, method, value) {
    const key = selected;
    const controls = [...root.querySelectorAll('button, select, textarea, input')];
    controls.forEach(control => { control.disabled = true; });
    try {
      const result = await api('/api/writing-preferences/' + key, {
        method, ...(method === 'PUT' ? {body: JSON.stringify({value})} : {}),
      });
      entries = result.entries;
      drafts.delete(key);
      notice(method === 'DELETE' ? 'Original default restored' : 'Customization saved for future drafts');
    } catch (error) {
      notice(error.message, true);
    } finally {
      controls.forEach(control => { control.disabled = false; });
      render(root);
    }
  }

  async function mount(root, helpers) {
    if (!root || loading) return;
    mountEditor(root, helpers);
    loading = true;
    try {
      const result = await helpers.api('/api/writing-preferences');
      entries = result.entries;
      render(root);
    } finally {
      loading = false;
    }
  }

  window.JobRadarWriting = {mount};
})();
