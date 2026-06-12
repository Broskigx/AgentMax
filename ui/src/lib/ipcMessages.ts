import type { AgentEvent } from '../types';

/**
 * Decode IPC events from WebSocket binary data or JSON text.
 *
 * Supports:
 * - MessagePack binary (via optional @msgpack/msgpack)
 * - Plain JSON text
 * - Newline-delimited JSON (NDJSON)
 */
export async function decodeIpcEvents(
  data: ArrayBuffer | string | Blob
): Promise<AgentEvent[]> {
  if (data instanceof ArrayBuffer) {
    return decodeBinary(data);
  }

  if (typeof data === 'string') {
    return decodeText(data);
  }

  if (data instanceof Blob) {
    const text = await data.text();
    return decodeText(text);
  }

  return [];
}

async function decodeBinary(buffer: ArrayBuffer): Promise<AgentEvent[]> {
  // Try to decode as MessagePack
  try {
    const { decode } = await import('@msgpack/msgpack');
    const result = decode(new Uint8Array(buffer));

    if (Array.isArray(result)) {
      return result.map(normalizeEvent).filter(Boolean) as AgentEvent[];
    }

    const single = normalizeEvent(result as Record<string, unknown>);
    return single ? [single] : [];
  } catch {
    // Fall back to text decoding
    const decoder = new TextDecoder('utf-8');
    const text = decoder.decode(buffer);
    return decodeText(text);
  }
}

function decodeText(text: string): AgentEvent[] {
  // Try NDJSON first (each line is a JSON object)
  if (text.includes('\n')) {
    const lines = text.trim().split('\n').filter(Boolean);
    const events: AgentEvent[] = [];
    for (const line of lines) {
      try {
        const parsed = JSON.parse(line);
        const normalized = normalizeEvent(parsed);
        if (normalized) events.push(normalized);
      } catch {
        // Skip unparseable lines
      }
    }
    if (events.length > 0) return events;
  }

  // Try single JSON object
  try {
    const parsed = JSON.parse(text);
    const normalized = normalizeEvent(parsed);
    if (normalized) return [normalized];
  } catch {
    // Not valid JSON
  }

  return [];
}

function normalizeEvent(raw: Record<string, unknown>): AgentEvent | null {
  if (!raw || typeof raw !== 'object') return null;

  const type = String(raw.type || raw.t || '').toLowerCase();
  const topic = String(raw.topic || raw.event || raw.kind || type);
  const payload = raw.payload || raw.data || raw.params || {};
  const source = String(raw.source || raw.from || 'unknown');

  return {
    type: type || topic,
    topic,
    payload: payload as Record<string, unknown>,
    source,
    ts: typeof raw.ts === 'number' ? raw.ts : typeof raw.timestamp === 'number' ? raw.timestamp : Date.now(),
  };
}
