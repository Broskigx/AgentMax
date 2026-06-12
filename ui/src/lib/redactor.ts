export function redactText(value: string) {
  return String(value || '')
    .replace(/\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g, '<EMAIL>')
    .replace(/\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|127\.0\.0\.1)\b/g, '<PRIVATE_IP>')
    .replace(/[A-Z]:\\Users\\[^\\\s]+/gi, '<USER_PATH>')
    .replace(/(api[_-]?key|token|secret|password|clave)\s*[:=]\s*['"]?([^\s,'"]{6,})/gi, '$1=<SECRET>');
}

export function redactRecord<T>(record: T): T {
  if (typeof record === 'string') return redactText(record) as T;
  if (Array.isArray(record)) return record.map((item) => redactRecord(item)) as T;
  if (record && typeof record === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(record as Record<string, unknown>)) {
      if (['raw_response', 'dataUrl', 'data_url', 'base64', 'token'].includes(key)) {
        out[key] = '<SECRET>';
      } else {
        out[key] = redactRecord(value);
      }
    }
    return out as T;
  }
  return record;
}
