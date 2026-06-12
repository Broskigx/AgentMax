import { useAgentStore } from '../../store/agentStore';
import { motion, AnimatePresence } from 'framer-motion';

// ── Styles ───────────────────────────────────────────────────────────────────

const overlayVariants = {
  hidden: { opacity: 0, scale: 0.96 },
  visible: {
    opacity: 1, scale: 1,
    transition: { duration: 0.2, ease: 'easeOut' },
  },
  exit: {
    opacity: 0, scale: 0.96,
    transition: { duration: 0.15, ease: 'easeIn' },
  },
};

const stepCardVariants = {
  hidden: { opacity: 0, x: -10, height: 0 },
  visible: (i: number) => ({
    opacity: 1, x: 0, height: 'auto',
    transition: { delay: i * 0.06, type: 'spring', damping: 20, stiffness: 200 },
  }),
  exit: { opacity: 0, height: 0, transition: { duration: 0.15 } },
};

const styles = {
  overlay: {
    position: 'fixed' as const, inset: 0, zIndex: 9999,
    display: 'flex', flexDirection: 'column' as const,
    background: 'rgba(6,8,13,0.88)', backdropFilter: 'blur(20px) saturate(1.3)',
    WebkitBackdropFilter: 'blur(20px) saturate(1.3)',
  },
  header: {
    padding: '20px 24px 12px', display: 'flex', alignItems: 'center', gap: 12,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  headerLogo: {
    width: 28, height: 28, borderRadius: 8,
    display: 'grid', placeItems: 'center',
    background: 'linear-gradient(135deg, #5b8cff, #8b5cf6)',
    color: '#fff', fontSize: 12, fontWeight: 800,
    boxShadow: '0 2px 12px rgba(91,140,255,0.25)',
  },
  headerTitle: { fontSize: 16, fontWeight: 700, color: '#e8edf5' },
  headerBadge: (color: string) => ({
    fontSize: 9, padding: '3px 10px', borderRadius: 20,
    background: `${color}15`, color,
    fontWeight: 600, border: `1px solid ${color}22`,
  }),
  content: {
    flex: 1, overflowY: 'auto' as const, padding: '16px 24px',
  },
  stepCard: {
    padding: '12px 16px', marginBottom: 6, borderRadius: 10,
    background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)',
    display: 'flex', gap: 12, alignItems: 'flex-start',
    overflow: 'hidden',
  },
  stepNumber: {
    width: 24, height: 24, borderRadius: 6, display: 'grid', placeItems: 'center',
    background: 'rgba(91,140,255,0.1)', color: '#5b8cff', fontSize: 11, fontWeight: 700,
    flexShrink: 0, border: '1px solid rgba(91,140,255,0.15)',
  },
  footer: {
    padding: '16px 24px 20px', borderTop: '1px solid rgba(255,255,255,0.06)',
    display: 'flex', gap: 10, justifyContent: 'center',
  },
  cancelBtn: {
    padding: '8px 24px', borderRadius: 8, border: '1px solid rgba(248,113,113,0.25)',
    background: 'rgba(248,113,113,0.08)', color: '#f87171', cursor: 'pointer',
    fontSize: 12, fontWeight: 700, transition: 'all 150ms',
  },
  minimizeBtn: {
    padding: '8px 24px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)',
    background: 'rgba(255,255,255,0.03)', color: '#b0bac9', cursor: 'pointer',
    fontSize: 12, fontWeight: 600, transition: 'all 150ms',
  },
  spinner: {
    width: 22, height: 22, border: '2px solid rgba(91,140,255,0.15)',
    borderTopColor: '#5b8cff', borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
};

// ── Progress Bar ──────────────────────────────────────────────────────────────

function ProgressBar({ current, total }: { current: number; total: number }) {
  const pct = total > 0 ? Math.min((current / total) * 100, 100) : 0;
  return (
    <div style={{
      width: '100%', height: 2,
      background: 'rgba(255,255,255,0.04)', borderRadius: 2,
      overflow: 'hidden', margin: '4px 0 8px',
    }}>
      <motion.div
        style={{
          height: '100%', borderRadius: 2,
          background: 'linear-gradient(90deg, #5b8cff, #8b5cf6)',
        }}
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.4, ease: 'easeOut' }}
      />
    </div>
  );
}

