import { create } from 'zustand';
import { invoke } from '@tauri-apps/api/core';
import { AgentEvent, AgentState, ChatAttachment, ChatMessage, ExperienceMode, StepLog, SystemStatus, TaskStatus, ThinkingSummary, ToolDiagnostics, WorkflowItem } from '../types';
import {
  apiFetch,
  refreshBackendStatus,
  setApiIpcToken,
} from '../lib/apiClient';
import {
  BackendUnavailableError,
  recordClientFailure,
  sendChatMessage,
  shouldStartAutonomousTask,
  submitAutonomousTask,
  type ChatResult,
} from '../lib/chatService';
import {
  fetchScreenInfo,
  isMouseCommand,
  isScreenshotCommand,
  runMouseMoveTool,
  runScreenshotTool,
} from '../lib/computerMaxTools';
import {
  getDesktopPermissions,
  setDesktopPermissions,
  type PermissionState,
} from '../lib/desktopAutomationService';
import { decodeIpcEvents } from '../lib/ipcMessages';
import { extractTools } from '../lib/toolParser';
import {
  type ChatSessionMeta,
  createSession,
  getActiveSessionId,
  getSessionMessages,
  getSessionsStore,
  initSessionsStore,
  removeSession,
  setActiveSessionId,
  setSessionsStore,
  upsertSessionMessages,
} from '../lib/chatSessions';

// ── Constants ──────────────────────────────────────────────────────────────────

import { RUNTIME_ENDPOINTS } from '../config/runtimeEndpoints';
import { loadUserPreferences, saveUserPreferences } from '../lib/userPreferences';

const API_BASE  = RUNTIME_ENDPOINTS.pythonApi;
const RUST_API  = RUNTIME_ENDPOINTS.rustApi;
const WS_URL    = RUNTIME_ENDPOINTS.ws;

const _savedPrefs = loadUserPreferences();
const WS_RECONNECT_DELAY = 3000;
const WS_MAX_RECONNECT_DELAY = 30000;
const STATUS_POLL_INTERVAL = 15000;
const SAVE_DEBOUNCE_MS = 500;
const MAX_MESSAGES_STORAGE = 100;
const LEGACY_TEST_RESPONSE_RE = /(Estoy en linea\.\s*Soy AgentMax en modo prueba local,\s*listo para validar la primera experiencia\.?|El servidor de prueba responde sin modelo externo|validar AgentMax antes de conectar un proveedor real)/i;

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function isTauriRuntime() {
  return typeof window !== 'undefined' && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function _updateActiveTaskMsg(
  messages: ChatMessage[],
  taskId: string | null,
  patch: Partial<ChatMessage>,
): ChatMessage[] {
  if (!taskId || !messages.length) return messages;
  const lastIdx = [...messages].reverse().findIndex(m => m.taskId === taskId);
  if (lastIdx === -1) return messages;
  const idx = messages.length - 1 - lastIdx;
  const next = [...messages];
  next[idx] = { ...next[idx], ...patch };
  return next;
}

function _getActiveTaskMsg(messages: ChatMessage[], taskId: string | null) {
  return taskId ? [...messages].reverse().find(m => m.taskId === taskId) : undefined;
}

function normalizeThinkingSummary(raw: any): ThinkingSummary | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  return {
    intent: raw.intent,
    reasoningDepth: raw.reasoning_depth ?? raw.reasoningDepth,
    confidenceScore: raw.confidence_score ?? raw.confidenceScore,
    uncertaintyScore: raw.uncertainty_score ?? raw.uncertaintyScore,
    riskScore: raw.risk_score ?? raw.riskScore,
    complexityScore: raw.complexity_score ?? raw.complexityScore,
    ambiguityScore: raw.ambiguity_score ?? raw.ambiguityScore,
    planningDepth: raw.planning_depth ?? raw.planningDepth,
    checklistScore: raw.checklist_score ?? raw.checklistScore,
    checklist: raw.checklist,
    tokenUsage: raw.token_usage ?? raw.tokenUsage,
    tokenBalance: raw.token_balance ?? raw.tokenBalance,
    objectives: raw.objectives,
    recoveryStrategies: raw.recovery_strategies ?? raw.recoveryStrategies,
    toolReflectionCount: raw.tool_reflection_count ?? raw.toolReflectionCount,
    suggestedTools: raw.suggested_tools ?? raw.suggestedTools,
    showChecklist: raw.show_checklist ?? raw.showChecklist,
    showThinkingPanel: raw.show_thinking_panel ?? raw.showThinkingPanel,
  };
}

function isSimpleConversationalMessage(text: string) {
  const normalized = text
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[¿?¡!.,:;]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!normalized) return true;
  const simplePatterns = [
    /^(hola|hey|buenas|hello)( pilot| AgentMax| AgentMax)?( como estas)?$/,
    /^(como estas|que tal|todo bien|estas ahi)$/,
    /^(gracias|ok|dale|perfecto|listo|bien|genial)$/,
    /^(quien eres|quien sos|que eres)$/,
  ];
  return normalized.length <= 80 && simplePatterns.some((pattern) => pattern.test(normalized));
}

function shouldShowOperationalThinking(
  text: string,
  attachments: ChatAttachment[],
  result: { taskId?: string; thinkingCore?: ThinkingSummary },
  extractedTools: Array<{ name: string; params: Record<string, string> }>,
) {
  if (attachments.length > 0 || result.taskId || extractedTools.length > 0) return true;
  if (isSimpleConversationalMessage(text)) return false;

  const lower = text.toLowerCase();
  const evidenceTerms = [
    'error', 'bug', 'falla', 'fallo', 'backend', 'frontend', 'api', 'server',
    'archivo', 'carpeta', 'log', 'stacktrace', 'captura', 'pantalla',
    'revisa', 'analiza', 'diagnostica', 'arregla', 'debug', 'deploy',
    'instala', 'ejecuta', 'cmd', 'powershell', 'terminal', 'click',
  ];
  const risk = result.thinkingCore?.riskScore ?? 0;
  const complexity = result.thinkingCore?.complexityScore ?? 0;
  return shouldStartAutonomousTask(text) || risk > 0.18 || complexity > 0.35 || evidenceTerms.some(term => lower.includes(term));
}

function prepareVisibleThinking(
  summary: ThinkingSummary | undefined,
  showOperationalThinking: boolean,
): ThinkingSummary | undefined {
  if (!summary) return undefined;
  if (showOperationalThinking) {
    return { ...summary, showChecklist: true, showThinkingPanel: true };
  }
  const { checklist: _checklist, suggestedTools: _suggestedTools, ...rest } = summary;
  return { ...rest, showChecklist: false, showThinkingPanel: false };
}

