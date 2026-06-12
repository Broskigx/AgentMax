import type { ChatAttachment, ChatMessage, ThinkingSummary } from '../types';
import { apiFetch, BackendUnavailableError } from './apiClient';
import { sanitizeModelResponse } from './responseSanitizer';

export { BackendUnavailableError };

export interface ChatResult {
  content: string;
  source: 'rust-lm-proxy' | 'python-backend' | 'local';
  taskId?: string;
  thinkingCore?: ThinkingSummary;
  think?: string;
  demoMode?: boolean;
}

/**
 * Determine if a user message should start an autonomous task.
 */
export function shouldStartAutonomousTask(text: string): boolean {
  const lower = text.toLowerCase().trim();
  const textOnlyTerms = [
    'solo texto',
    'responde en texto',
    'sin usar herramientas',
    'sin ejecutar',
    'no ejecutes',
    'no uses herramientas',
  ];
  if (textOnlyTerms.some(term => lower.includes(term))) return false;

  const actionPatterns = [
    /\b(abre|abrir|open|launch|inicia|iniciar)\b/,
    /\b(cierra|cerrar|close|kill|mata|deten)\b/,
    /\b(busca|buscar|search|investiga|investigar|navega|navegar)\b/,
    /\b(descarga|download|instala|install)\b/,
    /\b(click|haz clic|mueve|move mouse|captura|screenshot|pantalla)\b/,
    /\b(ejecuta|corre|run|powershell|cmd|terminal)\b/,
    /\b(crea|modifica|edita|borra|elimina|write|delete)\b/,
    /\b(redacta|redactar|escribe|escribir|compose|draft)\b/,
  ];

  return actionPatterns.some(pattern => pattern.test(lower));
}

/**
 * Submit an autonomous task to the backend.
 */
export async function submitAutonomousTask(
  command: string,
  signal: AbortSignal
): Promise<ChatResult> {
  const response = await apiFetch('/api/tasks', {
    method: 'POST',
    body: JSON.stringify({ description: command }),
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => 'Unknown error');
    throw new BackendUnavailableError(`Failed to create task: ${errorText.slice(0, 200)}`);
  }

  const data = await response.json();
  let taskResult = data.result || data.message || 'Task created and queued.';
  let taskStatus = data.status;
  let taskThinking = data.thinking_core || data.thinking_summary;

  if (data.task_id) {
    try {
      const statusResponse = await apiFetch(`/api/tasks/${data.task_id}`, { signal });
      if (statusResponse.ok) {
        const statusData = await statusResponse.json();
        taskResult = statusData.result || taskResult;
        taskStatus = statusData.status || taskStatus;
        taskThinking = statusData.thinking_core || taskThinking;
      }
    } catch {
      // Best-effort status fetch; the task was still accepted by the backend.
    }
  }

  return {
    content: taskStatus === 'completed' ? taskResult : taskResult,
    source: 'python-backend',
    taskId: data.task_id,
    thinkingCore: taskThinking,
  };
}

/**
 * Send a chat message to the AI backend.
 */
export async function sendChatMessage(
  text: string,
  _history: ChatMessage[],
  settings: { temperature: number; maxTokens: number; contextTurns: number; model?: string },
  signal: AbortSignal,
  attachments?: ChatAttachment[]
): Promise<ChatResult> {
  try {
    const response = await apiFetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        message: text,
        model: settings.model,
        temperature: settings.temperature,
        max_tokens: settings.maxTokens,
        context_turns: settings.contextTurns,
        enable_thinking: true,
        thinking: { enabled: true, visible: false },
        attachments: attachments?.map(a => ({
          data_url: a.dataUrl,
          mime: a.mime,
          name: a.name,
        })),
      }),
      signal,
    });

    if (response.ok) {
      const data = await response.json();
      const sanitized = sanitizeModelResponse(data.reply || data.content || '(no response)');
      return {
        content: sanitized.visibleText || '(no response)',
        source: 'python-backend',
        taskId: data.task_id,
        thinkingCore: data.thinking_core || data.thinking_summary,
        think: data.think || data.reasoning_content,
      };
    }
  } catch (error) {
    if (error instanceof BackendUnavailableError) {
      throw error;
    }
    // Fall through to LM Studio only on transport errors, not explicit backend failures.
  }

  try {
    const response = await fetch('http://127.0.0.1:1234/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: settings.model || '',
        messages: [
          {
            role: 'system',
            content: 'Eres AgentMax, el agente tecnico de AgentMax. Razona internamente antes de responder, pero nunca muestres <think>, razonamiento oculto ni cadenas internas. Si necesitas una herramienta, indica una accion segura y verificable.',
          },
          { role: 'user', content: text },
        ],
        temperature: settings.temperature,
        max_tokens: settings.maxTokens,
        stream: false,
        enable_thinking: true,
        chat_template_kwargs: { enable_thinking: true },
        extra_body: { chat_template_kwargs: { enable_thinking: true } },
      }),
      signal,
    });

    if (response.ok) {
      const data = await response.json();
      const message = data?.choices?.[0]?.message || {};
      const sanitized = sanitizeModelResponse(message.content || '');
      return {
        content: sanitized.visibleText || '(empty response)',
        source: 'rust-lm-proxy',
        think: message.reasoning_content || data?.choices?.[0]?.reasoning_content,
      };
    }
  } catch {
    // Fall through
  }

  throw new BackendUnavailableError(
    'Backend offline / no model connected. Start the local Python backend or connect LM Studio on :1234.'
  );
}

/**
 * Record a client failure to the backend.
 */
export async function recordClientFailure(data: {
  prompt: string;
  error: string;
  attachments?: ChatAttachment[];
}): Promise<void> {
  try {
    await apiFetch('/api/chat/failure', {
      method: 'POST',
      body: JSON.stringify({
        prompt: data.prompt,
        error: data.error,
        has_attachments: (data.attachments?.length || 0) > 0,
      }),
    });
  } catch {
    // Best-effort
  }
}