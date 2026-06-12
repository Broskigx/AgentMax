import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Clipboard,
  Download,
  Eraser,
  Filter,
  Play,
  RefreshCcw,
  Search,
  Square,
  Terminal,
  TriangleAlert,
} from 'lucide-react';
import {
  clearRecoveryView,
  exportRecoveryBundle,
  getLlamaCppStatus,
  getRecoveryLogs,
  getRecoveryStatus,
  onLlamaCppStatus,
  onRecoveryLog,
  startLlamaCpp,
  stopLlamaCpp,
  type LlamaCppStatus,
  type RecoveryLogRecord,
  type RecoveryStatus,
} from '../../lib/recoveryService';
import './RecoveryTestWindow.css';

const severityOrder = ['all', 'error', 'warn', 'info', 'debug', 'trace'] as const;
type SeverityFilter = (typeof severityOrder)[number];

function levelClass(level: string) {
  const normalized = level.toLowerCase();
  if (normalized === 'error') return 'recovery-line--error';
  if (normalized === 'warn') return 'recovery-line--warn';
  if (normalized === 'debug' || normalized === 'trace') return 'recovery-line--debug';
  return 'recovery-line--info';
}

function formatTime(ts: number) {
  return new Date(ts).toLocaleTimeString('es-ES', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function stringifyDetails(details: unknown) {
  if (!details) return '';
  try {
    return JSON.stringify(details, null, 2);
  } catch {
    return String(details);
  }
}

export function RecoveryTestWindow() {
  const [logs, setLogs] = useState<RecoveryLogRecord[]>([]);
  const [status, setStatus] = useState<RecoveryStatus | null>(null);
  const [llamaStatus, setLlamaStatus] = useState<LlamaCppStatus | null>(null);
  const [severity, setSeverity] = useState<SeverityFilter>('all');
  const [source, setSource] = useState('all');
  const [query, setQuery] = useState('');
  const [follow, setFollow] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    const [nextLogs, nextStatus, nextLlama] = await Promise.allSettled([
      getRecoveryLogs(),
      getRecoveryStatus(),
      getLlamaCppStatus(),
    ]);
    if (nextLogs.status === 'fulfilled') setLogs(nextLogs.value);
    if (nextStatus.status === 'fulfilled') setStatus(nextStatus.value);
    if (nextLlama.status === 'fulfilled') setLlamaStatus(nextLlama.value);
  }, []);

  useEffect(() => {
    void refresh();
    let unlistenRecovery: (() => void) | undefined;
    let unlistenLlama: (() => void) | undefined;
    void onRecoveryLog((record) => {
      setLogs((current) => [...current, record].slice(-2000));
      setStatus((current) => current ? { ...current, recordCount: current.recordCount + 1 } : current);
    }).then((unlisten) => {
      unlistenRecovery = unlisten;
    });
    void onLlamaCppStatus((payload) => {
      setLlamaStatus(payload);
    }).then((unlisten) => {
      unlistenLlama = unlisten;
    });
    const id = window.setInterval(refresh, 5000);
    return () => {
      window.clearInterval(id);
      unlistenRecovery?.();
      unlistenLlama?.();
    };
  }, [refresh]);

  useEffect(() => {
    if (follow) endRef.current?.scrollIntoView({ block: 'end' });
  }, [follow, logs.length]);

  const sources = useMemo(() => {
    const unique = Array.from(new Set(logs.map((line) => line.source))).sort();
    return ['all', ...unique];
  }, [logs]);

  const filteredLogs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return logs.filter((line) => {
      if (severity !== 'all' && line.level.toLowerCase() !== severity) return false;
      if (source !== 'all' && line.source !== source) return false;
      if (!normalizedQuery) return true;
      return `${line.source} ${line.level} ${line.message} ${stringifyDetails(line.details)}`
        .toLowerCase()
        .includes(normalizedQuery);
    });
  }, [logs, query, severity, source]);

  const selectedLog = filteredLogs.find((line) => line.id === selectedId) ?? null;
  const errorCount = logs.filter((line) => line.level.toLowerCase() === 'error').length;
  const warnCount = logs.filter((line) => line.level.toLowerCase() === 'warn').length;

  const copyVisible = useCallback(async () => {
    const text = filteredLogs
      .map((line) => `[${formatTime(line.ts)}] ${line.level.toUpperCase()} ${line.source}: ${line.message}`)
      .join('\n');
    await navigator.clipboard.writeText(text);
    setActionMessage(`Copied ${filteredLogs.length} visible lines`);
  }, [filteredLogs]);

  const clearView = useCallback(async () => {
    await clearRecoveryView();
    await refresh();
    setActionMessage('Recovery Test view cleared');
  }, [refresh]);

  const exportBundle = useCallback(async () => {
    const path = await exportRecoveryBundle();
    setActionMessage(`Exported: ${path}`);
  }, []);

  const startGguf = useCallback(async () => {
    const next = await startLlamaCpp();
    setLlamaStatus(next);
    setActionMessage(next.ready ? 'llama.cpp GGUF backend is ready' : next.error || 'llama.cpp start requested');
  }, []);

  const stopGguf = useCallback(async () => {
    const next = await stopLlamaCpp();
    setLlamaStatus(next);
    setActionMessage('llama.cpp sidecar stopped');
  }, []);

  return (
    <main className="recovery-root">
      <aside className="recovery-sidebar" aria-label="Recovery Test status">
        <div className="recovery-brand">
          <Terminal size={22} />
          <div>
            <strong>AgentMax Recovery</strong>
            <span>Real runtime logs • crash diagnostics • GGUF sidecar</span>
          </div>
        </div>

        <section className="recovery-status-grid">
          <div>
            <Activity size={16} />
            <span>Total logs</span>
            <strong>{logs.length}</strong>
          </div>
          <div>
            <TriangleAlert size={16} />
            <span>Errors</span>
            <strong>{errorCount}</strong>
          </div>
          <div>
            <Filter size={16} />
            <span>Warnings</span>
            <strong>{warnCount}</strong>
          </div>
          <div>
            <Terminal size={16} />
            <span>Dirty start</span>
            <strong>{status?.dirtyStartDetected ? 'Yes' : 'No'}</strong>
          </div>
        </section>

        <section className="recovery-panel">
          <div className="recovery-panel-heading">
            <span>GGUF backend</span>
            <strong>{llamaStatus?.ready ? 'Ready' : llamaStatus?.running ? 'Starting' : 'Stopped'}</strong>
          </div>
          <div className="recovery-kv">
            <span>URL</span>
            <code>{llamaStatus?.baseUrl || 'http://127.0.0.1:8080/v1'}</code>
          </div>
          <div className="recovery-kv">
            <span>Model</span>
            <code>{llamaStatus?.models?.[0] || llamaStatus?.modelPath || 'AGENTMAX_GGUF_MODEL_PATH not set'}</code>
          </div>
          {llamaStatus?.error ? <p className="recovery-error-text">{llamaStatus.error}</p> : null}
          <div className="recovery-button-row">
            <button onClick={startGguf} title="Start llama.cpp sidecar">
              <Play size={15} />
              Start
            </button>
            <button onClick={stopGguf} title="Stop llama.cpp sidecar">
              <Square size={15} />
              Stop
            </button>
          </div>
        </section>

        <section className="recovery-panel">
          <div className="recovery-panel-heading">
            <span>Log storage</span>
            <strong>{status?.configured ? 'Active' : 'Pending'}</strong>
          </div>
          <code className="recovery-path">{status?.logPath || 'Waiting for Tauri log path'}</code>
        </section>
      </aside>

      <section className="recovery-console">
        <header className="recovery-toolbar">
          <label className="recovery-search">
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              placeholder="Search logs, sources, stack traces"
            />
          </label>
          <select value={severity} onChange={(event) => setSeverity(event.currentTarget.value as SeverityFilter)}>
            {severityOrder.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <select value={source} onChange={(event) => setSource(event.currentTarget.value)}>
            {sources.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <button onClick={refresh} title="Refresh">
            <RefreshCcw size={16} />
          </button>
          <button onClick={copyVisible} title="Copy visible logs">
            <Clipboard size={16} />
          </button>
          <button onClick={exportBundle} title="Export diagnostics bundle">
            <Download size={16} />
          </button>
          <button onClick={clearView} title="Clear view">
            <Eraser size={16} />
          </button>
        </header>

        <div className="recovery-command-line" aria-label="Read only command line">
          <span>$</span>
          <code>recovery-test --follow --read-only</code>
          <label>
            <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.currentTarget.checked)} />
            follow
          </label>
        </div>

        <div className="recovery-log-list" role="log" aria-live="polite">
          {filteredLogs.length === 0 ? (
            <div className="recovery-empty">
              <Terminal size={28} />
              <strong>No matching logs</strong>
              <span>Real logs from Tauri, Python backend, LM Studio calls and panics appear here automatically.</span>
            </div>
          ) : filteredLogs.map((line) => (
            <button
              key={line.id}
              className={`recovery-line ${levelClass(line.level)} ${selectedId === line.id ? 'recovery-line--selected' : ''}`}
              onClick={() => setSelectedId((current) => current === line.id ? null : line.id)}
            >
              <span className="recovery-line-time">{formatTime(line.ts)}</span>
              <span className="recovery-line-level">{line.level}</span>
              <span className="recovery-line-source">{line.source}</span>
              <span className="recovery-line-message">{line.message}</span>
            </button>
          ))}
          <div ref={endRef} />
        </div>

        <footer className="recovery-details">
          {selectedLog ? (
            <>
              <strong>{selectedLog.source}</strong>
              <span>{selectedLog.message}</span>
              {selectedLog.details ? <pre>{stringifyDetails(selectedLog.details)}</pre> : null}
            </>
          ) : (
            <span>{actionMessage || `${filteredLogs.length} visible lines. Select a line to inspect details.`}</span>
          )}
        </footer>
      </section>
    </main>
  );
}