async function revealAssistantMessage(
  aiId: string,
  finalContent: string,
  patch: Partial<ChatMessage>,
  signal: AbortSignal,
  set: (partial: Partial<AgentStore> | ((state: AgentStore) => Partial<AgentStore>)) => void,
) {
  const text = finalContent || '';
  const minDelay = text.length > 700 ? 3 : text.length > 320 ? 7 : 13;
  const punctuationDelay = text.length > 700 ? 8 : 28;
  let visible = '';

  for (let index = 0; index < text.length; index += 1) {
    if (signal.aborted) break;
    visible += text[index];
    set((s) => ({
      messages: s.messages.map((message) => (
        message.id === aiId
          ? { ...message, ...patch, content: visible, status: 'streaming' as const, timestamp: Date.now() }
          : message
      )),
    }));
    const char = text[index];
    const delay = /[.!?]\s/.test(`${char}${text[index + 1] ?? ''}`) ? punctuationDelay : minDelay;
    await new Promise(resolve => window.setTimeout(resolve, delay));
  }

  set((s) => {
    const next = s.messages.map((message) => (
      message.id === aiId
        ? { ...message, ...patch, content: text, status: signal.aborted ? 'cancelled' as const : 'done' as const, timestamp: Date.now() }
        : message
    ));
    saveMessages(next);
    return { messages: next };
  });
}

