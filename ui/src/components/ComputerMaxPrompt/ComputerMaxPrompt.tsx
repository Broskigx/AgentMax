import { Monitor, Shield, TriangleAlert } from 'lucide-react';
import { GradientText } from '../brand/GradientText';
import { useAgentStore } from '../../store/agentStore';
import './ComputerMaxPrompt.css';

export function ComputerMaxPrompt() {
  const { computerControlRequest, approveComputerControl, denyComputerControl } = useAgentStore();

  if (!computerControlRequest) return null;

  return (
    <div className="computermax-prompt__backdrop" role="dialog" aria-modal="true" aria-labelledby="computermax-title">
      <div className="computermax-prompt__card">
        <header className="computermax-prompt__header">
          <div className="computermax-prompt__badge">
            <Shield size={22} strokeWidth={1.75} />
          </div>
          <h2 id="computermax-title" className="computermax-prompt__title">
            <GradientText animated={false}>ComputerMax</GradientText>
          </h2>
          <p className="computermax-prompt__subtitle">
            AgentMax solicita acceso temporal para controlar tu equipo de forma segura.
          </p>
        </header>

        <div className="computermax-prompt__body">
          <div className="computermax-prompt__warning">
            <TriangleAlert size={16} strokeWidth={2} />
            <span>
              ComputerMax podrá mover el mouse, escribir texto, capturar la pantalla
              y ejecutar herramientas del sistema bajo tu supervisión.
            </span>
          </div>

          <div className="computermax-prompt__section">
            <span className="computermax-prompt__label">Tarea solicitada</span>
            <div className="computermax-prompt__command">{computerControlRequest.command}</div>
          </div>

          <div className="computermax-prompt__section">
            <span className="computermax-prompt__label">Motivo</span>
            <div className="computermax-prompt__reason">
              <Monitor size={14} style={{ display: 'inline', verticalAlign: 'middle', marginRight: 6 }} />
              {computerControlRequest.reason}
            </div>
          </div>

          <div className="computermax-prompt__meta">
            <span>{new Date().toLocaleTimeString()}</span>
            <span>ComputerMax</span>
          </div>
        </div>

        <footer className="computermax-prompt__footer">
          <button type="button" className="am-btn" onClick={denyComputerControl}>
            Denegar
          </button>
          <button
            type="button"
            className="am-btn am-btn-primary computermax-prompt__approve"
            onClick={approveComputerControl}
            autoFocus
          >
            Aprobar ComputerMax
          </button>
        </footer>
      </div>
    </div>
  );
}
