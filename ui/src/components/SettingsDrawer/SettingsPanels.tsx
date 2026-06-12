import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  BrainCircuit,
  CheckCircle2,
  Cpu,
  MonitorCog,
  PauseCircle,
  Play,
  RefreshCcw,
  Server,
  ShieldCheck,
  Square,
  Terminal,
  TriangleAlert,
  Wrench,
} from 'lucide-react';
import { useAgentStore } from '../../store/agentStore';
import {
  getLlamaCppStatus,
  openRecoveryWindow,
  startLlamaCpp,
  stopLlamaCpp,
  type LlamaCppStatus,
} from '../../lib/recoveryService';
import { ToolDiagnosticsPanel } from '../ToolDiagnostics/ToolDiagnosticsPanel';

type BackendChoice = 'AgentMax' | 'lmstudio' | 'llamacpp' | 'local_peft' | 'claude';

const backendChoices: Array<{
  id: BackendChoice;
  label: string;
  description: string;
  badge: string;
}> = [
  { id: 'AgentMax', label: 'AgentMax Local', description: 'Asistente integrado', badge: 'AM' },
  { id: 'llamacpp', label: 'GGUF llama.cpp', description: 'Servidor GGUF local', badge: 'GG' },
  { id: 'lmstudio', label: 'LM Studio', description: 'Servidor externo', badge: 'LM' },
  { id: 'local_peft', label: 'PEFT local', description: 'Adaptador LoRA', badge: 'PF' },
  { id: 'claude', label: 'Cloud API', description: 'Proveedor externo', badge: 'API' },
];

interface SettingsPanelsProps {
  active?: boolean;
  section?: SettingsSection;
}

export type SettingsSection = 'models' | 'diagnostics' | 'safety';