function newMessageId(prefix: 'u' | 'ai') {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj?.randomUUID) return `${prefix}-${cryptoObj.randomUUID()}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadMessages(): ChatMessage[] {
  try {
    const saved = localStorage.getItem('AgentMax.messages');
    if (!saved) return [];
    return (JSON.parse(saved) as ChatMessage[])
      .filter((m) => m.role === 'user' || Boolean(m.content))
      .filter((m) => !LEGACY_TEST_RESPONSE_RE.test(m.content || ''))
      .map((m) => ({
        ...m,
        status: m.status === 'sending' || m.status === 'streaming' ? 'cancelled' : m.status,
      }));
  } catch { return []; }
}

// Debounced save to localStorage — avoids excessive disk I/O
let _saveTimer: ReturnType<typeof setTimeout> | null = null;
let _pendingMessages: ChatMessage[] | null = null;

function saveMessages(messages: ChatMessage[]) {
  _pendingMessages = messages.map((message) => ({
    ...message,
    attachments: message.attachments?.map((attachment) => ({ ...attachment, dataUrl: undefined })),
  }));
  if (_saveTimer) return;
  _saveTimer = setTimeout(() => {
    if (_pendingMessages) {
      const trimmed = _pendingMessages.slice(-MAX_MESSAGES_STORAGE);
      localStorage.setItem('AgentMax.messages', JSON.stringify(trimmed));
      const sessionId = getActiveSessionId();
      if (sessionId) {
        const store = upsertSessionMessages(getSessionsStore(), sessionId, trimmed);
        setSessionsStore(store);
        useAgentStore.setState({ chatSessions: store.sessions });
      }
      _pendingMessages = null;
    }
    _saveTimer = null;
  }, SAVE_DEBOUNCE_MS);
}

// Flush any pending save immediately (call before unload)
function flushSaveMessages() {
  if (_saveTimer) {
    clearTimeout(_saveTimer);
    _saveTimer = null;
  }
  if (_pendingMessages) {
    localStorage.setItem('AgentMax.messages', JSON.stringify(_pendingMessages.slice(-MAX_MESSAGES_STORAGE)));
    _pendingMessages = null;
  }
}

// Flush on page unload
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', flushSaveMessages);
}

// ── Store ────────────────────────────────────────────────────────────────────

type AIBackend = 'claude' | 'lmstudio' | 'local_peft' | 'llamacpp' | 'AgentMax' | 'mock' | 'test';

interface AIStatus {
  backend: AIBackend;
  model: string;
  models?: string[];
  has_loaded_model?: boolean;
  supports_vision: boolean;
  health: { ok: boolean; models?: string[]; has_loaded_model?: boolean; error?: string };
}

interface LearningPrompt {
  taskId: string;
  target: string;
  bounds: number[];
  confidence: number;
  question: string;
}

export type ModuleName = 'mouse' | 'keyboard' | 'screen';
export type { ExperienceMode };

export interface AdvancedAISettings {
  temperature: number;
  maxTokens: number;
  contextTurns: number;
  autoTools: boolean;
  verboseReasoning: boolean;
}

export interface TerminalLine {
  id: string;
  type: 'cmd' | 'stdout' | 'stderr' | 'info' | 'error';
  text: string;
  ts: number;
}

interface ComputerControlRequest {
  reason: string;
  command: string;
  assistantMessageId: string;
}

interface AgentStore {
  ipcToken: string | null;
  initToken: () => Promise<void>;

  modMouse: boolean;
  modKeyboard: boolean;
  modScreen: boolean;
  toggleModule: (mod: ModuleName) => Promise<void>;

  wsConnected: boolean;
  ws: WebSocket | null;
  agentState: AgentState;
  currentTaskId: string | null;
  currentTask: TaskStatus | null;
  stepLogs: StepLog[];
  systemStatus: SystemStatus | null;
  toolDiagnostics: ToolDiagnostics | null;
  toolDoctorReport: any | null;
  workflows: WorkflowItem[];
  aiStatus: AIStatus | null;
  lmstudioModels: string[];
  selectedModel: string;
  learningPrompt: LearningPrompt | null;
  isRecording: boolean;
  airGapEnabled: boolean;
  licenseRevoked: boolean;
  messages: ChatMessage[];
  activeChatId: string;
  chatSessions: ChatSessionMeta[];
  isTyping: boolean;
  experienceMode: ExperienceMode;
  advancedAISettings: AdvancedAISettings;
  isMinimized: boolean;
  showOverlay: boolean;
  showCommandPalette: boolean;
  computerControlActive: boolean;
  computerControlStartedAt: number | null;
  computerControlCommand: string | null;
  computerControlRequest: ComputerControlRequest | null;
  pendingComputerControlMessageId: string | null;
  chatAbortController: AbortController | null;
  backendOnline: boolean;
  connectionBanner: string | null;
  ipcAuthEnabled: boolean;

  connect: () => void;
  refreshConnectionStatus: () => Promise<void>;
  disconnect: () => void;
  stopAI: () => Promise<void>;
  sendMessage: (text: string, attachments?: ChatAttachment[]) => Promise<void>;
  sendDirectMessage: (text: string) => Promise<void>;
  exportChat: () => void;
  clearMessages: () => void;
  createNewChat: () => void;
  switchChat: (sessionId: string) => void;
  deleteChat: (sessionId: string) => void;
  submitTask: (description: string) => Promise<string | null>;
  cancelTask: (taskId: string) => Promise<void>;
  emergencyStop: () => Promise<void>;
  finishComputerControl: () => Promise<void>;
  fetchStatus: () => Promise<void>;
  fetchToolDiagnostics: () => Promise<void>;
  runToolsDoctor: () => Promise<void>;
  runToolsDryTest: () => Promise<void>;
  fetchAIStatus: () => Promise<void>;
  fetchLMStudioModels: () => Promise<void>;
  setSelectedModel: (model: string) => void;
  _handleEvent: (event: AgentEvent) => void;
  setShowCommandPalette: (v: boolean) => void;
  toggleAirGap: () => void;
  switchBackend: (backend: 'claude' | 'lmstudio' | 'local_peft' | 'llamacpp' | 'AgentMax' | 'mock') => Promise<void>;
  setExperienceMode: (mode: ExperienceMode) => void;
  updateAdvancedAISettings: (patch: Partial<AdvancedAISettings>) => void;
  approveComputerControl: () => Promise<void>;
  denyComputerControl: () => void;
  setMinimized: (v: boolean) => void;
  confirmElement: (taskId: string, confirmed: boolean) => void;
  dismissLearningPrompt: () => void;
  setRecording: (v: boolean) => void;
}

const _bootMessages = loadMessages();
const _bootSessions = initSessionsStore(_bootMessages);

export const useAgentStore = create<AgentStore>((set, get) => ({
  ipcToken: null,
  backendOnline: false,
  connectionBanner: 'Checking backend...',
  ipcAuthEnabled: false,
  initToken: async () => {
    if (!isTauriRuntime()) {
      set({ ipcToken: null });
      setApiIpcToken(null);
      await get().refreshConnectionStatus();
      return;
    }

    try {
      const token = await invoke<string>('get_ipc_token');
      setApiIpcToken(token);
      set({ ipcToken: token });
      const states = await invoke<{ mouse: boolean; keyboard: boolean; screen: boolean }>(
        'get_module_states', { token }
      );
      set({ modMouse: states.mouse, modKeyboard: states.keyboard, modScreen: states.screen });
    } catch (e) { console.error('IPC Token error', e); }
    await get().refreshConnectionStatus();
  },

  refreshConnectionStatus: async () => {
    const status = await refreshBackendStatus();
    set({
      backendOnline: status.backendOnline,
      connectionBanner: status.banner,
      ipcAuthEnabled: status.ipcAuthEnabled,
    });
  },

  modMouse: true,
  modKeyboard: true,
  modScreen: true,
  toggleModule: async (mod) => {
    const { ipcToken } = get();
    if (!ipcToken) return;
    const active = await invoke<boolean>('toggle_module', { module: mod, token: ipcToken });
    set({
      modMouse: mod === 'mouse' ? active : get().modMouse,
      modKeyboard: mod === 'keyboard' ? active : get().modKeyboard,
      modScreen: mod === 'screen' ? active : get().modScreen
    });
  },

  wsConnected: false,
  ws: null,
  agentState: 'idle',
  currentTaskId: null,
  currentTask: null,
  stepLogs: [],
  systemStatus: null,
  toolDiagnostics: null,
  toolDoctorReport: null,
  workflows: [],
  aiStatus: null,
  lmstudioModels: [],
  selectedModel: _savedPrefs.selectedModel,
  learningPrompt: null,
  isRecording: false,
  airGapEnabled: false,
  licenseRevoked: false,
  messages: getSessionMessages(_bootSessions, _bootSessions.activeId),
  activeChatId: _bootSessions.activeId,
  chatSessions: _bootSessions.sessions,
  isTyping: false,
  experienceMode: _savedPrefs.experienceMode,
  advancedAISettings: _savedPrefs.advancedAISettings,
  isMinimized: false,
  showOverlay: false,
  showCommandPalette: false,
  computerControlActive: false,
  computerControlStartedAt: null,
  computerControlCommand: null,
  computerControlRequest: null,
  pendingComputerControlMessageId: null,
  chatAbortController: null,

  connect: () => {
    const state = get();
    if (state.ws?.readyState === WebSocket.OPEN) return;
    // Close existing connection before creating new one
    if (state.ws) {
      state.ws.onclose = null;
      state.ws.close();
    }

    const ws = new WebSocket(WS_URL);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      const { ipcToken, ipcAuthEnabled } = get();
      if (ipcAuthEnabled) {
        if (!ipcToken) {
          ws.close(4401, 'IPC token unavailable');
          return;
        }
        ws.send(JSON.stringify({ cmd: 'auth', token: ipcToken }));
      } else {
        set({ wsConnected: true });
      }
    };
    ws.onclose = () => {
      set({ wsConnected: false });
      // Exponential backoff for reconnection
      const delay = WS_RECONNECT_DELAY;
      setTimeout(() => {
        if (get().ws?.readyState !== WebSocket.OPEN) {
          get().connect();
        }
      }, delay);
    };
    ws.onmessage = async (e) => {
      try {
        if (typeof e.data === 'string') {
          const control = JSON.parse(e.data);
          if (control?.type === 'auth_ok') {
            set({ wsConnected: true });
            return;
          }
          if (control?.type === 'auth_failed') {
            set({ wsConnected: false });
            ws.close(4401, String(control.reason || 'IPC authentication failed'));
            return;
          }
        }
        const events = await decodeIpcEvents(e.data);
        for (const ev of events) {
          get()._handleEvent(ev);
        }
      } catch (err) {
        console.warn('WS decode error:', err);
      }
    };
    set({ ws });
  },

  disconnect: () => {
    const ws = get().ws;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    set({ ws: null, wsConnected: false });
  },

  stopAI: async () => {
    const { chatAbortController, ipcToken } = get();
    chatAbortController?.abort();
    try {
      await apiFetch('/api/chat/stop', { method: 'POST' });
    } catch (e) {
      console.error('Stop AI failed', e);
    } finally {
      set((s) => {
        const next = s.messages.map((m) => (
          m.status === 'sending' || m.status === 'streaming'
            ? { ...m, status: 'cancelled' as const, content: m.content || 'Respuesta detenida.' }
            : m
        ));
        saveMessages(next);
        return { messages: next, isTyping: false, agentState: 'idle', chatAbortController: null };
      });
    }
  },

  sendMessage: async (text: string, attachments: ChatAttachment[] = []) => {
    const cleanText = text.trim();
    const safeAttachments = attachments.slice(0, 4);
    if (!cleanText && safeAttachments.length === 0) return;
    if (get().isTyping) {
      const activeController = get().chatAbortController;
      if (activeController) return;
      set({ isTyping: false, agentState: 'idle' });
    }

    if (safeAttachments.length === 0 && shouldStartAutonomousTask(cleanText) && !get().computerControlActive) {
      const userMsg: ChatMessage = {
        id: newMessageId('u'),
        role: 'user',
        content: cleanText,
        timestamp: Date.now(),
        status: 'done',
        source: 'local',
      };
      const aiId = newMessageId('ai');
      const aiMsg: ChatMessage = {
        id: aiId,
        role: 'assistant',
        content: 'Necesito activar ComputerMax para controlar tu equipo antes de ejecutar esta tarea.',
        timestamp: Date.now(),
        status: 'done',
        source: 'local',
        thinkingStartedAt: Date.now(),
        thinkingElapsedMs: 0,
        thinkingSummary: {
          intent: 'computer_control_permission',
          reasoningDepth: 'dynamic',
          confidenceScore: 0.74,
          riskScore: 0.22,
          suggestedTools: ['supervisor', 'desktop_control', 'tools'],
        },
      };
      set((s) => {
        const next = [...s.messages, userMsg, aiMsg];
        saveMessages(next);
        return {
          messages: next,
          agentState: 'idle',
          isTyping: false,
          computerControlCommand: cleanText,
          pendingComputerControlMessageId: aiId,
          computerControlRequest: {
            assistantMessageId: aiId,
            command: cleanText,
            reason: 'Esta tarea puede abrir aplicaciones, mover el mouse, escribir texto, consultar la pantalla o ejecutar herramientas del sistema. ComputerMax necesita tu autorización para actuar fuera del chat.',
          },
        };
      });
      return;
    }

    const currentMessages = get().messages;
    const settings = { ...get().advancedAISettings, model: get().selectedModel };
    const controller = new AbortController();
    let timedOut = false;

    const slowNoticeId = window.setTimeout(() => {
      set((s) => {
        if (!s.isTyping) return {};
        const aiId = s.messages[s.messages.length - 1]?.id;
        if (!aiId) return {};
        const next = s.messages.map((m) => (
          m.id === aiId && !m.content
            ? {
                ...m,
                content: 'LM Studio esta generando la respuesta. El modelo local puede tardar si esta cargando o usando CPU/GPU.',
                status: 'streaming' as const,
                timestamp: Date.now(),
              }
            : m
        ));
        saveMessages(next);
        return { messages: next };
      });
    }, 8000);

    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 90000);

    const userMsg: ChatMessage = {
      id: newMessageId('u'),
      role: 'user',
      content: cleanText || 'Imagen adjunta',
      timestamp: Date.now(),
      status: 'done',
      source: 'local',
      attachments: safeAttachments,
    };
    const aiId = newMessageId('ai');
    const initialOperationalThinking = safeAttachments.length > 0 || shouldStartAutonomousTask(cleanText) || !isSimpleConversationalMessage(cleanText);
    const aiMsg: ChatMessage = {
      id: aiId,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      status: 'sending',
      source: 'rust-lm-proxy',
      thinkingStartedAt: Date.now(),
      thinkingSummary: {
        intent: safeAttachments.length > 0 ? 'analizando imagen' : 'analizando solicitud',
        reasoningDepth: 'dynamic',
        confidenceScore: 0,
        riskScore: 0,
        suggestedTools: safeAttachments.length > 0 ? ['thinking_core', 'vision_metadata'] : ['thinking_core'],
        showThinkingPanel: initialOperationalThinking,
        showChecklist: false,
      },
    };

    set(s => {
      const next = [...s.messages, userMsg, aiMsg];
      saveMessages(next);
      return { messages: next, isTyping: true, agentState: 'working', chatAbortController: controller };
    });

    try {
      const result = await sendChatMessage(cleanText, currentMessages, settings, controller.signal, safeAttachments);

      const { clean: displayContent, tools } = extractTools(result.content);
      const normalizedThinking = normalizeThinkingSummary(result.thinkingCore);
      const showOperationalThinking = shouldShowOperationalThinking(cleanText, safeAttachments, result, tools);
      const visibleThinking = prepareVisibleThinking(normalizedThinking, showOperationalThinking);

      await revealAssistantMessage(
        aiId,
        displayContent,
        {
          source: result.source,
          taskId: result.taskId,
          thinkingElapsedMs: Date.now() - (aiMsg.thinkingStartedAt ?? aiMsg.timestamp),
          thinkingSummary: visibleThinking ?? (showOperationalThinking ? aiMsg.thinkingSummary : undefined),
          taskStatus: (result.taskId ? 'queued' : undefined) as 'queued' | undefined,
        },
        controller.signal,
        set,
      );

      set(s => ({ currentTaskId: result.taskId || s.currentTaskId }));

      if (result.taskId) set({ showOverlay: true, isMinimized: true });

      if (tools.length > 0) {
        const { ipcToken } = get();
        const toolResults: string[] = [];
        for (const tool of tools.slice(0, 5)) {
          const output = await _executeTool(tool.name, tool.params, ipcToken, set, get);
          if (output) toolResults.push(`[${tool.name}] → ${output}`);
        }

        // Feed results back to model so it can confirm success or retry on failure
        if (toolResults.length > 0 && !controller.signal.aborted) {
          const feedback = `Tool execution results:\n${toolResults.join('\n')}`;
          const feedbackMsg: ChatMessage = {
            id: newMessageId('u'),
            role: 'user',
            content: feedback,
            timestamp: Date.now(),
            status: 'done',
            source: 'local',
          };
          const feedbackAiId = newMessageId('ai');
          const feedbackAiMsg: ChatMessage = {
            id: feedbackAiId,
            role: 'assistant',
            content: '',
            timestamp: Date.now(),
            status: 'sending',
            source: 'rust-lm-proxy',
          };
          set(s => {
            const next = [...s.messages, feedbackMsg, feedbackAiMsg];
            saveMessages(next);
            return { messages: next, isTyping: true };
          });
          try {
            const fbController = new AbortController();
            const feedbackResult = await sendChatMessage(
              feedback,
              get().messages.slice(0, -2), // history before feedback msgs
              settings,
              fbController.signal,
            );
            const { clean: fbClean } = extractTools(feedbackResult.content);
            set(s => {
              const next = s.messages.map(m => m.id === feedbackAiId ? {
                ...m, content: fbClean, status: 'done' as const, timestamp: Date.now(),
              } : m);
              saveMessages(next);
              return { messages: next };
            });
          } catch { /* feedback follow-up is best-effort */ }
        }
      }
    } catch (err: any) {
      const aborted = controller.signal.aborted;
      const backendOffline = err instanceof BackendUnavailableError;
      const message = timedOut
        ? 'Error: LM Studio no entrego respuesta despues de 90 segundos. Revisa si el modelo esta cargado, si quedo una generacion pegada o si la GPU/CPU esta saturada.'
        : aborted
          ? 'Respuesta detenida.'
          : backendOffline
            ? `Backend offline / no model connected: ${err.message}`
            : `Error: ${err.message || String(err)}`;
      if (backendOffline) {
        set({
          backendOnline: false,
          connectionBanner: 'Backend offline — reinicia AgentMax o exporta Diagnostics para soporte',
        });
      }
      if (!aborted) {
        void recordClientFailure({ prompt: cleanText, error: message, attachments: safeAttachments });
      }
      set(s => {
        const next = s.messages.map(m => m.id === aiId ? {
          ...m,
          content: message,
          status: aborted ? 'cancelled' as const : 'error' as const,
          error: aborted ? undefined : message,
          timestamp: Date.now(),
        } : m);
        saveMessages(next);
        return { messages: next };
      });
    } finally {
      window.clearTimeout(slowNoticeId);
      window.clearTimeout(timeoutId);
      set({ isTyping: false, chatAbortController: null });
      set({ agentState: 'idle' });
    }
  },

  sendDirectMessage: async (text: string) => {
    return get().sendMessage(text);
  },

  exportChat: () => {
    const lines = get().messages.map((message) => {
      const who = message.role === 'user' ? 'Usuario' : 'AgentMax';
      return `## ${who}\n\n${message.content || ''}`;
    });
    const blob = new Blob([lines.join('\n\n---\n\n')], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `AgentMax-chat-${new Date().toISOString().slice(0, 10)}.md`;
    link.click();
    URL.revokeObjectURL(url);
  },

  clearMessages: () => {
    get().chatAbortController?.abort();
    flushSaveMessages();
    localStorage.setItem('AgentMax.messages', JSON.stringify([]));
    const sessionId = get().activeChatId;
    if (sessionId) {
      const store = upsertSessionMessages(getSessionsStore(), sessionId, []);
      setSessionsStore(store);
      set({ chatSessions: store.sessions });
    }
    set({ messages: [], agentState: 'idle', isTyping: false, chatAbortController: null });
  },

  createNewChat: () => {
    get().chatAbortController?.abort();
    flushSaveMessages();
    let store = upsertSessionMessages(getSessionsStore(), get().activeChatId, get().messages);
    store = createSession(store);
    setSessionsStore(store);
    setActiveSessionId(store.activeId);
    localStorage.setItem('AgentMax.messages', JSON.stringify([]));
    set({
      activeChatId: store.activeId,
      chatSessions: store.sessions,
      messages: [],
      agentState: 'idle',
      isTyping: false,
      chatAbortController: null,
    });
  },

  switchChat: (sessionId) => {
    if (sessionId === get().activeChatId) return;
    get().chatAbortController?.abort();
    flushSaveMessages();
    let store = upsertSessionMessages(getSessionsStore(), get().activeChatId, get().messages);
    const messages = getSessionMessages(store, sessionId);
    store = { ...store, activeId: sessionId };
    setSessionsStore(store);
    setActiveSessionId(sessionId);
    localStorage.setItem('AgentMax.messages', JSON.stringify(messages));
    set({
      activeChatId: sessionId,
      chatSessions: store.sessions,
      messages,
      agentState: 'idle',
      isTyping: false,
      chatAbortController: null,
    });
  },

  deleteChat: (sessionId) => {
    const store = removeSession(getSessionsStore(), sessionId);
    if (!store) return;
    setSessionsStore(store);
    setActiveSessionId(store.activeId);
    const messages = getSessionMessages(store, store.activeId);
    localStorage.setItem('AgentMax.messages', JSON.stringify(messages));
    set({
      activeChatId: store.activeId,
      chatSessions: store.sessions,
      messages,
      agentState: 'idle',
      isTyping: false,
      chatAbortController: null,
    });
  },

  submitTask: async (description) => {
    const { ipcToken } = get();
    if (!ipcToken) return null;
    try {
      const res = await invoke<any>('submit_task', { description, token: ipcToken });
      set({ currentTaskId: res.task_id, stepLogs: [] });
      return res.task_id;
    } catch (err) {
      console.error('submitTask failed:', err);
      return null;
    }
  },

  cancelTask: async (id) => {
    const { ipcToken } = get();
    if (!ipcToken) return;
    try {
      await invoke('get_task_status', { task_id: id, token: ipcToken });
    } catch {}
    set({ agentState: 'idle', showOverlay: false });
  },

  emergencyStop: async () => {
    const { ipcToken } = get();
    if (ipcToken) {
      try {
        await invoke('emergency_stop', { token: ipcToken });
      } catch {}
    }
    set({ agentState: 'idle', showOverlay: false });
  },

  setShowCommandPalette: (v) => set({ showCommandPalette: v }),
  toggleAirGap: () => set((s) => ({ airGapEnabled: !s.airGapEnabled })),
  switchBackend: async (backend: 'claude' | 'lmstudio' | 'local_peft' | 'llamacpp' | 'AgentMax' | 'mock') => {
    saveUserPreferences({ activeBackend: backend });
    set((s) => ({ aiStatus: s.aiStatus ? { ...s.aiStatus, backend } : null }));
    await get().fetchAIStatus();
  },
  setExperienceMode: (mode) => {
    saveUserPreferences({ experienceMode: mode });
    set({ experienceMode: mode });
  },
  updateAdvancedAISettings: (patch) => {
    set((s) => {
      const advancedAISettings = { ...s.advancedAISettings, ...patch };
      saveUserPreferences({ advancedAISettings });
      return { advancedAISettings };
    });
  },
  approveComputerControl: async () => {
    const request = get().computerControlRequest;
    if (!request) return;

    const controller = new AbortController();
    let controlToken: string | null = null;
    let previousPermissions: PermissionState | null = null;
    set((s) => {
      const next = s.messages.map((message) => (
        message.id === request.assistantMessageId
          ? {
              ...message,
              content: 'ComputerMax activado. Ejecutando la tarea en tu equipo.',
              status: 'sending' as const,
              thinkingStartedAt: message.thinkingStartedAt ?? Date.now(),
            }
          : message
      ));
      saveMessages(next);
      return {
        messages: next,
        computerControlRequest: null,
        computerControlActive: true,
        computerControlStartedAt: Date.now(),
        computerControlCommand: request.command,
        pendingComputerControlMessageId: request.assistantMessageId,
        chatAbortController: controller,
        isTyping: true,
        agentState: 'working',
        showOverlay: true,
        isMinimized: true,
      };
    });

    try {
      const token = get().ipcToken;
      if (!token) {
        throw new Error('Las herramientas reales solo estan disponibles en la ventana de escritorio Tauri. En el navegador no puedo mover el mouse ni controlar el sistema sin fingirlo.');
      }
      controlToken = token;
      const currentPermissions = await getDesktopPermissions(token);
      previousPermissions = currentPermissions.success && currentPermissions.data ? currentPermissions.data : null;
      const granted = await setDesktopPermissions(token, {
        screenCaptureEnabled: true,
        automationEnabled: true,
        mouseControlEnabled: true,
        keyboardControlEnabled: true,
      });
      if (!granted.success) {
        throw new Error(granted.error || 'No se pudieron activar los permisos de automatizacion.');
      }
      const result = await executeLocalComputerTask(request.command, token, controller.signal, set);
      if (!result?.taskId) throw new Error('No se pudo crear la tarea autonoma.');

      const assistantMessage = get().messages.find(message => message.id === request.assistantMessageId);
      await revealAssistantMessage(
        request.assistantMessageId,
        result.content,
        {
          source: result.source,
          taskId: result.taskId,
          taskStatus: 'completed',
          thinkingElapsedMs: Date.now() - (assistantMessage?.thinkingStartedAt ?? assistantMessage?.timestamp ?? Date.now()),
          thinkingSummary: prepareVisibleThinking(normalizeThinkingSummary(result.thinkingCore), true),
        },
        controller.signal,
        set,
      );
      if (controlToken && previousPermissions) {
        await setDesktopPermissions(controlToken, previousPermissions).catch(() => {});
      }
      set((s) => ({
        currentTaskId: result.taskId || s.currentTaskId,
        chatAbortController: null,
        isTyping: false,
        agentState: 'completed',
        showOverlay: false,
        isMinimized: false,
        computerControlActive: false,
        computerControlStartedAt: null,
        computerControlCommand: null,
        pendingComputerControlMessageId: null,
      }));
    } catch (error: any) {
      if (controlToken && previousPermissions) {
        await setDesktopPermissions(controlToken, previousPermissions).catch(() => {});
      }
      const messageText = `No pude iniciar el control de la computadora.\n\n${error?.message || String(error)}`;
      set((s) => {
        const next = s.messages.map((message) => (
          message.id === request.assistantMessageId
            ? {
                ...message,
                content: messageText,
                status: 'error' as const,
                error: messageText,
              }
            : message
        ));
        saveMessages(next);
        return {
          messages: next,
          computerControlActive: false,
          computerControlStartedAt: null,
          computerControlCommand: null,
          pendingComputerControlMessageId: null,
          chatAbortController: null,
          isTyping: false,
          agentState: 'idle',
          showOverlay: false,
          isMinimized: false,
        };
      });
    }
  },
  denyComputerControl: () => {
    const request = get().computerControlRequest;
    set((s) => {
      const next = request
        ? s.messages.map((message) => (
            message.id === request.assistantMessageId
              ? { ...message, content: 'No ejecuté la tarea porque no se concedió permiso para controlar la computadora.', status: 'cancelled' as const }
              : message
          ))
        : s.messages;
      saveMessages(next);
      return {
        messages: next,
        computerControlRequest: null,
        computerControlCommand: null,
        pendingComputerControlMessageId: null,
      };
    });
  },
  setMinimized: (v) => set({ isMinimized: v }),
  confirmElement: (_taskId, _confirmed) => set({ learningPrompt: null }),
  dismissLearningPrompt: () => set({ learningPrompt: null }),
  setRecording: (v) => set({ isRecording: v }),

  fetchLMStudioModels: async () => {
    // Fetch directly from LM Studio's OpenAI-compatible endpoint so the model
    // list works with just the GUI + LM Studio (no Python core required).
    try {
      const resp = await fetch('http://127.0.0.1:1234/v1/models');
      if (!resp.ok) return;
      const data = await resp.json();
      const models: string[] = (data.data ?? [])
        .map((m: any) => m.id)
        .filter((id: string) => Boolean(id) && !id.includes('embed'));
      set({ lmstudioModels: models });
      // Auto-select the first chat model if the user hasn't picked one yet.
      if (models.length > 0 && !get().selectedModel) {
        set({ selectedModel: models[0] });
      }
      // Keep aiStatus in sync for has_loaded_model / urgent states
      const currentAi = get().aiStatus;
      if (currentAi) {
        set({
          aiStatus: {
            ...currentAi,
            models,
            has_loaded_model: models.length > 0,
            health: { ...currentAi.health, ok: models.length > 0, models },
          },
        });
      }
    } catch {}
  },

  setSelectedModel: (model) => {
    saveUserPreferences({ selectedModel: model });
    set({ selectedModel: model });
  },

  // Shell/terminal support removed from main UI (all logs & diagnostics belong in Recovery)
  runShellCommand: async (_cmd: string) => {
    return 'Terminal y logs de shell están disponibles exclusivamente en la ventana de Recovery.';
  },

  finishComputerControl: async () => {
    set({
      computerControlActive: false,
      computerControlStartedAt: null,
      computerControlCommand: null,
      computerControlRequest: null,
      pendingComputerControlMessageId: null,
      isMinimized: false,
      showOverlay: false,
    });
    const { ipcToken } = get();
    if (ipcToken) {
      try { await invoke('hide_hud_overlay', { token: ipcToken }); } catch {}
    }
  },

  fetchStatus: async () => {
    try {
      const { ipcToken } = get();
      let data: any = null;
      if (ipcToken) {
        data = await invoke<any>('get_system_status', { token: ipcToken });
      } else {
        const res = await apiFetch('/api/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        data = await res.json();
      }
      set({ systemStatus: data });
    } catch {}
  },

  fetchToolDiagnostics: async () => {
    try {
      const res = await apiFetch('/api/tools');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ toolDiagnostics: data });
    } catch (error) {
      set((s) => ({
        toolDiagnostics: s.toolDiagnostics ?? {
          catalog: { count: 0, valid: false, categories: {}, issues: [{ code: 'tools.offline', message: String(error) }] },
        },
      }));
    }
  },

  runToolsDoctor: async () => {
    try {
      const res = await apiFetch('/api/tools/doctor');
      const data = await res.json();
      set({ toolDoctorReport: data });
      await get().fetchToolDiagnostics();
    } catch (error) {
      set({ toolDoctorReport: { ok: false, error: String(error) } });
    }
  },

  runToolsDryTest: async () => {
    try {
      const res = await apiFetch('/api/tools/test', {
        method: 'POST',
        body: JSON.stringify({ safe: true, integration: false }),
      });
      const data = await res.json();
      set({ toolDoctorReport: data });
      await get().fetchToolDiagnostics();
    } catch (error) {
      set({ toolDoctorReport: { ok: false, error: String(error) } });
    }
  },

  fetchAIStatus: async () => {
    try {
      const res = await apiFetch('/api/ai/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ aiStatus: data });
    } catch {}
  },

  _handleEvent: (event) => {
    const { topic, payload } = event;
    set(state => {
      let next: Partial<AgentStore> = {};

      if (topic === 'task.thinking_ready' && payload) {
        const taskId = payload.task_id as string | undefined;
        const nextMsgs = _updateActiveTaskMsg(state.messages, taskId || state.currentTaskId, {
          thinkingSummary: normalizeThinkingSummary(payload),
        });
        saveMessages(nextMsgs);
        next = { ...next, messages: nextMsgs };
      }
      else if (topic === 'task.step_started' && payload) {
        const step: StepLog = {
          stepNumber: payload.step as number,
          total: payload.total as number,
          description: payload.description as string,
          timestamp: Date.now()
        };
        const nextMsgs = _updateActiveTaskMsg(state.messages, state.currentTaskId, {
          taskStatus: 'executing',
          taskSteps: [...(_getActiveTaskMsg(state.messages, state.currentTaskId)?.taskSteps ?? []), step]
        });
        saveMessages(nextMsgs);
        next = { ...next, stepLogs: [...state.stepLogs, step], messages: nextMsgs };
      }
      else if (topic === 'task.completed') {
        const completedTaskId = payload?.task_id as string | null || state.currentTaskId;
        const summary = typeof payload?.result_summary === 'string' ? payload.result_summary.trim() : '';
        const active = _getActiveTaskMsg(state.messages, completedTaskId);
        const content = summary
          ? `Tarea completada.\n\n${summary}`
          : (active?.content || 'Tarea completada y validada.');
        const nextMsgs = _updateActiveTaskMsg(state.messages, completedTaskId, {
          content,
          taskStatus: 'completed',
          status: 'done',
        });
        saveMessages(nextMsgs);
        next = {
          ...next,
          agentState: 'completed',
          messages: nextMsgs,
          computerControlActive: false,
          computerControlStartedAt: null,
          computerControlCommand: null,
          computerControlRequest: null,
          pendingComputerControlMessageId: null,
          showOverlay: false,
          isMinimized: false,
        };
        setTimeout(() => set({ agentState: 'idle' }), 2000);
      }
      else if (topic === 'task.failed' && payload) {
        const error = typeof payload.error === 'string' ? payload.error : 'La tarea fallo durante la ejecucion.';
        const nextMsgs = _updateActiveTaskMsg(state.messages, payload.task_id as string | null || state.currentTaskId, {
          content: `No pude completar la tarea.\n\n${error}`,
          taskStatus: 'failed',
          status: 'error',
          error,
        });
        saveMessages(nextMsgs);
        next = {
          ...next,
          agentState: 'idle',
          messages: nextMsgs,
          computerControlActive: false,
          computerControlStartedAt: null,
          computerControlCommand: null,
          computerControlRequest: null,
          pendingComputerControlMessageId: null,
          showOverlay: false,
          isMinimized: false,
        };
      }
      else if (topic === 'task.step_retrying' && payload) {
        // (terminal output removed — diagnostics live in Recovery)
      }
      else if (topic === 'task.execution_reflection' && payload) {
        const thinkingSummary = normalizeThinkingSummary(payload.thinking_core);
        const reflectedTaskId = (payload.task_id as string | undefined) || state.currentTaskId;
        const nextMsgs = _updateActiveTaskMsg(state.messages, reflectedTaskId, {
          thinkingSummary: thinkingSummary ?? _getActiveTaskMsg(state.messages, state.currentTaskId)?.thinkingSummary,
        });
        saveMessages(nextMsgs);
        next = {
          ...next,
          messages: nextMsgs,
        };
      }
      else if (topic.startsWith('tool.') && payload) {
        const toolId = String(payload.tool_id || payload.tool || 'tool');
        const status = topic.replace('tool.', '');
        const error = typeof payload.error === 'string' ? payload.error : '';
        next = {
          ...next,
          toolDiagnostics: state.toolDiagnostics
            ? {
                ...state.toolDiagnostics,
                input_monitor: state.toolDiagnostics.input_monitor,
                runtime: state.toolDiagnostics.runtime,
              }
            : state.toolDiagnostics,
        };
        // (tool log lines removed from main store — see Recovery for full real logs)
      }

      return next;
    });
  }
}));

