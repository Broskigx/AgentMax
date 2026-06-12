import { useEffect, useMemo, useState } from 'react';
import { useAgentStore } from '../../store/agentStore';
import './AgentControlCursor.css';

interface CursorTargetDetail {
  x?: number;
  y?: number;
  label?: string;
}

function formatElapsed(startedAt: number | null) {
  if (!startedAt) return '00:00';
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  const ss = String(seconds % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

function shortCommand(command: string | null) {
  if (!command) return 'ComputerMax activo';
  return command.length > 58 ? `${command.slice(0, 58)}...` : command;
}

export function AgentControlCursor() {
  const {
    computerControlActive,
    computerControlStartedAt,
    computerControlCommand,
    agentState,
    stopAI,
  } = useAgentStore();
  const [cursor, setCursor] = useState(() => ({
    x: Math.max(80, Math.round((globalThis.innerWidth || 900) * 0.62)),
    y: Math.max(80, Math.round((globalThis.innerHeight || 700) * 0.34)),
    label: 'ComputerMax',
  }));
  const [elapsed, setElapsed] = useState(() => formatElapsed(computerControlStartedAt));

  useEffect(() => {
    document.documentElement.classList.toggle('agent-control-active', computerControlActive);
    return () => document.documentElement.classList.remove('agent-control-active');
  }, [computerControlActive]);

  useEffect(() => {
    if (!computerControlActive) return undefined;
    const id = window.setInterval(() => setElapsed(formatElapsed(computerControlStartedAt)), 500);
    setElapsed(formatElapsed(computerControlStartedAt));
    return () => window.clearInterval(id);
  }, [computerControlActive, computerControlStartedAt]);

  useEffect(() => {
    const onTarget = (event: Event) => {
      const detail = (event as CustomEvent<CursorTargetDetail>).detail ?? {};
      setCursor((current) => ({
        x: typeof detail.x === 'number' ? detail.x : current.x,
        y: typeof detail.y === 'number' ? detail.y : current.y,
        label: detail.label || current.label,
      }));
    };
    window.addEventListener('AgentMax:agent-cursor-target', onTarget);
    return () => window.removeEventListener('AgentMax:agent-cursor-target', onTarget);
  }, []);

  const command = useMemo(() => shortCommand(computerControlCommand), [computerControlCommand]);

  if (!computerControlActive) return null;

  return (
    <>
      <div
        className="agent-control-cursor"
        style={{ left: cursor.x, top: cursor.y }}
        aria-hidden="true"
      >
        <div className="agent-control-cursor__mark" />
        <div className="agent-control-cursor__label">
          <span>{cursor.label}</span>
        </div>
      </div>

      <section className="agent-control-hud" aria-live="polite" aria-label="ComputerMax activo">
        <div className="agent-control-hud__pulse" />
        <div className="agent-control-hud__copy">
          <strong>ComputerMax</strong>
          <span>{command}</span>
        </div>
        <div className="agent-control-hud__meta">
          <span>{agentState === 'working' ? 'Ejecutando' : 'Activo'}</span>
          <span>{elapsed}</span>
        </div>
        <button type="button" onClick={() => void stopAI()}>
          Detener
        </button>
      </section>
    </>
  );
}
