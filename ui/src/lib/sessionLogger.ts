/**
 * Session logging for AgentMax.
 *
 * All logs are stored LOCALLY — never sent to any server.
 * Used to improve AgentMax in future releases.
 */

export interface SessionLogEntry {
  ts?: number;
  session_id?: string;
  user_message?: string;
  backend?: string;
  model_status?: string;
  assistant_response_sanitized?: string;
  raw_response?: string;
  tool_calls?: Array<{ name: string; params?: Record<string, unknown> }>;
  tool_results?: Array<{ name: string; success: boolean; output?: string }>;
  approvals?: string[];
  denials?: string[];
  token_usage?: { estimated?: number; actual?: number; plan?: string };
  screenshot_metadata?: Array<{
    width?: number;
    height?: number;
    bytes?: number;
    timestamp?: number;
    sentToAgent?: boolean;
    stored?: boolean;
  }>;
  safety_flags?: string[];
  outcome?: 'solved' | 'partial' | 'failed' | 'cancelled' | 'model_unavailable' | 'queued' | 'screenshot_captured';
  error?: string;
}

const LOG_PREFIX = 'AgentMax.session.';

let _sessionId: string | null = null;

function getSessionId(): string {
  if (!_sessionId) {
    _sessionId = `session-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  }
  return _sessionId;
}

/**
 * Append a log entry to the current session.
 * Entries are stored in localStorage under AgentMax.session.<sessionId>.
 * Redaction: emails, secrets, tokens, IPs are replaced before storing.
 */
export function appendSessionLog(entry: SessionLogEntry): void {
  try {
    const sid = getSessionId();
    const key = `${LOG_PREFIX}${sid}`;

    // Load existing
    let logs: SessionLogEntry[] = [];
    try {
      const raw = localStorage.getItem(key);
      if (raw) logs = JSON.parse(raw);
    } catch { /* ignore */ }

    // Basic redaction on common fields
    const redacted = redactEntry({
      ...entry,
      ts: entry.ts || Date.now(),
      session_id: sid,
    });

    logs.push(redacted);

    // Keep last 500 entries per session
    if (logs.length > 500) logs = logs.slice(-500);

    localStorage.setItem(key, JSON.stringify(logs));
  } catch {
    // localStorage full — silently drop
  }
}

function redactEntry(entry: SessionLogEntry): SessionLogEntry {
  const text = entry.user_message || '';
  const redacted = {
    ...entry,
    user_message: redactText(text),
    raw_response: entry.raw_response ? '<REDACTED>' : undefined,
  };
  // Clear raw images
  if (redacted.screenshot_metadata) {
    redacted.screenshot_metadata = redacted.screenshot_metadata.map(s => ({
      ...s,
      // Keep metadata, strip actual image data
    }));
  }
  return redacted;
}

function redactText(text: string): string {
  if (!text) return text;
  return text
    .replace(/\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g, '<EMAIL>')
    .replace(/\b(api[_-]?key|token|secret|password|passwd|pwd)\b\s*[:=]\s*['\"]?([^\s,'\"]{6,})/gi, '$1=<SECRET>')
    .replace(/\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)\b/g, '<PRIVATE_IP>')
    .replace(/[A-Z]:\\Users\\[^\\\s]+/gi, '<USER_PATH>')
    .replace(/\/home\/[^/\s]+/gi, '<USER_PATH>');
}

/**
 * Get all entries for the current session.
 */
export function getSessionLogs(): SessionLogEntry[] {
  try {
    const sid = getSessionId();
    const key = `${LOG_PREFIX}${sid}`;
    const raw = localStorage.getItem(key);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return [];
}

/**
 * Clear all session logs.
 */
export function clearSessionLogs(): void {
  try {
    const sid = getSessionId();
    localStorage.removeItem(`${LOG_PREFIX}${sid}`);
  } catch { /* ignore */ }
}

/**
 * Get all session keys from localStorage.
 */
export function getAllSessionKeys(): string[] {
  const keys: string[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(LOG_PREFIX)) {
        keys.push(key);
      }
    }
  } catch { /* ignore */ }
  return keys;
}