// ── Tool executor ─────────────────────────────────────────────────────────────

// Returns a short result string for the feedback loop, or '' if no meaningful output
async function _executeTool(
  name: string,
  params: Record<string, string>,
  token: string | null,
  set: (patch: any) => void,
  get: () => any,
): Promise<string> {
  const addSystemMsg = (text: string) => {
    const msg: ChatMessage = {
      id: `sys-${Date.now()}`,
      role: 'assistant',
      content: text,
      timestamp: Date.now(),
      status: 'done',
      source: 'local',
    };
    set((s: any) => {
      const next = [...s.messages, msg];
      saveMessages(next);
      return { messages: next };
    });
  };

  try {
    switch (name) {
      case 'SANDBOX': {
        const cmd = params['0'] ?? Object.values(params).join(', ');
        if (!cmd) return 'ERROR: empty command';
        const result = await invoke<any>('run_shell_command', { cmd, token });
        const stdout = (result.stdout || '').trim();
        const stderr = (result.stderr || '').trim();
        const out = stdout || stderr || '(no output)';
        const rc = result.returncode ?? result.exit_code ?? 0;
        const status = rc === 0 ? 'SUCCESS' : `ERROR (exit ${rc})`;
        addSystemMsg(`**Terminal** \`${cmd}\`\n\`\`\`\n${out.slice(0, 1500)}\n\`\`\``);
        return `${status}: ${out.slice(0, 300)}`;
      }
      case 'SCREENSHOT': {
        const b64 = await invoke<string>('screenshot_base64', { token });
        addSystemMsg(`**Pantalla capturada** (${Math.round(b64.length / 1024)} KB)`);
        return `Screenshot captured (${Math.round(b64.length / 1024)} KB)`;
      }
      case 'SCREEN_COLORS': {
        const colors = await invoke<string>('capture_screen_colors', { token });
        addSystemMsg(`**Mapa de pantalla:**\n\`\`\`\n${colors}\n\`\`\``);
        return `Screen color map: ${colors.slice(0, 200)}`;
      }
      case 'WINDOW_INFO': {
        const info = await invoke<string>('get_focused_window_info', { token });
        addSystemMsg(`**Ventana activa:** ${info}`);
        return `Active window: ${info}`;
      }
      case 'MOUSE_MOVE': {
        const x = parseInt(params.x ?? '', 10);
        const y = parseInt(params.y ?? '', 10);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          const err = `ERROR: MOUSE_MOVE necesita enteros x e y. Got x=${params.x} y=${params.y}. Usa UIAutomation PowerShell para encontrar las coordenadas reales primero.`;
          addSystemMsg(`**Mouse move fallido:** coordenadas inválidas (x=${params.x}, y=${params.y})`);
          return err;
        }
        await invoke('inject_mouse_move', { token, x, y });
        addSystemMsg(`**Mouse movido** a (${x}, ${y})`);
        return `Mouse moved to (${x}, ${y})`;
      }
      case 'MOUSE_CLICK': {
        const x = parseInt(params.x ?? '', 10);
        const y = parseInt(params.y ?? '', 10);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
          const err = `ERROR: MOUSE_CLICK necesita enteros x e y. Got x=${params.x} y=${params.y}. Usa UIAutomation PowerShell para encontrar las coordenadas reales primero.`;
          addSystemMsg(`**Mouse click fallido:** coordenadas inválidas (x=${params.x}, y=${params.y})`);
          return err;
        }
        const button = params.button ?? 'left';
        if (button === 'double') {
          await invoke('inject_input', { token, inputType: 'click', x, y, button: 'left' });
          await new Promise(r => setTimeout(r, 80));
          await invoke('inject_input', { token, inputType: 'click', x, y, button: 'left' });
          addSystemMsg(`**Doble click** en (${x}, ${y})`);
        } else {
          await invoke('inject_input', { token, inputType: 'click', x, y, button });
          addSystemMsg(`**Click ${button}** en (${x}, ${y})`);
        }
        return `Clicked (${x}, ${y}) button=${button}`;
      }
      case 'KEY_TYPE': {
        const text = params.text ?? Object.values(params).join(' ');
        await invoke('type_text', { token, text });
        addSystemMsg(`**Texto escrito:** "${text.slice(0, 60)}"`);
        return `Typed: "${text.slice(0, 60)}"`;
      }
      case 'KEY_PRESS': {
        const vk = parseInt(params.vk ?? '13', 10);
        await invoke('inject_input', { token, inputType: 'key', vkCode: vk, isDown: true });
        await new Promise(r => setTimeout(r, 30));
        await invoke('inject_input', { token, inputType: 'key', vkCode: vk, isDown: false });
        addSystemMsg(`**Tecla presionada:** VK=${vk}`);
        return `Key pressed: VK=${vk}`;
      }
      default:
        return '';
    }
  } catch (err: any) {
    const errMsg = err?.message ?? String(err);
    const msg: ChatMessage = {
      id: `err-${Date.now()}`,
      role: 'assistant',
      content: `**Error ejecutando ${name}:** ${errMsg}`,
      timestamp: Date.now(),
      status: 'error',
      source: 'local',
    };
    set((s: any) => {
      const next = [...s.messages, msg];
      saveMessages(next);
      return { messages: next };
    });
    return `ERROR: ${errMsg}`;
  }
}

