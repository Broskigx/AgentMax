import { useAgentStore } from '../../store/agentStore';

// ── Styles ───────────────────────────────────────────────────────────────────

const styles = {
  backdrop: {
    position: 'fixed' as const, inset: 0, zIndex: 9000,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'rgba(6,8,13,0.6)', backdropFilter: 'blur(4px)',
    WebkitBackdropFilter: 'blur(4px)',
  },
  card: {
    width: 'min(440px, 92vw)', borderRadius: 14,
    background: 'rgba(15,18,26,0.98)', border: '1px solid rgba(91,140,255,0.15)',
    boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
    overflow: 'hidden',
  },
  header: {
    padding: '20px 24px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)',
    display: 'flex', alignItems: 'center', gap: 12,
  },
  headerIcon: {
    width: 36, height: 36, borderRadius: 10,
    display: 'grid', placeItems: 'center',
    background: 'rgba(91,140,255,0.12)', color: '#5b8cff',
    fontSize: 16, fontWeight: 700, flexShrink: 0,
  },
  headerTitle: { fontSize: 15, fontWeight: 700, color: '#e8edf5' },
  headerSubtitle: { fontSize: 11, color: '#6b7a8d', marginTop: 2 },
  body: { padding: '16px 24px 20px' },
  question: { fontSize: 13.5, color: '#b0bac9', lineHeight: 1.6, marginBottom: 16 },
  targetBox: {
    padding: 10, borderRadius: 8, marginBottom: 16,
    background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
    fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: '#8f9aaa',
  },
  footer: {
    padding: '12px 24px 16px', borderTop: '1px solid rgba(255,255,255,0.06)',
    display: 'flex', gap: 10, justifyContent: 'flex-end',
  },
  primaryBtn: {
    padding: '8px 20px', borderRadius: 8, border: 'none',
    background: 'rgba(91,140,255,0.2)', color: '#5b8cff',
    cursor: 'pointer', fontSize: 12, fontWeight: 700,
    transition: 'all 150ms',
  },
  secondaryBtn: {
    padding: '8px 20px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)',
    background: 'transparent', color: '#6b7a8d',
    cursor: 'pointer', fontSize: 12, fontWeight: 600,
    transition: 'all 150ms',
  },
};

// ── LearningPrompt ───────────────────────────────────────────────────────────

export function LearningPrompt() {
  const { learningPrompt, confirmElement, dismissLearningPrompt } = useAgentStore();

  if (!learningPrompt) return null;

  return (
    <div style={styles.backdrop}>
      <div style={styles.card}>
        {/* Header */}
        <div style={styles.header}>
          <div style={styles.headerIcon}>?</div>
          <div>
            <div style={styles.headerTitle}>Aprendizaje interactivo</div>
            <div style={styles.headerSubtitle}>
              AgentMax quiere aprender sobre este elemento
            </div>
          </div>
        </div>

        {/* Body */}
        <div style={styles.body}>
          <div style={styles.question}>{learningPrompt.question}</div>

          <div style={{
            fontSize: 10, fontWeight: 700, color: '#6b7a8d',
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6,
          }}>
            Elemento detectado
          </div>
          <div style={styles.targetBox}>
            <div>Target: {learningPrompt.target}</div>
            <div>Posición: ({learningPrompt.bounds[0]}, {learningPrompt.bounds[1]})</div>
            <div>Confianza: {Math.round(learningPrompt.confidence * 100)}%</div>
          </div>

          <div style={{ fontSize: 11, color: '#6b7a8d', lineHeight: 1.5 }}>
            ¿Este elemento se identificó correctamente? Tu respuesta ayuda a AgentMax
            a mejorar su precisión en futuras interacciones.
          </div>
        </div>

        {/* Footer */}
        <div style={styles.footer}>
          <button
            onClick={() => confirmElement(learningPrompt.taskId, false)}
            style={styles.secondaryBtn}
          >
            No es correcto
          </button>
          <button
            onClick={() => confirmElement(learningPrompt.taskId, true)}
            style={styles.primaryBtn}
          >
            Sí, es correcto
          </button>
        </div>
      </div>
    </div>
  );
}
