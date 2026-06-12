import { useEffect, useState } from 'react';

// ── Types ────────────────────────────────────────────────────────────────────

interface WidgetState {
  agentState: string;
  isTyping: boolean;
  lastUserMsg: string;
  msgCount: number;
}

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = {
  container: {
    width: '100vw', height: '100vh',
    background: 'transparent',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontFamily: 'Inter, system-ui, sans-serif',
    WebkitUserSelect: 'none' as const,
    userSelect: 'none' as const,
  },
  card: {
    width: '100%', height: '100%',
    display: 'flex', flexDirection: 'column' as const,
    background: 'rgba(10,13,20,0.85)',
    backdropFilter: 'blur(12px)',
    WebkitBackdropFilter: 'blur(12px)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 12,
    overflow: 'hidden',
  },
  header: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)',
    flexShrink: 0,
  },
  dot: (color: string) => ({
    width: 6, height: 6, borderRadius: '50%', background: color,
    boxShadow: `0 0 6px ${color}44`,
    flexShrink: 0,
  }),
  title: { fontSize: 11, fontWeight: 700, color: '#e8edf5' },
  badge: {
    fontSize: 8, padding: '1px 6px', borderRadius: 3,
    background: 'rgba(91,140,255,0.15)', color: '#5b8cff',
    fontWeight: 600, letterSpacing: '0.04em', marginLeft: 'auto',
  },
  body: {
    flex: 1, display: 'flex', flexDirection: 'column' as const,
    justifyContent: 'center', padding: '6px 12px 8px',
    overflow: 'hidden',
  },
  stateText: { fontSize: 10, color: '#6b7a8d', fontWeight: 500 },
  messageText: {
    fontSize: 11, color: '#b0bac9', lineHeight: 1.4,
    overflow: 'hidden', textOverflow: 'ellipsis',
    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' as const,
    marginTop: 2,
  },
  typingIndicator: {
    display: 'flex', alignItems: 'center', gap: 4, marginTop: 4,
    fontSize: 9, color: '#5b8cff',
  },
  typingDot: (delay: number) => ({
    width: 4, height: 4, borderRadius: '50%', background: '#5b8cff',
    animation: `widget-pulse 1.2s ease-in-out ${delay}s infinite`,
  }),
  emptyState: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    height: '100%', fontSize: 10, color: '#4a5568',
  },
  footer: {
    padding: '4px 12px 6px', display: 'flex', justifyContent: 'space-between',
    alignItems: 'center', fontSize: 8, color: '#3a4a5a',
    borderTop: '1px solid rgba(255,255,255,0.04)',
  },
};

// ── Widget ────────────────────────────────────────────────────────────────────

export function Widget() {
  const [state, setState] = useState<WidgetState>({
    agentState: 'idle',
    isTyping: false,
    lastUserMsg: '',
    msgCount: 0,
  });

  useEffect(() => {
    let bc: BroadcastChannel | null = null;
    try {
      bc = new BroadcastChannel('AgentMax');
      bc.onmessage = (e: MessageEvent<WidgetState>) => {
        setState(e.data);
      };
    } catch {
      // BroadcastChannel not supported — widget shows empty state
    }
    return () => bc?.close();
  }, []);

  const stateColor = state.agentState === 'idle' ? '#4ade80' :
                     state.agentState === 'working' ? '#5b8cff' :
                     state.agentState === 'completed' ? '#4ade80' :
                     state.agentState === 'error' ? '#f87171' : '#fbbf24';

  const stateLabel = state.agentState === 'idle' ? 'Inactivo' :
                     state.agentState === 'working' ? 'Trabajando' :
                     state.agentState === 'completed' ? 'Completado' :
                     state.agentState === 'error' ? 'Error' : state.agentState;

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        {/* Header */}
        <div style={styles.header}>
          <div style={styles.dot(stateColor)} />
          <span style={styles.title}>AgentMax</span>
          <span style={styles.badge}>{stateLabel}</span>
        </div>

        {/* Body */}
        <div style={styles.body}>
          {state.msgCount > 0 ? (
            <>
              <div style={styles.stateText}>
                Último mensaje:
              </div>
              <div style={styles.messageText}>
                {state.lastUserMsg || '—'}
              </div>
              {state.isTyping && (
                <div style={styles.typingIndicator}>
                  <div style={styles.typingDot(0)} />
                  <div style={styles.typingDot(0.4)} />
                  <div style={styles.typingDot(0.8)} />
                  <span style={{ marginLeft: 4 }}>Escribiendo...</span>
                </div>
              )}
            </>
          ) : (
            <div style={styles.emptyState}>
              AgentMax esperando instrucciones
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={styles.footer}>
          <span>{state.msgCount} mensajes</span>
          <span>AgentMax v0.1</span>
        </div>
      </div>

    </div>
  );
}