// ── Widget broadcast (syncs state to the mini widget window) ──────────────────

function localThinkingCore(command: string, checklist: ThinkingSummary['checklist'], tokenUsage = 0): ThinkingSummary {
  return {
    intent: 'local_desktop_tool_execution',
    reasoningDepth: 'controlled_tool_use',
    confidenceScore: 0.86,
    riskScore: 0.22,
    checklistScore: checklist?.every(item => item.status === 'done') ? 1 : 0.75,
    checklist,
    tokenUsage,
    suggestedTools: command.toLowerCase().includes('pantalla') ? ['screenshot_base64'] : ['inject_mouse_move'],
    showChecklist: true,
    showThinkingPanel: true,
  };
}

async function executeLocalComputerTask(
  command: string,
  token: string,
  signal: AbortSignal,
  set: (patch: any) => void,
): Promise<ChatResult> {
  const taskId = `local-${Date.now()}`;
  const checklist: NonNullable<ThinkingSummary['checklist']> = [
    { id: 'permission', label: 'ComputerMax autorizado por el usuario', status: 'done' },
    { id: 'screen', label: 'Resolucion real leida desde Tauri', status: 'done' },
    { id: 'tool', label: 'Herramienta local seleccionada', status: 'done' },
  ];

  const addSystemMsg = (content: string) => {
    const msg: ChatMessage = {
      id: `local-tool-${Date.now()}`,
      role: 'assistant',
      content,
      timestamp: Date.now(),
      status: 'done',
      source: 'local',
      thinkingSummary: { intent: 'tool_result', showChecklist: false, showThinkingPanel: true },
    };
    set((s: AgentStore) => {
      const next = [...s.messages, msg];
      saveMessages(next);
      return { messages: next };
    });
  };

  if (signal.aborted) throw new Error('Tarea cancelada antes de ejecutar ComputerMax.');
  const screen = await fetchScreenInfo(token);

  if (isMouseCommand(command)) {
    const move = await runMouseMoveTool(token, command, screen);
    checklist.push({
      id: 'result',
      label: `Mouse en (${move.finalPoint.x}, ${move.finalPoint.y})`,
      status: 'done',
    });
    addSystemMsg(`**ComputerMax:** mouse movido a \`${move.finalPoint.x}, ${move.finalPoint.y}\`.`);
    return {
      content: `Listo. ComputerMax movió el mouse a (${move.finalPoint.x}, ${move.finalPoint.y}). Puedes pedir posiciones como "centro", "esquina superior derecha" o coordenadas x=500, y=300.`,
      source: 'local',
      taskId,
      thinkingCore: localThinkingCore(command, checklist),
    };
  }

  if (isScreenshotCommand(command)) {
    const shot = await runScreenshotTool(token, screen);
    const kb = Math.round(shot.imageBase64!.length / 1024);
    checklist.push({ id: 'result', label: `Screenshot capturado (${kb} KB)`, status: 'done' });
    addSystemMsg(`**ComputerMax:** captura de pantalla (${kb} KB, ${shot.width}x${shot.height}).`);
    return {
      content: `Listo. ComputerMax capturó la pantalla (${shot.width}x${shot.height}, ~${kb} KB).`,
      source: 'local',
      taskId,
      thinkingCore: localThinkingCore(command, checklist),
    };
  }

  checklist.push({ id: 'result', label: 'No hay herramienta local segura para esa acción', status: 'blocked' });
  return {
    content: 'No ejecuté esa acción porque aún no tengo una herramienta local segura para hacerla. Prueba: "mueve el mouse al centro" o "captura pantalla".',
    source: 'local',
    taskId,
    thinkingCore: localThinkingCore(command, checklist),
  };
}

