import { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, X } from 'lucide-react';
import { useAgentStore } from '../../store/agentStore';
import './NoModelAlert.css';

export function NoModelAlert() {
  const aiStatus = useAgentStore((s) => s.aiStatus);
  const lmstudioModels = useAgentStore((s) => s.lmstudioModels);
  const fetchLMStudioModels = useAgentStore((s) => s.fetchLMStudioModels);

  const [dismissed, setDismissed] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });

  // Determine if we have a real loaded model for the active LM path
  const currentBackend = aiStatus?.backend ?? 'AgentMax';
  const hasLoadedFromStatus = aiStatus?.has_loaded_model === true || (aiStatus?.health?.ok && (aiStatus?.models?.length ?? 0) > 0);
  const hasLocalModels = lmstudioModels.length > 0;
  const isLmStudioActive = currentBackend === 'lmstudio';

  const noModel = isLmStudioActive && !hasLoadedFromStatus && !hasLocalModels;

  // Auto refresh models periodically while the alert is relevant
  useEffect(() => {
    if (!noModel || dismissed) return;
    const id = window.setInterval(() => {
      void fetchLMStudioModels();
    }, 12000);
    return () => window.clearInterval(id);
  }, [noModel, dismissed, fetchLMStudioModels]);

  // Reset dismiss when situation changes (user loads a model)
  useEffect(() => {
    if (!noModel) setDismissed(false);
  }, [noModel]);

  if (!noModel || dismissed) return null;

  const handleDragEnd = (_: any, info: any) => {
    // Keep it on screen-ish by updating base position
    setPos((p) => ({
      x: Math.max(-280, Math.min(280, p.x + info.offset.x)),
      y: Math.max(-120, Math.min(160, p.y + info.offset.y)),
    }));
  };

  return (
    <AnimatePresence>
      <motion.div
        className="nomodel-alert"
        drag
        dragMomentum={false}
        onDragEnd={handleDragEnd}
        initial={{ opacity: 0, y: 8, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1, x: pos.x, y: pos.y }}
        exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.1 } }}
        transition={{ type: 'spring', stiffness: 420, damping: 32 }}
        whileDrag={{ scale: 1.01, boxShadow: '0 10px 30px rgba(0,0,0,0.35)' }}
        style={{ x: pos.x, y: pos.y }}
      >
        <div className="nomodel-alert__inner">
          <div className="nomodel-alert__icon" aria-hidden>
            <AlertTriangle size={15} />
          </div>
          <div className="nomodel-alert__body">
            <div className="nomodel-alert__title">Sin modelo cargado</div>
            <div className="nomodel-alert__msg">
              LM Studio no tiene ningún modelo cargado. No se puede ejecutar IA ni cargar tareas.
            </div>
            <div className="nomodel-alert__hint">
              Abre LM Studio → carga un modelo → inicia Local Server (puerto 1234)
            </div>
          </div>
          <button
            type="button"
            className="nomodel-alert__close"
            onClick={() => setDismissed(true)}
            aria-label="Cerrar alerta"
            title="Ocultar (temporal)"
          >
            <X size={13} />
          </button>
        </div>
        <div className="nomodel-alert__footer">
          <span className="nomodel-alert__urgent">URGENTE</span>
          <span className="nomodel-alert__drag">arrastra para mover</span>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
