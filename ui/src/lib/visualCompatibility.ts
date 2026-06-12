/**
 * Apply visual compatibility profile.
 *
 * In Tauri: use native window decorations + dark titlebar.
 * In browser: add any needed fallback styles.
 */
export function applyVisualCompatibilityProfile(): void {
  const isTauri = typeof window !== 'undefined' && Boolean((window as any).__TAURI_INTERNALS__);

  if (isTauri) {
    // Tauri native: use data attribute for CSS targeting
    document.documentElement.setAttribute('data-tauri', 'true');
  } else {
    // Browser fallback: ensure we have full viewport coverage
    document.documentElement.setAttribute('data-browser', 'true');
  }

  // Apply dark theme class
  document.documentElement.classList.add('AgentMax-ready');

  // Set color-scheme meta
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) {
    meta.setAttribute('content', 'dark');
  }

  console.log('[AgentMax] Visual profile applied:', isTauri ? 'Tauri' : 'Browser');
}
