import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

type TauriWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __AGENTMAX_RECOVERY_LOGGER__?: boolean;
};

export type RecoverySeverity = 'trace' | 'debug' | 'info' | 'warn' | 'error';

export interface RecoveryLogRecord {
  id: string;
  ts: number;
  level: RecoverySeverity | string;
  source: string;
  message: string;
  details?: unknown;
}

export interface RecoveryStatus {
  configured: boolean;
  logPath?: string;
  dirtyStartDetected: boolean;
  recordCount: number;
}

export interface LlamaCppStatus {
  configured: boolean;
  running: boolean;
  ready: boolean;
  host: string;
  port: number;
  baseUrl: string;
  modelPath: string;
  serverBin: string;
  models: string[];
  error?: string;
}

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

let tokenPromise: Promise<string | null> | null = null;

export async function getRecoveryToken(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  if (!tokenPromise) {
    tokenPromise = invoke<string>('get_ipc_token').catch(() => null);
  }
  return tokenPromise;
}

async function invokeWithToken<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  const token = await getRecoveryToken();
  if (!token) throw new Error('Tauri IPC token unavailable');
  return invoke<T>(command, { ...args, token });
}

function serializeArg(value: unknown): string {
  if (value instanceof Error) {
    return `${value.name}: ${value.message}${value.stack ? `\n${value.stack}` : ''}`;
  }
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export async function recoveryLogFrontend(
  level: RecoverySeverity,
  source: string,
  message: string,
  details?: unknown,
) {
  if (!isTauriRuntime()) return null;
  const token = await getRecoveryToken();
  return invoke<RecoveryLogRecord>('recovery_log_frontend', {
    level,
    source,
    message,
    details,
    token,
  }).catch(() => null);
}

export function installFrontendRecoveryLogging(source: string) {
  if (!isTauriRuntime()) return;
  const target = window as TauriWindow;
  if (target.__AGENTMAX_RECOVERY_LOGGER__) return;
  target.__AGENTMAX_RECOVERY_LOGGER__ = true;

  const originalError = console.error.bind(console);
  const originalWarn = console.warn.bind(console);

  console.error = (...args: unknown[]) => {
    originalError(...args);
    void recoveryLogFrontend('error', `${source}.console`, args.map(serializeArg).join(' '));
  };

  console.warn = (...args: unknown[]) => {
    originalWarn(...args);
    void recoveryLogFrontend('warn', `${source}.console`, args.map(serializeArg).join(' '));
  };

  window.addEventListener('error', (event) => {
    void recoveryLogFrontend('error', `${source}.window`, event.message || 'Window error', {
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
      stack: event.error?.stack,
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    void recoveryLogFrontend('error', `${source}.promise`, serializeArg(event.reason), {
      reason: serializeArg(event.reason),
    });
  });

  void recoveryLogFrontend('info', source, 'Frontend recovery logging installed', {
    href: window.location.href,
    platform: navigator.platform,
  });
}

export async function getRecoveryLogs() {
  return invokeWithToken<RecoveryLogRecord[]>('recovery_get_logs');
}

export async function getRecoveryStatus() {
  return invokeWithToken<RecoveryStatus>('recovery_get_status');
}

export async function clearRecoveryView() {
  return invokeWithToken<void>('recovery_clear_view');
}

export async function exportRecoveryBundle() {
  return invokeWithToken<string>('recovery_export_bundle');
}

export async function openRecoveryWindow() {
  return invokeWithToken<void>('recovery_open_window');
}

export async function getLlamaCppStatus() {
  return invokeWithToken<LlamaCppStatus>('llamacpp_get_status');
}

export async function startLlamaCpp() {
  return invokeWithToken<LlamaCppStatus>('llamacpp_start');
}

export async function stopLlamaCpp() {
  return invokeWithToken<LlamaCppStatus>('llamacpp_stop');
}

export async function onRecoveryLog(callback: (record: RecoveryLogRecord) => void) {
  if (!isTauriRuntime()) return () => undefined;
  return listen<RecoveryLogRecord>('recovery-log', (event) => callback(event.payload));
}

export async function onLlamaCppStatus(callback: (status: LlamaCppStatus) => void) {
  if (!isTauriRuntime()) return () => undefined;
  return listen<LlamaCppStatus>('llamacpp-status', (event) => callback(event.payload));
}