let _widgetBc: BroadcastChannel | null = null;
try { _widgetBc = new BroadcastChannel('AgentMax'); } catch {}

// Cleanup broadcast channel on unload
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    _widgetBc?.close();
  });
}

useAgentStore.subscribe((state, prev) => {
  if (!_widgetBc) return;
  if (
    state.agentState !== prev.agentState ||
    state.isTyping !== prev.isTyping ||
    state.messages.length !== prev.messages.length
  ) {
    const lastUserMsg = [...state.messages].reverse().find(m => m.role === 'user')?.content ?? '';
    _widgetBc.postMessage({
      agentState: state.agentState,
      isTyping: state.isTyping,
      lastUserMsg,
      msgCount: state.messages.length,
    });

    const { ipcToken } = useAgentStore.getState();
    // Only show widget when a real automation task is executing (not during plain chat)
    const taskRunning = (state.agentState === 'working' || state.agentState === 'validating') && !!state.currentTaskId;
    const wasTaskRunning = (prev.agentState === 'working' || prev.agentState === 'validating') && !!prev.currentTaskId;
    if (taskRunning) {
      if (ipcToken) invoke('show_widget', { token: ipcToken }).catch(() => {});
    } else if (wasTaskRunning && !taskRunning) {
      setTimeout(() => {
        if (ipcToken) invoke('hide_widget', { token: ipcToken }).catch(() => {});
      }, 3000);
    }
  }
});
