import type { ChatMessage } from '../types';

export interface ChatSessionMeta {
  id: string;
  title: string;
  preview: string;
  updatedAt: number;
}

interface SessionsStore {
  activeId: string;
  sessions: ChatSessionMeta[];
  data: Record<string, ChatMessage[]>;
}

const STORE_KEY = 'AgentMax.sessions.v1';

function newSessionId() {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  return `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function deriveTitle(messages: ChatMessage[]) {
  const firstUser = messages.find((m) => m.role === 'user' && m.content.trim());
  if (!firstUser) return 'Nuevo chat';
  const text = firstUser.content.trim();
  return text.length > 44 ? `${text.slice(0, 44)}…` : text;
}

function derivePreview(messages: ChatMessage[]) {
  const last = [...messages].reverse().find((m) => (m.role === 'user' || m.role === 'assistant') && m.content.trim());
  if (!last) return 'Sin mensajes';
  const text = last.content.trim();
  return text.length > 56 ? `${text.slice(0, 56)}…` : text;
}

function buildMeta(id: string, messages: ChatMessage[]): ChatSessionMeta {
  return {
    id,
    title: deriveTitle(messages),
    preview: derivePreview(messages),
    updatedAt: messages[messages.length - 1]?.timestamp ?? Date.now(),
  };
}

function emptyStore(): SessionsStore {
  const id = newSessionId();
  return {
    activeId: id,
    sessions: [{ id, title: 'Nuevo chat', preview: 'Sin mensajes', updatedAt: Date.now() }],
    data: { [id]: [] },
  };
}

export function loadSessionsStore(fallbackMessages: ChatMessage[] = []): SessionsStore {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as SessionsStore;
      if (parsed?.activeId && parsed.sessions?.length && parsed.data) {
        return parsed;
      }
    }
  } catch {
    // fall through to migration
  }

  const legacy = fallbackMessages.length ? fallbackMessages : [];
  const store = emptyStore();
  store.data[store.activeId] = legacy;
  store.sessions[0] = buildMeta(store.activeId, legacy);
  saveSessionsStore(store);
  return store;
}

export function saveSessionsStore(store: SessionsStore) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    // ignore quota errors
  }
}

export function upsertSessionMessages(
  store: SessionsStore,
  sessionId: string,
  messages: ChatMessage[],
): SessionsStore {
  const data = { ...store.data, [sessionId]: messages };
  const meta = buildMeta(sessionId, messages);
  const sessions = store.sessions.some((s) => s.id === sessionId)
    ? store.sessions.map((s) => (s.id === sessionId ? meta : s))
    : [meta, ...store.sessions];
  return {
    ...store,
    data,
    sessions: sessions.sort((a, b) => b.updatedAt - a.updatedAt),
  };
}

export function createSession(store: SessionsStore): SessionsStore {
  const id = newSessionId();
  const meta: ChatSessionMeta = {
    id,
    title: 'Nuevo chat',
    preview: 'Sin mensajes',
    updatedAt: Date.now(),
  };
  return {
    activeId: id,
    sessions: [meta, ...store.sessions],
    data: { ...store.data, [id]: [] },
  };
}

export function removeSession(store: SessionsStore, sessionId: string): SessionsStore | null {
  if (store.sessions.length <= 1) return null;
  const sessions = store.sessions.filter((s) => s.id !== sessionId);
  const data = { ...store.data };
  delete data[sessionId];
  const activeId = store.activeId === sessionId ? sessions[0].id : store.activeId;
  return { activeId, sessions, data };
}

export function getSessionMessages(store: SessionsStore, sessionId: string) {
  return store.data[sessionId] ?? [];
}

let _cache: SessionsStore | null = null;
let _activeSessionId = '';

export function getSessionsStore() {
  if (!_cache) _cache = loadSessionsStore();
  return _cache;
}

export function setSessionsStore(store: SessionsStore) {
  _cache = store;
  saveSessionsStore(store);
}

export function setActiveSessionId(id: string) {
  _activeSessionId = id;
}

export function getActiveSessionId() {
  return _activeSessionId;
}

export function initSessionsStore(fallbackMessages: ChatMessage[] = []) {
  _cache = loadSessionsStore(fallbackMessages);
  _activeSessionId = _cache.activeId;
  return _cache;
}
