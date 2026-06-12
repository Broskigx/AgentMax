/**
 * AirGapToggle — "Privacy Shield" physical switch.
 *
 * When ON:
 *  - All external network connections are blocked at the socket level
 *  - AI backend is forced to LM Studio (local model)
 *  - A persistent red banner confirms offline mode to the user
 *
 * Designed for banks, hospitals, and government deployments that require
 * a provable guarantee: "Data never leaves this machine."
 */
import { useAgentStore } from '../../store/agentStore';
import './AirGapToggle.css';

export function AirGapToggle() {
  const { airGapEnabled, toggleAirGap } = useAgentStore();

  return (
    <div className={`airgap-wrapper ${airGapEnabled ? 'active' : ''}`}>
      {airGapEnabled && (
        <div className="airgap-banner">
          <span className="airgap-shield">🛡</span>
          <span>AIR-GAP ACTIVE — No external connections</span>
        </div>
      )}

      <button
        className={`airgap-toggle ${airGapEnabled ? 'on' : 'off'}`}
        onClick={toggleAirGap}
        title={airGapEnabled
          ? 'Air-Gap ON — click to allow external connections'
          : 'Air-Gap OFF — click to block all external connections (Privacy Shield)'}
      >
        <span className="airgap-icon">{airGapEnabled ? '🔒' : '🔓'}</span>
        <div className="airgap-text">
          <span className="airgap-label">Privacy Shield</span>
          <span className="airgap-state">{airGapEnabled ? 'OFFLINE ONLY' : 'Normal mode'}</span>
        </div>
        <div className={`airgap-pill ${airGapEnabled ? 'pill-on' : 'pill-off'}`}>
          <div className="airgap-knob" />
        </div>
      </button>
    </div>
  );
}
