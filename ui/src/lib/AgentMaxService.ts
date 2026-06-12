import { sanitizeModelResponse } from './responseSanitizer';

export interface AgentMaxMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface AgentMaxLog {
  id: string;
  ts: number;
  type: 'request' | 'response' | 'error' | 'info';
  method: string;
  endpoint: string;
  statusCode?: number;
  durationMs?: number;
  error?: string;
}

export interface AgentMaxSettings {
  endpoint: string;
  model: string;
  temperature: number;
  maxTokens: number;
  systemPrompt: string;
  timeoutSec: number;
  showThinking: boolean;
}

export const DEFAULT_AGENT_PILOT_SETTINGS: AgentMaxSettings = {
  endpoint: 'http://127.0.0.1:1235/v1/chat/completions',
  model: '',
  temperature: 0.4,
  maxTokens: 2048,
  systemPrompt: `You are AgentMax, an experimental AI assistant for AgentMax desktop automation.

You can help with:
- Analyzing screenshots and suggesting actions
- Planning multi-step desktop tasks
- Coordinating tools (shell, file operations, browser, etc.)

IMPORTANT:
- Be concise and direct
- If you lack information, say so
- Never pretend to have executed actions you haven't
- Your reasoning is internal — respond clearly to the user`,
  timeoutSec: 60,
  showThinking: true,
};

function genLogId(): string {
  return `log-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
}

export async function sendAgentMaxMessage(
  messages: AgentMaxMessage[],
  settings: AgentMaxSettings,
  signal: AbortSignal,
  addLog: (log: AgentMaxLog) => void,
): Promise<string> {
  const t0 = performance.now();

  const thinkingOpts = { enable_thinking: settings.showThinking };
  const payload = {
    model: settings.model || undefined,
    messages,
    temperature: settings.temperature,
    max_tokens: settings.maxTokens,
    stream: false,
    ...thinkingOpts,
    chat_template_kwargs: thinkingOpts,
    extra_body: { chat_template_kwargs: thinkingOpts },
  };

  addLog({
    id: genLogId(),
    ts: Date.now(),
    type: 'request',
    method: 'POST',
    endpoint: settings.endpoint,
  });

  const response = await fetch(settings.endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });

  const durationMs = Math.round(performance.now() - t0);

  if (!response.ok) {
    const errorText = await response.text().catch(() => 'Unknown error');
    addLog({
      id: genLogId(),
      ts: Date.now(),
      type: 'error',
      method: 'POST',
      endpoint: settings.endpoint,
      statusCode: response.status,
      durationMs,
      error: `HTTP ${response.status}: ${errorText.slice(0, 200)}`,
    });
    throw new Error(`AgentMax endpoint returned ${response.status}: ${errorText.slice(0, 200)}`);
  }

  const data = await response.json();

  addLog({
    id: genLogId(),
    ts: Date.now(),
    type: 'response',
    method: 'POST',
    endpoint: settings.endpoint,
    statusCode: response.status,
    durationMs,
  });

  const message = data?.choices?.[0]?.message || {};
  const content = sanitizeModelResponse(message.content || '').visibleText;
  if (!content) {
    throw new Error('Empty response from AgentMax endpoint');
  }

  return content;
}

export async function checkAgentMaxHealth(
  settings: AgentMaxSettings,
  addLog: (log: AgentMaxLog) => void,
): Promise<{ ok: boolean; error?: string }> {
  const t0 = performance.now();
  const baseUrl = settings.endpoint.replace('/chat/completions', '/models');

  try {
    const response = await fetch(baseUrl, {
      method: 'GET',
      signal: AbortSignal.timeout(5000),
    });

    const durationMs = Math.round(performance.now() - t0);

    if (response.ok) {
      const data = await response.json();
      const models = data?.data || data || [];
      addLog({
        id: genLogId(),
        ts: Date.now(),
        type: 'info',
        method: 'GET',
        endpoint: baseUrl,
        statusCode: response.status,
        durationMs,
      });
      return { ok: true };
    }

    return { ok: false, error: `HTTP ${response.status}` };
  } catch (err: any) {
    return { ok: false, error: err?.message || String(err) };
  }
}
