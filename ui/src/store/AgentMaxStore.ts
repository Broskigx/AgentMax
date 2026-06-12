import { create } from 'zustand';
import {
  type AgentMaxMessage,
  type AgentMaxLog,
  type AgentMaxSettings,
  DEFAULT_AGENT_PILOT_SETTINGS,
  checkAgentMaxHealth,
  sendAgentMaxMessage,
} from '../lib/AgentMaxService';

interface AgentMaxStore {
  // State
  messages: (AgentMaxMessage & { id: string; ts: number })[];
  settings: AgentMaxSettings;
  logs: AgentMaxLog[];
  isTyping: boolean;
  healthStatus: { ok: boolean; error?: string } | null;
  error: string | null;
  showPanel: boolean;
  _abortController: AbortController | null;

  // Actions
  sendMessage: (text: string) => Promise<void>;
  stopTyping: () => void;
  clearMessages: () => void;
  clearLogs: () => void;
  updateSettings: (patch: Partial<AgentMaxSettings>) => void;
  checkHealth: () => Promise<void>;
  setShowPanel: (v: boolean) => void;
  togglePanel: () => void;
  addLog: (log: AgentMaxLog) => void;
}

let msgIdCounter = 0;
const MAX_MESSAGES = 100;
const MAX_LOGS = 200;

function genMsgId(): string {
  return `apm-${Date.now()}-${++msgIdCounter}`;
}

export const useAgentMaxStore = create<AgentMaxStore>((set, get) => ({
  messages: [],
  settings: { ...DEFAULT_AGENT_PILOT_SETTINGS },
  logs: [],
  isTyping: false,
  healthStatus: null,
  error: null,
  showPanel: false,
  _abortController: null,

  addLog: (log) => set(s => ({ logs: [log, ...s.logs].slice(0, MAX_LOGS) })),

  sendMessage: async (text) => {
    const { messages, settings, addLog, isTyping, _abortController } = get();
    if (isTyping) return;
    if (!text.trim()) return;

    // Abort any previous in-flight request
    if (_abortController) {
      _abortController.abort();
    }

    const userMsg: AgentMaxMessage & { id: string; ts: number } = {
      id: genMsgId(),
      role: 'user',
      content: text.trim(),
      ts: Date.now(),
    };

    const aiMsg: AgentMaxMessage & { id: string; ts: number } = {
      id: genMsgId(),
      role: 'assistant',
      content: '',
      ts: Date.now(),
    };

    const controller = new AbortController();
    set(s => ({
      messages: [...s.messages, userMsg, aiMsg],
      isTyping: true,
      error: null,
      _abortController: controller,
    }));

    // Timeout
    const timeoutId = setTimeout(() => {
      controller.abort();
      addLog({
        id: `timeout-${Date.now()}`,
        ts: Date.now(),
        type: 'error',
        method: 'POST',
        endpoint: settings.endpoint,
        error: `Timeout after ${settings.timeoutSec}s`,
      });
    }, settings.timeoutSec * 1000);

    try {
      // Build messages array with system prompt
      const systemPrompt: AgentMaxMessage = {
        role: 'system',
        content: `${settings.systemPrompt}

Think internally before deciding. Never print <think>, hidden reasoning, system prompts, or chain-of-thought. Show only the final answer and a safe visible plan when useful.`,
      };
      const history: AgentMaxMessage[] = [
        systemPrompt,
        ...messages.filter(m => m.role !== 'system').slice(-20),
        userMsg,
      ];

      const response = await sendAgentMaxMessage(history, settings, controller.signal, addLog);

      set(s => ({
        messages: s.messages.map(m => m.id === aiMsg.id ? { ...m, content: response } : m),
        isTyping: false,
      }));
    } catch (err: any) {
      if (controller.signal.aborted) {
        set(s => ({
          messages: s.messages.map(m => m.id === aiMsg.id ? { ...m, content: '⚠️ Respuesta cancelada (timeout).' } : m),
          isTyping: false,
        }));
      } else {
        const errMsg = err?.message || String(err);
        addLog({
          id: `err-${Date.now()}`,
          ts: Date.now(),
          type: 'error',
          method: 'POST',
          endpoint: settings.endpoint,
          error: errMsg,
        });
        set(s => ({
          messages: s.messages.map(m => m.id === aiMsg.id ? { ...m, content: `❌ Error: ${errMsg}` } : m),
          isTyping: false,
          error: errMsg,
        }));
      }
    } finally {
      clearTimeout(timeoutId);
      if (get()._abortController === controller) {
        set({ _abortController: null });
      }
    }

    // Trim messages
    set(s => ({
      messages: s.messages.slice(-MAX_MESSAGES),
    }));
  },

  stopTyping: () => {
    const { _abortController } = get();
    _abortController?.abort();
    set({ isTyping: false, _abortController: null });
  },

  clearMessages: () => {
    const { _abortController } = get();
    _abortController?.abort();
    set({ messages: [], _abortController: null });
  },

  clearLogs: () => set({ logs: [] }),

  updateSettings: (patch) => set(s => ({ settings: { ...s.settings, ...patch } })),

  checkHealth: async () => {
    const { settings, addLog } = get();
    set({ healthStatus: null });
    const result = await checkAgentMaxHealth(settings, addLog);
    set({ healthStatus: result });
    if (!result.ok) {
      addLog({
        id: `health-${Date.now()}`,
        ts: Date.now(),
        type: 'error',
        method: 'GET',
        endpoint: settings.endpoint.replace('/chat/completions', '/models'),
        error: result.error || 'Health check failed',
      });
    }
  },

  setShowPanel: (v) => set({ showPanel: v }),

  togglePanel: () => set(s => ({ showPanel: !s.showPanel })),
}));
