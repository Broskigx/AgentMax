import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAgentStore } from '../../store/agentStore';
import './AISettings.css';

interface AISettingsProps {
  visible: boolean;
  onClose: () => void;
}

export function AISettings({ visible, onClose }: AISettingsProps) {
  const {
    aiStatus,
    lmstudioModels,
    selectedModel,
    setSelectedModel,
    experienceMode,
    advancedAISettings,
    fetchAIStatus,
    switchBackend,
    fetchLMStudioModels,
    setExperienceMode,
    updateAdvancedAISettings,
  } = useAgentStore();

  const [switching, setSwitching] = useState(false);
  const [showAgentMaxDetails, setShowAgentMaxDetails] = useState(false);

  useEffect(() => {
    if (visible) {
      fetchAIStatus();
      fetchLMStudioModels();
    }
  }, [visible, fetchAIStatus, fetchLMStudioModels]);

  const handleSwitch = async (backend: 'claude' | 'lmstudio' | 'local_peft' | 'llamacpp' | 'AgentMax' | 'mock') => {
    if (aiStatus?.backend === backend) return;
    setSwitching(true);
    try {
      await switchBackend(backend);
    } catch (err) {
      console.error('switchBackend failed:', err);
    } finally {
      setSwitching(false);
    }
  };

  const current = aiStatus?.backend ?? 'AgentMax';
  const isConnected = aiStatus?.health?.ok ?? false;
  const isEasy = experienceMode === 'easy';
  const isNormal = experienceMode === 'normal';
  const isAdvanced = experienceMode === 'advanced';

  return (
    <AnimatePresence>
      {visible && (
        <>
          <motion.div
            className="ai-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />

          <motion.div
            className="ai-panel"
            initial={{ opacity: 0, scale: 0.95, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: -10 }}
            transition={{ type: 'spring', damping: 22, stiffness: 350 }}
          >
            <div className="ai-header">
              <div>
                <span className="ai-title">Configuracion</span>
                <p className="ai-subtitle">Comodidad, modelo y comportamiento</p>
              </div>
              <button className="ai-close" onClick={onClose}>Cerrar</button>
            </div>

            <div className="ai-mode-selector">
              <button
                className={`ai-mode-btn ${isEasy ? 'ai-mode-btn--active' : ''}`}
                onClick={() => setExperienceMode('easy')}
              >
                <span>Facil</span>
                <small>Solo chat. AgentMax se encarga del resto.</small>
              </button>
              <button
                className={`ai-mode-btn ${isNormal ? 'ai-mode-btn--active' : ''}`}
                onClick={() => setExperienceMode('normal')}
              >
                <span>Normal</span>
                <small>Configuracion necesaria para uso diario.</small>
              </button>
              <button
                className={`ai-mode-btn ${isAdvanced ? 'ai-mode-btn--active' : ''}`}
                onClick={() => setExperienceMode('advanced')}
              >
                <span>Avanzado</span>
                <small>Temperatura, contexto, tokens y herramientas.</small>
              </button>
            </div>

            {isEasy && (
              <div className="ai-comfort-card">
                <div className="ai-section-title">Modo facil activo</div>
                <p>
                  AgentMax queda como un chat limpio. Oculta controles tecnicos, herramientas visibles y detalles
                  de modelo para que puedas escribir sin configurar nada.
                </p>
              </div>
            )}

            {!isEasy && (
              <>
                <div className="ai-selector">
                  {/* LM Studio */}
                  <button
                    className={`ai-backend-btn ${current === 'lmstudio' ? 'ai-backend-btn--active' : ''}`}
                    onClick={() => handleSwitch('lmstudio')}
                    disabled={switching}
                  >
                    <span className="ai-backend-icon">LM</span>
                    <div>
                      <div className="ai-backend-name">LM Studio</div>
                      <div className="ai-backend-desc">Local, privado, puerto 1234</div>
                    </div>
                    {current === 'lmstudio' && <span className="ai-check">Activo</span>}
                  </button>

                  {/* llama.cpp GGUF */}
                  <button
                    className={`ai-backend-btn ${current === 'llamacpp' ? 'ai-backend-btn--active' : ''}`}
                    onClick={() => handleSwitch('llamacpp')}
                    disabled={switching}
                  >
                    <span className="ai-backend-icon">GG</span>
                    <div>
                      <div className="ai-backend-name">GGUF llama.cpp</div>
                      <div className="ai-backend-desc">Sidecar local con AGENTMAX_GGUF_MODEL_PATH</div>
                    </div>
                    {current === 'llamacpp' && <span className="ai-check">Activo</span>}
                  </button>

                  {/* PEFT local */}
                  <button
                    className={`ai-backend-btn ${current === 'local_peft' ? 'ai-backend-btn--active' : ''}`}
                    onClick={() => handleSwitch('local_peft')}
                    disabled={switching}
                  >
                    <span className="ai-backend-icon">PF</span>
                    <div>
                      <div className="ai-backend-name">PEFT local</div>
                      <div className="ai-backend-desc">LoRA/adapter local, no GGUF</div>
                    </div>
                    {current === 'local_peft' && <span className="ai-check">Activo</span>}
                  </button>

                  {/* AgentMax Preview */}
                  <button
                    className={`ai-backend-btn ${current === 'AgentMax' ? 'ai-backend-btn--active' : ''}`}
                    onClick={() => handleSwitch('AgentMax')}
                    disabled={switching}
                    style={{
                      borderColor: current === 'AgentMax'
                        ? 'rgba(245,196,81,0.35)'
                        : 'rgba(245,196,81,0.08)',
                    }}
                  >
                    <span className="ai-backend-icon" style={{ color: '#f5c451' }}>AP</span>
                    <div>
                      <div className="ai-backend-name">
                        AgentMax Preview
                        <span style={{
                          marginLeft: 6,
                          padding: '1px 6px',
                          borderRadius: 4,
                          background: 'rgba(245,196,81,0.12)',
                          color: '#f5c451',
                          fontSize: 9,
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          letterSpacing: '0.04em',
                          verticalAlign: 'middle',
                        }}>Experimental</span>
                      </div>
                      <div className="ai-backend-desc">Planificacion con modelos locales (preview)</div>
                    </div>
                    {current === 'AgentMax' && <span className="ai-check" style={{ color: '#f5c451' }}>Preview</span>}
                  </button>

                  {/* API externa */}
                  {isAdvanced && (
                    <button
                      className={`ai-backend-btn ${current === 'claude' ? 'ai-backend-btn--active' : ''}`}
                      onClick={() => handleSwitch('claude')}
                      disabled={switching}
                    >
                      <span className="ai-backend-icon">API</span>
                      <div>
                        <div className="ai-backend-name">API externa</div>
                        <div className="ai-backend-desc">Cloud opcional para tareas avanzadas</div>
                      </div>
                      {current === 'claude' && <span className="ai-check">Activo</span>}
                    </button>
                  )}

                  {import.meta.env.DEV && (
                    <button
                      className={`ai-backend-btn ${current === 'mock' ? 'ai-backend-btn--active' : ''}`}
                      onClick={() => handleSwitch('mock')}
                      disabled={switching}
                    >
                      <span className="ai-backend-icon">⚡</span>
                      <div>
                        <div className="ai-backend-name">Mock (solo dev)</div>
                        <div className="ai-backend-desc">Respuestas simuladas para desarrollo</div>
                      </div>
                      {current === 'mock' && <span className="ai-check">Activo</span>}
                    </button>
                  )}
                </div>

                <div className="ai-status-block">
                  <div className="ai-status-row">
                    <span className="ai-label">Estado</span>
                    <span className={`ai-badge ${isConnected ? 'ai-badge--ok' : 'ai-badge--err'}`}>
                      {isConnected ? 'Conectado' : 'Sin conexion'}
                    </span>
                  </div>
                  {aiStatus?.model && (
                    <div className="ai-status-row">
                      <span className="ai-label">Modelo</span>
                      <span className="ai-value">{aiStatus.model}</span>
                    </div>
                  )}
                  <div className="ai-status-row">
                    <span className="ai-label">Vision</span>
                    <span className="ai-value">{aiStatus?.supports_vision ? 'Disponible' : 'Solo texto'}</span>
                  </div>
                </div>
              </>
            )}

            {/* ── AgentMax section ── */}
            {current === 'AgentMax' && (
              <div className="ai-lms-section">
                <div className="ai-section-title">
                  AgentMax Preview
                  <span style={{
                    marginLeft: 6, fontSize: 9, color: '#f5c451',
                    fontFamily: 'Inter, system-ui, sans-serif',
                  }}>· Experimental</span>
                </div>
                <div className="ai-AgentMax-card">
                  <p style={{ fontSize: 12, lineHeight: 1.6, color: 'rgba(255,255,255,0.6)', marginBottom: 10 }}>
                    AgentMax Preview puede ayudar a planificar, inspeccionar capturas de pantalla y
                    coordinar herramientas, pero la ejecucion avanzada de modelos locales puede requerir
                    configuracion adicional.
                  </p>
                  <div style={{
                    display: 'flex', flexDirection: 'column', gap: 6,
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>
                      <span>Estado</span>
                      <span className={`ai-badge ${isConnected ? 'ai-badge--ok' : 'ai-badge--err'}`}>
                        {isConnected ? 'Disponible' : 'No disponible'}
                      </span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>
                      <span>Base model</span>
                      <span style={{ color: 'rgba(255,255,255,0.5)', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>
                        Qwen3-VL-8B-Thinking
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => setShowAgentMaxDetails(!showAgentMaxDetails)}
                    style={{
                      marginTop: 10, padding: '6px 10px', borderRadius: 6,
                      border: '1px solid rgba(245,196,81,0.12)',
                      background: 'rgba(245,196,81,0.04)',
                      color: '#f5c451', cursor: 'pointer',
                      fontSize: 10, fontWeight: 600,
                      width: '100%', textAlign: 'left',
                    }}
                  >
                    {showAgentMaxDetails ? '▼ Ocultar detalles' : '▶ Ver detalles tecnicos'}
                  </button>
                  {showAgentMaxDetails && (
                    <div style={{
                      marginTop: 8, padding: 10, borderRadius: 6,
                      background: 'rgba(0,0,0,0.2)',
                      border: '1px solid rgba(255,255,255,0.04)',
                      fontSize: 10, color: 'rgba(255,255,255,0.35)',
                      lineHeight: 1.6, fontFamily: 'JetBrains Mono, monospace',
                    }}>
                      <div>Adapter: models/AgentMax/V2.1/adapter</div>
                      <div>Runtime: local_peft (experimental)</div>
                      <div>GPU: {'gpu' in navigator ? 'Detectada' : 'No detectada'}</div>
                      <div>Sanitizer: Activo</div>
                      <div style={{ marginTop: 4, color: '#f5c451' }}>
                        ⚠ La ejecucion local requiere GPU y dependencias Python.
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ── LM Studio section ── */}
            {!isEasy && (current === 'lmstudio' || lmstudioModels.length > 0) && (
              <div className="ai-lms-section">
                <div className="ai-section-title">Modelos en LM Studio — tocá uno para usarlo</div>
                {lmstudioModels.length === 0 ? (
                  <div className="ai-lms-empty" style={{ border: '1px solid #b45309', background: 'rgba(180,83,9,0.08)', padding: '8px 10px', borderRadius: 6 }}>
                    <strong style={{ color: '#fcd34d' }}>⚠ URGENTE — Sin modelo cargado</strong><br />
                    LM Studio no tiene ningún modelo cargado. No se puede ejecutar IA ni cargar nada.<br />
                    <span style={{ fontSize: 11, opacity: 0.85 }}>Abre LM Studio, carga un modelo y pulsa "Start Server" en el puerto 1234.</span>
                  </div>
                ) : (
                  lmstudioModels.map((model) => (
                    <button
                      key={model}
                      className="ai-model-item"
                      onClick={() => setSelectedModel(model)}
                      title="Usar este modelo"
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        width: '100%',
                        cursor: 'pointer',
                        textAlign: 'left',
                        border:
                          model === selectedModel
                            ? '1px solid rgba(126,200,126,0.55)'
                            : '1px solid rgba(255,255,255,0.06)',
                        background:
                          model === selectedModel
                            ? 'rgba(126,200,126,0.12)'
                            : 'rgba(255,255,255,0.02)',
                      }}
                    >
                      <span>{model}</span>
                      {model === selectedModel && (
                        <span style={{ fontSize: 10, color: '#7ec87e', fontWeight: 700 }}>
                          ✓ EN USO
                        </span>
                      )}
                    </button>
                  ))
                )}

                {!isConnected && (
                  <div className="ai-lms-hint">
                    <strong>Ruta:</strong> LM Studio / Local Server / Start en puerto 1234.
                  </div>
                )}
              </div>
            )}

            {isAdvanced && (
              <div className="ai-advanced">
                <div className="ai-section-title">Parametros avanzados</div>
                <label className="ai-range-row">
                  <span>Temperatura <b>{advancedAISettings.temperature.toFixed(2)}</b></span>
                  <input
                    type="range"
                    min="0"
                    max="1.2"
                    step="0.05"
                    value={advancedAISettings.temperature}
                    onChange={(event) => updateAdvancedAISettings({ temperature: Number(event.target.value) })}
                  />
                </label>
                <label className="ai-range-row">
                  <span>Tokens maximos <b>{advancedAISettings.maxTokens}</b></span>
                  <input
                    type="range"
                    min="512"
                    max="8192"
                    step="256"
                    value={advancedAISettings.maxTokens}
                    onChange={(event) => updateAdvancedAISettings({ maxTokens: Number(event.target.value) })}
                  />
                </label>
                <label className="ai-range-row">
                  <span>Historial <b>{advancedAISettings.contextTurns} turnos</b></span>
                  <input
                    type="range"
                    min="4"
                    max="24"
                    step="1"
                    value={advancedAISettings.contextTurns}
                    onChange={(event) => updateAdvancedAISettings({ contextTurns: Number(event.target.value) })}
                  />
                </label>
                <label className="ai-toggle-row">
                  <span>Herramientas automaticas</span>
                  <input
                    type="checkbox"
                    checked={advancedAISettings.autoTools}
                    onChange={(event) => updateAdvancedAISettings({ autoTools: event.target.checked })}
                  />
                </label>
                <label className="ai-toggle-row">
                  <span>Razonamiento detallado</span>
                  <input
                    type="checkbox"
                    checked={advancedAISettings.verboseReasoning}
                    onChange={(event) => updateAdvancedAISettings({ verboseReasoning: event.target.checked })}
                  />
                </label>
              </div>
            )}

            {switching && (
              <motion.div
                className="ai-switching"
                animate={{ opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 0.8, repeat: Infinity }}
              >
                Cambiando modelo...
              </motion.div>
            )}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
