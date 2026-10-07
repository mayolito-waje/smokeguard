// ---------------------------------------------------------------------------
// Theme resolution — shared by the auth gate (login page) and the dashboard
// ---------------------------------------------------------------------------

import type { Theme } from './renderers/drawStripChart';

export function resolveTheme(): Theme {
  try {
    const saved = localStorage.getItem('smokeguard-theme');
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* localStorage unavailable */ }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/** Apply the theme to <html> and persist the choice. */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('smokeguard-theme', theme); } catch { /* noop */ }
}
