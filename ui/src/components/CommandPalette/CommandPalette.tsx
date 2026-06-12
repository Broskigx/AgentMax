import { useState, useCallback, useEffect, useRef } from 'react';
import { useAgentStore } from '../../store/agentStore';
import { openRecoveryWindow } from '../../lib/recoveryService';
import { motion, AnimatePresence } from 'framer-motion';

// ── Commands ──────────────────────────────────────────────────────────────────

interface Command {
  id: string;
  label: string;
  description: string;
  icon: string;
  action: () => void;
  category: string;
}

function useCommands(): Command[] {
  const {
    sendMessage, emergencyStop, clearMessages, exportChat,
    toggleAirGap, setExperienceMode, switchBackend, toggleModule,
    airGapEnabled, experienceMode,
  } = useAgentStore();

  return [
    { id: 'task', label: 'Nueva tarea', description: 'Ejecuta una tarea en el sistema', icon: '⚡', category: 'acciones',
      action: () => { const t = prompt('Describe la tarea:'); if (t) sendMessage(t); } },
    { id: 'stop', label: 'Detener emergencia', description: 'Detiene toda ejecución activa', icon: '⛔', category: 'sistema',
      action: () => emergencyStop() },
    { id: 'clear', label: 'Limpiar chat', description: 'Borra todos los mensajes', icon: '🗑️', category: 'sistema',
      action: () => clearMessages() },
    { id: 'export', label: 'Exportar chat', description: 'Exporta como Markdown', icon: '📤', category: 'sistema',
      action: () => exportChat() },
    { id: 'recovery', label: 'Recovery & Logs', description: 'Abrir visor real de logs, crashes y diagnósticos', icon: '📋', category: 'herramientas',
      action: () => { void openRecoveryWindow(); } },
    { id: 'airgap', label: airGapEnabled ? 'Desactivar Air Gap' : 'Activar Air Gap', description: 'Aísla el agente de la red', icon: airGapEnabled ? '🔓' : '🔒', category: 'seguridad',
      action: () => toggleAirGap() },
    { id: 'mode-easy', label: 'Modo Fácil', description: 'Experiencia simplificada', icon: '🟢', category: 'modo',
      action: () => setExperienceMode('easy') },
    { id: 'mode-normal', label: 'Modo Normal', description: 'Experiencia balanceada', icon: '🟡', category: 'modo',
      action: () => { if (experienceMode !== 'normal') setExperienceMode('normal'); } },
    { id: 'mode-advanced', label: 'Modo Avanzado', description: 'Control total', icon: '🔴', category: 'modo',
      action: () => setExperienceMode('advanced') },
    { id: 'backend-claude', label: 'Backend: Claude', description: 'Usa Anthropic Claude', icon: '🤖', category: 'ia',
      action: () => switchBackend('claude') },
    { id: 'backend-lmstudio', label: 'Backend: LM Studio', description: 'Usa modelo local LM Studio', icon: '🖥️', category: 'ia',
      action: () => switchBackend('lmstudio') },
    { id: 'backend-llamacpp', label: 'Backend: GGUF llama.cpp', description: 'Usa llama-server con un modelo GGUF local', icon: 'GG', category: 'ia',
      action: () => switchBackend('llamacpp') },
    { id: 'backend-peft', label: 'Backend: PEFT local', description: 'Usa adapter LoRA local, separado de GGUF', icon: 'PF', category: 'ia',
      action: () => switchBackend('local_peft') },
  ];
}

// ── Animation Variants ────────────────────────────────────────────────────────

const backdropVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.15 } },
  exit: { opacity: 0, transition: { duration: 0.1 } },
};

const panelVariants = {
  hidden: { opacity: 0, scale: 0.92, y: -10 },
  visible: {
    opacity: 1, scale: 1, y: 0,
    transition: { type: 'spring', damping: 25, stiffness: 350 },
  },
  exit: {
    opacity: 0, scale: 0.92, y: -10,
    transition: { duration: 0.1, ease: 'easeIn' },
  },
};

const commandItemVariants = {
  hidden: { opacity: 0, x: -8 },
  visible: (i: number) => ({
    opacity: 1, x: 0,
    transition: { delay: i * 0.02, type: 'spring', damping: 22, stiffness: 250 },
  }),
};

// ── CommandPalette ────────────────────────────────────────────────────────────

