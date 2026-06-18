import { useEffect, useState } from 'react';
import { BrainCircuit, TriangleAlert } from 'lucide-react';
import { GradientText } from '../brand/GradientText';
import { useAgentStore } from '../../store/agentStore';
import './GoalApprovalPrompt.css';

const ACTION_LABELS: Record<string, string> = {
  task: 'Ejecutar tarea',
  install_package: 'Instalar paquete',
  create_venv: 'Crear entorno virtual',
  write_skill: 'Escribir skill',
  run_shell: 'Ejecutar comando',
};

function formatParams(actionType: string, params: Record<string, unknown>): string {
  if (actionType === 'run_shell') return String(params.command ?? '');
  if (actionType === 'install_package') return String(params.pkg ?? '');
  if (actionType === 'create_venv') return String(params.path ?? '');
  if (actionType === 'write_skill') return String(params.name ?? '');
  if (actionType === 'task') return String(params.description ?? params.prompt ?? '');
  return JSON.stringify(params, null, 2);
}

export function GoalApprovalPrompt() {
  const { goalApprovalRequest, approveGoalAction, denyGoalAction } = useAgentStore();
  const [remaining, setRemaining] = useState(0);

  useEffect(() => {
    if (!goalApprovalRequest) return;
    setRemaining(goalApprovalRequest.timeoutS);
    const interval = setInterval(() => {
      setRemaining(prev => {
        if (prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    // Auto-reject when the countdown elapses so the UI matches the server-side
    // approval timeout instead of leaving the dialog hanging.
    const expiry = setTimeout(() => {
      denyGoalAction();
    }, goalApprovalRequest.timeoutS * 1000);
    return () => {
      clearInterval(interval);
      clearTimeout(expiry);
    };
  }, [goalApprovalRequest, denyGoalAction]);

  if (!goalApprovalRequest) return null;

  const { actionType, params, timeoutS } = goalApprovalRequest;
  const label = ACTION_LABELS[actionType] ?? actionType;
  const detail = formatParams(actionType, params);
  const pct = timeoutS > 0 ? (remaining / timeoutS) * 100 : 0;

  return (
    <div className="goal-approval__backdrop" role="dialog" aria-modal="true" aria-labelledby="goal-approval-title">
      <div className="goal-approval__card">
        <header className="goal-approval__header">
          <div className="goal-approval__badge">
            <BrainCircuit size={22} strokeWidth={1.75} />
          </div>
          <h2 id="goal-approval-title" className="goal-approval__title">
            <GradientText animated={false}>GoalEngine</GradientText> solicita permiso
          </h2>
          <p className="goal-approval__subtitle">
            El agente autónomo quiere ejecutar una acción que modifica el sistema.
          </p>
        </header>

        <div className="goal-approval__body">
          <div className="goal-approval__warning">
            <TriangleAlert size={16} strokeWidth={2} />
            <span>
              Esta acción no se puede deshacer automáticamente. Revisa los detalles antes de aprobar.
            </span>
          </div>

          <div className="goal-approval__section">
            <span className="goal-approval__label">Acción solicitada</span>
            <div className="goal-approval__code">{label}</div>
          </div>

          {detail && (
            <div className="goal-approval__section">
              <span className="goal-approval__label">Parámetros</span>
              <div className="goal-approval__code">{detail}</div>
            </div>
          )}

          <div className="goal-approval__timer">
            <span>{remaining}s</span>
            <div className="goal-approval__timer-bar">
              <div className="goal-approval__timer-fill" style={{ width: `${pct}%` }} />
            </div>
            <span>auto-rechaza</span>
          </div>
        </div>

        <footer className="goal-approval__footer">
          <button type="button" className="am-btn goal-approval__deny" onClick={denyGoalAction}>
            Rechazar
          </button>
          <button
            type="button"
            className="am-btn goal-approval__approve"
            onClick={approveGoalAction}
            autoFocus
          >
            Aprobar {label}
          </button>
        </footer>
      </div>
    </div>
  );
}