// ── Spinner ───────────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <motion.div
      style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: '24px 0' }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
    >
      <div style={styles.spinner} />
      <motion.span
        style={{ fontSize: 13, color: '#5b8cff', fontWeight: 600 }}
        animate={{ opacity: [0.7, 1, 0.7] }}
        transition={{ repeat: Infinity, duration: 2 }}
      >
        Ejecutando tarea...
      </motion.span>
    </motion.div>
  );
}

// ── Overlay ───────────────────────────────────────────────────────────────────

interface OverlayProps {
  visible: boolean;
  agentState: string;
}

export function Overlay({ visible, agentState }: OverlayProps) {
  const { currentTaskId, stepLogs, emergencyStop, setMinimized, isMinimized } = useAgentStore();

  const statusColor = agentState === 'working' ? '#5b8cff' :
                      agentState === 'validating' ? '#fbbf24' :
                      agentState === 'completed' ? '#4ade80' :
                      agentState === 'error' ? '#f87171' : '#6b7a8d';

  const isRunning = agentState === 'working' || agentState === 'validating';

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          variants={overlayVariants}
          initial="hidden"
          animate="visible"
          exit="exit"
          style={styles.overlay}
        >
          {/* Header */}
          <div style={styles.header}>
            <motion.div
              style={styles.headerLogo}
              animate={{ rotate: isRunning ? [0, 5, 0, -5, 0] : 0 }}
              transition={isRunning ? { repeat: Infinity, duration: 4, ease: 'easeInOut' } : {}}
            >
              A
            </motion.div>
            <span style={styles.headerTitle}>Ejecución de Tarea</span>
            <motion.span
              style={styles.headerBadge(statusColor)}
              key={agentState}
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
            >
              {agentState === 'working' ? 'Ejecutando' :
               agentState === 'validating' ? 'Validando' :
               agentState === 'completed' ? 'Completado' :
               agentState === 'error' ? 'Error' : 'Inactivo'}
            </motion.span>
            {currentTaskId && (
              <span style={{ fontSize: 9, color: '#4a5568', fontFamily: 'JetBrains Mono, monospace' }}>
                ID: {currentTaskId.slice(0, 8)}...
              </span>
            )}
          </div>

          {/* Content */}
          <div style={styles.content}>
            {stepLogs.length > 0 && <ProgressBar current={stepLogs.length} total={stepLogs.length + 1} />}

            {stepLogs.length > 0 ? (
              <>
                <motion.div
                  style={{ fontSize: 9, fontWeight: 700, color: '#6b7a8d', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                >
                  Pasos de ejecución ({stepLogs.length})
                </motion.div>
                <AnimatePresence mode="popLayout">
                  {stepLogs.map((step, i) => (
                    <motion.div
                      key={i}
                      custom={i}
                      variants={stepCardVariants}
                      initial="hidden"
                      animate="visible"
                      exit="exit"
                      layout
                      style={styles.stepCard}
                    >
                      <motion.div
                        style={styles.stepNumber}
                        animate={{
                          background: step.stepNumber === stepLogs.length && isRunning
                            ? 'rgba(91,140,255,0.2)' : 'rgba(91,140,255,0.1)',
                          borderColor: step.stepNumber === stepLogs.length && isRunning
                            ? 'rgba(91,140,255,0.3)' : 'rgba(91,140,255,0.15)',
                        }}
                      >
                        {step.stepNumber}
                      </motion.div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, color: '#e8edf5', marginBottom: 1 }}>
                          {step.description}
                        </div>
                        <div style={{ fontSize: 9, color: '#6b7a8d' }}>
                          Paso {step.stepNumber} de {step.total}
                        </div>
                      </div>
                      {step.stepNumber === stepLogs.length && isRunning && (
                        <motion.div
                          style={{ ...styles.spinner, width: 14, height: 14, borderWidth: 1.5, flexShrink: 0 }}
                          animate={{ rotate: 360 }}
                          transition={{ repeat: Infinity, duration: 0.8, ease: 'linear' }}
                        />
                      )}
                      {!isRunning && step.success !== false && (
                        <motion.span
                          style={{ color: '#4ade80', fontSize: 14, flexShrink: 0 }}
                          initial={{ scale: 0 }}
                          animate={{ scale: 1 }}
                          transition={{ type: 'spring', damping: 10 }}
                        >
                          ✓
                        </motion.span>
                      )}
                    </motion.div>
                  ))}
                </AnimatePresence>
              </>
            ) : (
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                justifyContent: 'center', height: '100%', gap: 16,
              }}>
                <Spinner />
                <div style={{ fontSize: 12, color: '#6b7a8d', textAlign: 'center' }}>
                  {isRunning
                    ? 'La tarea se está ejecutando. Espera mientras completo los pasos.'
                    : 'Preparando la ejecución...'}
                </div>
              </div>
            )}

            {/* Completed state */}
            {agentState === 'completed' && !isRunning && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                style={{
                  marginTop: 16, padding: 20, borderRadius: 12,
                  background: 'rgba(74,222,128,0.05)',
                  border: '1px solid rgba(74,222,128,0.12)',
                  textAlign: 'center',
                  position: 'relative' as const,
                  overflow: 'hidden',
                }}
              >
                <motion.div
                  style={{
                    position: 'absolute', inset: 0,
                    background: 'radial-gradient(ellipse at center, rgba(74,222,128,0.04) 0%, transparent 70%)',
                  }}
                  animate={{ scale: [1, 1.2, 1] }}
                  transition={{ repeat: Infinity, duration: 3 }}
                />
                <motion.div
                  style={{ color: '#4ade80', fontSize: 24, marginBottom: 8 }}
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: 'spring', damping: 10, delay: 0.1 }}
                >
                  ✓
                </motion.div>
                <div style={{ color: '#4ade80', fontSize: 15, fontWeight: 700, marginBottom: 4, position: 'relative' }}>
                  Tarea completada
                </div>
                <div style={{ color: '#6b7a8d', fontSize: 11, position: 'relative' }}>
                  La tarea se ejecutó y validó correctamente.
                </div>
              </motion.div>
            )}

            {/* Error state */}
            {agentState === 'error' && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                style={{
                  marginTop: 16, padding: 20, borderRadius: 12,
                  background: 'rgba(248,113,113,0.05)',
                  border: '1px solid rgba(248,113,113,0.12)',
                }}
              >
                <div style={{ color: '#f87171', fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
                  Error en la tarea
                </div>
                <div style={{ color: '#fca5a5', fontSize: 11 }}>
                  La tarea falló durante la ejecución. Revisa el chat para más detalles.
                </div>
              </motion.div>
            )}
          </div>

          {/* Footer */}
          <motion.div
            style={styles.footer}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.1 }}
          >
            {isRunning && (
              <motion.button
                onClick={emergencyStop}
                style={styles.cancelBtn}
                whileHover={{ scale: 1.02, background: 'rgba(248,113,113,0.12)' }}
                whileTap={{ scale: 0.98 }}
              >
                ⚠ Detener emergencia
              </motion.button>
            )}
            <motion.button
              onClick={() => setMinimized(!isMinimized)}
              style={styles.minimizeBtn}
              whileHover={{ scale: 1.02, background: 'rgba(255,255,255,0.05)' }}
              whileTap={{ scale: 0.98 }}
            >
              {isMinimized ? 'Restaurar ventana' : 'Minimizar'}
            </motion.button>
          </motion.div>

        </motion.div>
      )}
    </AnimatePresence>
  );
}