export function SettingsPanels({ active = true, section = 'models' }: SettingsPanelsProps) {
  const {
    ipcToken,
    aiStatus,
    systemStatus,
    toolDiagnostics,
    fetchAIStatus,
    fetchStatus,
    fetchToolDiagnostics,
    switchBackend,
    emergencyStop,
    backendOnline,
  } = useAgentStore();

  const [httpReady, setHttpReady] = useState(false);
  const [llamaStatus, setLlamaStatus] = useState<LlamaCppStatus | null>(null);
  const [busyBackend, setBusyBackend] = useState<BackendChoice | null>(null);
  const [statusMsg, setStatusMsg] = useState('');

  const refresh = useCallback(async () => {
    try {
      const health = await fetch('http://127.0.0.1:7790/health', { cache: 'no-store' });
      setHttpReady(health.ok);
    } catch {
      setHttpReady(false);
    }
    await Promise.allSettled([fetchStatus(), fetchAIStatus(), fetchToolDiagnostics()]);
    const llama = await getLlamaCppStatus().catch(() => null);
    if (llama) setLlamaStatus(llama);
  }, [fetchAIStatus, fetchStatus, fetchToolDiagnostics]);

  useEffect(() => {
    if (!active) return undefined;
    void refresh();
    const id = window.setInterval(refresh, 8000);
    return () => window.clearInterval(id);
  }, [active, refresh]);

  const activeBackend = (aiStatus?.backend || 'AgentMax') as BackendChoice;
  const runtimeOk = backendOnline && (httpReady || systemStatus?.status === 'running');
  const toolsOk = Boolean(toolDiagnostics?.catalog?.valid || toolDiagnostics?.runtime);

  const inspectorItems = useMemo(() => {
    const runtimeHistory = toolDiagnostics?.runtime?.history || [];
    return [
      { icon: <Server size={16} />, label: 'API', value: runtimeOk ? 'online' : 'offline', ok: runtimeOk },
      { icon: <ShieldCheck size={16} />, label: 'Tools', value: toolsOk ? 'valid' : 'pending', ok: toolsOk },
      { icon: <Activity size={16} />, label: 'Runs', value: String(runtimeHistory.length), ok: runtimeHistory.every((item) => item.success !== false) },
      { icon: <BrainCircuit size={16} />, label: 'Backend', value: activeBackend, ok: Boolean(aiStatus?.health?.ok || activeBackend === 'AgentMax' || llamaStatus?.ready) },
    ];
  }, [activeBackend, aiStatus?.health?.ok, llamaStatus?.ready, runtimeOk, toolDiagnostics, toolsOk]);

  const chooseBackend = useCallback(async (backend: BackendChoice) => {
    setBusyBackend(backend);
    setStatusMsg(`Cambiando a ${backend}...`);
    try {
      if (backend === 'llamacpp') {
        const status = await startLlamaCpp();
        setLlamaStatus(status);
        if (!status.ready) setStatusMsg(status.error || 'llama.cpp no está listo');
      }
      await switchBackend(backend);
      await refresh();
      setStatusMsg(`Activo: ${backend}`);
    } catch (error) {
      setStatusMsg(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyBackend(null);
    }
  }, [refresh, switchBackend]);

  const handleLlamaStop = useCallback(async () => {
    setBusyBackend('llamacpp');
    try {
      const status = await stopLlamaCpp();
      setLlamaStatus(status);
      setStatusMsg('llama.cpp detenido');
    } catch (error) {
      setStatusMsg(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyBackend(null);
    }
  }, []);

  return (
    <>
      {statusMsg ? <p className="settings-drawer__status">{statusMsg}</p> : null}

      <section className="settings-drawer__panel" hidden={section !== 'models'}>
        <div className="settings-drawer__panel-head">
          <span>Modelo</span>
          <Wrench size={16} />
        </div>
        <div className="settings-drawer__backends">
          {backendChoices.map((choice) => {
            const isActive = activeBackend === choice.id;
            return (
              <button
                key={choice.id}
                type="button"
                className={`settings-drawer__backend ${isActive ? 'settings-drawer__backend--active' : ''}`}
                onClick={() => chooseBackend(choice.id)}
                disabled={busyBackend !== null}
              >
                <span>{choice.badge}</span>
                <div>
                  <strong>{choice.label}</strong>
                  <small>{choice.description}</small>
                </div>
                {isActive ? <CheckCircle2 size={16} /> : null}
              </button>
            );
          })}
        </div>
      </section>

      <section className="settings-drawer__panel" hidden={section !== 'models'}>
        <div className="settings-drawer__panel-head">
          <span>GGUF</span>
          <Cpu size={16} />
        </div>
        <div className="settings-drawer__gguf">
          <strong>{llamaStatus?.ready ? 'Listo' : llamaStatus?.running ? 'Iniciando' : 'Detenido'}</strong>
          <code>{llamaStatus?.models?.[0] || llamaStatus?.modelPath || 'Sin modelo configurado'}</code>
          {llamaStatus?.error ? (
            <span className="settings-drawer__error">
              <TriangleAlert size={14} />
              {llamaStatus.error}
            </span>
          ) : null}
        </div>
        <div className="settings-drawer__row">
          <button type="button" onClick={() => chooseBackend('llamacpp')} disabled={busyBackend !== null}>
            <Play size={14} /> Iniciar
          </button>
          <button type="button" onClick={handleLlamaStop} disabled={busyBackend !== null}>
            <Square size={14} /> Detener
          </button>
        </div>
      </section>

      <section className="settings-drawer__panel" hidden={section !== 'diagnostics'}>
        <div className="settings-drawer__panel-head">
          <span>Estado</span>
          <Activity size={16} />
        </div>
        <div className="settings-drawer__health">
          {inspectorItems.map((item) => (
            <div key={item.label} className={item.ok ? 'settings-drawer__health-ok' : 'settings-drawer__health-off'}>
              {item.icon}
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
      </section>

      <div hidden={section !== 'diagnostics'}>
        <ToolDiagnosticsPanel ipcToken={ipcToken} />
      </div>

      <section className="settings-drawer__panel settings-drawer__panel--actions" hidden={section !== 'safety'}>
        <div className="settings-drawer__row">
          <button type="button" onClick={() => { void refresh(); }}>
            <RefreshCcw size={14} /> Actualizar
          </button>
          <button type="button" onClick={() => { void openRecoveryWindow(); }}>
            <Terminal size={14} /> Recovery & Logs
          </button>
        </div>
        <button type="button" className="settings-drawer__stop" onClick={emergencyStop}>
          <PauseCircle size={15} /> Detener agente
        </button>
      </section>
    </>
  );
}
