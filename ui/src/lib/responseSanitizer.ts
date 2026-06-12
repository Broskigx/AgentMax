export interface SanitizedResponse {
  visibleText: string;
  filteredThinking: boolean;
  removedSystemPrompt: boolean;
  truncated: boolean;
}

const THINK_BLOCK_RE = /<\s*think\s*>[\s\S]*?<\s*\/\s*think\s*>/gi;
const THINK_OPEN_RE = /<\s*think\s*>/i;
const THINK_CLOSE_RE = /<\s*\/\s*think\s*>/i;
const SYSTEM_PROMPT_RE = /^\s*(system|developer)\s*:\s*[\s\S]*?(?=(assistant|user)\s*:|$)/i;

export function sanitizeModelResponse(raw: string, maxChars = 12_000): SanitizedResponse {
  let text = String(raw || '');
  const filteredThinking = THINK_OPEN_RE.test(text) || THINK_CLOSE_RE.test(text);

  if (THINK_CLOSE_RE.test(text)) {
    const parts = text.split(THINK_CLOSE_RE);
    text = parts[parts.length - 1] || '';
  }

  text = text.replace(THINK_BLOCK_RE, '');
  text = text.split(THINK_OPEN_RE)[0] || text;
  text = text.replace(THINK_CLOSE_RE, '');

  const beforeSystem = text;
  text = text.replace(SYSTEM_PROMPT_RE, '').trim();
  const removedSystemPrompt = beforeSystem !== text;

  text = text.replace(/\n{3,}/g, '\n\n').trim();
  let truncated = false;
  if (text.length > maxChars) {
    text = text.slice(0, maxChars).trimEnd();
    const lastStop = text.lastIndexOf('.');
    if (lastStop > maxChars - 500) text = text.slice(0, lastStop + 1);
    text += '\n\n[Respuesta recortada por seguridad.]';
    truncated = true;
  }

  return { visibleText: text, filteredThinking, removedSystemPrompt, truncated };
}
