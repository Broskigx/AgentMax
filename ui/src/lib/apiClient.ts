/**
 * Shared HTTP client for the Python beta/runtime API on :7790.
 *
 * Port contract:
 *   - 7790 Python — tasks, chat, beta, diagnostics, tools
 *   - 7789 Rust    — vision/LM proxy (see desktopAutomationService / RUST_API)
 */

import { RUNTIME_ENDPOINTS } from '../config/runtimeEndpoints';

export const PYTHON_API_BASE = RUNTIME_ENDPOINTS.pythonApi;

let ipcToken: string | null = null;
let ipcAuthEnabled: boolean | null = null;

export class BackendUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

export function setApiIpcToken(token: string | null) {
  ipcToken = token;
}

export function getApiIpcToken() {
  return ipcToken;
}

export function isIpcAuthEnabled() {
  return ipcAuthEnabled === true;
}

export async function refreshBackendStatus(): Promise<{
  backendOnline: boolean;
  ipcAuthEnabled: boolean;
  modelConnected: boolean;
  banner: string | null;
}> {
  try {
    const health = await fetch(`${PYTHON_API_BASE}/health`, { cache: 'no-store' });
    if (!health.ok) {
      ipcAuthEnabled = false;
      return {
        backendOnline: false,
        ipcAuthEnabled: false,
        modelConnected: false,
        banner: 'Backend offline — cierra y vuelve a abrir AgentMax desde el menu Inicio',
      };
    }

    const healthJson = await health.json().catch(() => ({}));
    ipcAuthEnabled = Boolean(healthJson?.ipc_auth_enabled);

    let modelConnected = false;
    try {
      const ai = await apiFetch('/api/ai/status', { cache: 'no-store' });
      if (ai.ok) {
        const aiJson = await ai.json();
        modelConnected = Boolean(aiJson?.health?.ok);
      }
    } catch {
      modelConnected = false;
    }

    let banner: string | null = null;
    if (healthJson?.limited) {
      const reason = healthJson?.fallback_reason ? `: ${healthJson.fallback_reason}` : '';
      banner = `MODO LIMITADO (${healthJson?.runtime_mode || 'fallback'})${reason}`;
    } else if (ipcAuthEnabled) {
      banner = 'IPC auth ON — local API requires X-AgentMax-Token';
    } else if (!modelConnected) {
      banner = 'Backend online — no external model connected (local beta chat still works)';
    }

    return {
      backendOnline: true,
      ipcAuthEnabled: Boolean(ipcAuthEnabled),
      modelConnected,
      banner,
    };
  } catch {
    ipcAuthEnabled = false;
    return {
      backendOnline: false,
      ipcAuthEnabled: false,
      modelConnected: false,
      banner: 'Backend offline — instala Python 3.11+ y reinicia AgentMax',
    };
  }
}

export function backendHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  if (ipcAuthEnabled && ipcToken) {
    headers.set('X-AgentMax-Token', ipcToken);
  }
  return headers;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = backendHeaders(init?.headers);
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return fetch(`${PYTHON_API_BASE}${path}`, {
    ...init,
    headers,
  });
}
