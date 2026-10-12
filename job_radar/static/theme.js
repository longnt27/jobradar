(() => {
  const storageKey = 'job-radar-theme';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  const validPreference = (value) => ['light', 'dark', 'system'].includes(value) ? value : 'system';
  let preference = 'system';

  try {
    preference = validPreference(localStorage.getItem(storageKey));
  } catch {
    // The theme still works when browser storage is unavailable.
  }

  function applyTheme() {
    document.documentElement.dataset.theme = preference === 'system'
      ? (systemTheme.matches ? 'dark' : 'light') : preference;
    const selector = document.getElementById('theme-preference');
    if (selector) selector.value = preference;
  }

  // Run before the stylesheet loads so the first paint uses the saved theme.
  applyTheme();
  systemTheme.addEventListener('change', applyTheme);
  window.addEventListener('storage', (event) => {
    if (event.key === storageKey || event.key === null) {
      preference = validPreference(event.newValue);
      applyTheme();
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    const selector = document.getElementById('theme-preference');
    applyTheme();
    selector.addEventListener('change', () => {
      preference = validPreference(selector.value);
      try {
        localStorage.setItem(storageKey, preference);
      } catch {
        // Keep the selection for this page even if it cannot be saved.
      }
      applyTheme();
    });
  });
})();