export function CommandPalette() {
  const { showCommandPalette, setShowCommandPalette } = useAgentStore();
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const commands = useCommands();

  // Reset state when opened
  useEffect(() => {
    if (showCommandPalette) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [showCommandPalette]);

  // Filter commands
  const filtered = query
    ? commands.filter(c =>
        c.label.toLowerCase().includes(query.toLowerCase()) ||
        c.description.toLowerCase().includes(query.toLowerCase()) ||
        c.category.toLowerCase().includes(query.toLowerCase())
      )
    : commands;

  const handleSelect = useCallback((cmd: Command) => {
    setShowCommandPalette(false);
    cmd.action();
  }, [setShowCommandPalette]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(i => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && filtered[selectedIndex]) {
      e.preventDefault();
      handleSelect(filtered[selectedIndex]);
    } else if (e.key === 'Escape') {
      setShowCommandPalette(false);
    }
  }, [filtered, selectedIndex, handleSelect, setShowCommandPalette]);

  // Close on backdrop click
  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) setShowCommandPalette(false);
  }, [setShowCommandPalette]);

  if (!showCommandPalette) return null;

  // Group by category
  const categories = filtered.reduce<Record<string, Command[]>>((acc, cmd) => {
    (acc[cmd.category] = acc[cmd.category] || []).push(cmd);
    return acc;
  }, {});

  let globalIndex = 0;

  return (
    <AnimatePresence>
      {showCommandPalette && (
        <motion.div
          key="cmd-backdrop"
          variants={backdropVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          style={{
            position: 'fixed' as const, inset: 0, zIndex: 9900,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={handleBackdropClick}
        >
          <motion.div
            key="cmd-panel"
            className="am-glass"
            variants={panelVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            style={{
              width: 'min(520px, 94vw)', maxHeight: '70vh',
              borderRadius: 16,
              border: '1px solid rgba(255,255,255,0.06)',
              boxShadow: '0 32px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)',
              display: 'flex', flexDirection: 'column' as const, overflow: 'hidden',
            }}
          >
            {/* Search */}
            <div style={{ padding: 12, borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 12px', borderRadius: 10,
                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
              }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#6b7a8d" strokeWidth="2">
                  <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => { setQuery(e.target.value); setSelectedIndex(0); }}
                  onKeyDown={handleKeyDown}
                  placeholder="Buscar comandos..."
                  style={{
                    flex: 1, border: 'none', outline: 'none', background: 'transparent',
                    color: '#e8edf5', fontSize: 14, fontFamily: 'Inter, system-ui, sans-serif',
                  }}
                />
                {query && (
                  <motion.button
                    onClick={() => setQuery('')}
                    initial={{ opacity: 0, scale: 0 }}
                    animate={{ opacity: 1, scale: 1 }}
                    style={{
                      background: 'rgba(255,255,255,0.06)', border: 'none',
                      borderRadius: 4, width: 20, height: 20, cursor: 'pointer',
                      color: '#6b7a8d', fontSize: 10, display: 'grid', placeItems: 'center',
                    }}
                  >
                    ✕
                  </motion.button>
                )}
              </div>
            </div>

            {/* Commands list */}
            <div style={{
              flex: 1, overflowY: 'auto' as const, padding: 4,
            }}>
              {Object.entries(categories).map(([category, cmds]) => (
                <div key={category}>
                  <motion.div
                    style={{
                      padding: '8px 12px 4px', fontSize: 9, fontWeight: 700,
                      color: '#4a5568', textTransform: 'uppercase' as const,
                      letterSpacing: '0.08em',
                    }}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.05 }}
                  >
                    {category}
                  </motion.div>
                  {cmds.map((cmd) => {
                    const idx = globalIndex++;
                    const selected = idx === selectedIndex;
                    return (
                      <motion.div
                        key={cmd.id}
                        custom={idx}
                        variants={commandItemVariants}
                        initial="hidden"
                        animate="visible"
                        style={{
                          display: 'flex', alignItems: 'center', gap: 10,
                          padding: '8px 12px', margin: 2, borderRadius: 8,
                          cursor: 'pointer',
                          transition: 'all 0.08s',
                          background: selected ? 'rgba(91,140,255,0.1)' : 'transparent',
                          border: selected ? '1px solid rgba(91,140,255,0.2)' : '1px solid transparent',
                        }}
                        onClick={() => handleSelect(cmd)}
                        onMouseEnter={() => setSelectedIndex(idx)}
                        whileHover={{ background: 'rgba(255,255,255,0.03)' }}
                      >
                        <div style={{
                          width: 28, height: 28, borderRadius: 6,
                          display: 'grid', placeItems: 'center',
                          background: 'rgba(255,255,255,0.03)', fontSize: 13, flexShrink: 0,
                          border: '1px solid rgba(255,255,255,0.04)',
                        }}>
                          {cmd.icon}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, fontWeight: 600, color: '#e8edf5' }}>
                            {cmd.label}
                          </div>
                          <div style={{ fontSize: 10, color: '#6b7a8d', marginTop: 1 }}>
                            {cmd.description}
                          </div>
                        </div>
                        {selected && (
                          <motion.div
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            style={{
                              width: 18, height: 18, borderRadius: 4,
                              background: 'rgba(91,140,255,0.15)', color: '#5b8cff',
                              display: 'grid', placeItems: 'center',
                              fontSize: 9, fontWeight: 700,
                            }}
                          >
                            ↵
                          </motion.div>
                        )}
                      </motion.div>
                    );
                  })}
                </div>
              ))}
              {filtered.length === 0 && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  style={{ padding: 32, textAlign: 'center', color: '#6b7a8d', fontSize: 12 }}
                >
                  <div style={{ fontSize: 28, marginBottom: 8, opacity: 0.4 }}>⌕</div>
                  No se encontraron comandos para "{query}"
                </motion.div>
              )}
            </div>

            {/* Footer */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.08 }}
              style={{
                padding: '8px 12px', borderTop: '1px solid rgba(255,255,255,0.06)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                fontSize: 9, color: '#4a5568', fontFamily: 'JetBrains Mono, monospace',
              }}
            >
              <span>{filtered.length} comandos</span>
              <div style={{ display: 'flex', gap: 10 }}>
                <span><kbd style={keyHint}>↑↓</kbd> Navegar</span>
                <span><kbd style={keyHint}>↵</kbd> Seleccionar</span>
                <span><kbd style={keyHint}>Esc</kbd> Cerrar</span>
              </div>
            </motion.div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

const keyHint: React.CSSProperties = {
  padding: '1px 4px', borderRadius: 3,
  background: 'rgba(255,255,255,0.06)', color: '#6b7a8d',
  fontFamily: 'JetBrains Mono, monospace', fontSize: 8,
  marginRight: 2, border: '1px solid rgba(255,255,255,0.04)',
};
