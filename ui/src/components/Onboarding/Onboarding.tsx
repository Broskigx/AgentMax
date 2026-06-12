import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AgentMaxLogo } from '../brand/AgentMaxLogo';
import {
  CanvasRevealEffect,
  SignInPage,
  type DeveloperProfile,
} from '../ui/sign-in-flow-1';
import RealismButton from '../ui/shiny-borders-button';
import { Bot, Terminal, ShieldCheck, CheckCircle2, ArrowRight, X } from 'lucide-react';
import './Onboarding.css';

interface OnboardingStep {
  id: string;
  title: string;
  description: string;
  details: string[];
  icon: typeof Bot;
  badge?: string;
}

const STEPS: OnboardingStep[] = [
  {
    id: 'model',
    title: 'Runtime local, estado visible',
    description: 'AgentMax intenta iniciar su backend local y te muestra claramente si necesita atencion.',
    details: [
      'Backend de la app separado de LM Studio y llama.cpp',
      'No se descarga ningun modelo sin tu confirmacion',
      'Los fallos de conexion se muestran en la interfaz',
      'LM Studio y cloud son opcionales — puedes activarlos despues',
    ],
    icon: Bot,
    badge: 'Paso 1 de 3',
  },
  {
    id: 'tools',
    title: 'ComputerMax bajo supervisión',
    description: 'AgentMax puede observar tu equipo y activar ComputerMax solo con tu permiso.',
    details: [
      'Pantalla, mouse y teclado se autorizan por separado y solo para la tarea',
      'Screenshot con vista previa antes de enviarse al agente',
      'CMD / PowerShell y filesystem estan deshabilitados por defecto',
      'Acciones sensibles siempre piden confirmación',
    ],
    icon: ShieldCheck,
    badge: 'Paso 2 de 3',
  },
  {
    id: 'try',
    title: 'Prueba una tarea segura',
    description: 'AgentMax esta listo para una prueba controlada:',
    details: [
      'Escribe: "Solo texto: revisa este error"',
      'Escribe: "Cuantos tokens me quedan?"',
      'Usa "Capturar pantalla" para adjuntar evidencia visual',
      'Usa Stop Agent si necesitas detener una tarea',
    ],
    icon: Terminal,
    badge: 'Paso 3 de 3',
  },
];

interface OnboardingProps {
  onComplete: () => void;
  onDismiss: () => void;
  AgentMaxAvailable: boolean;
}

export function Onboarding({ onComplete, onDismiss, AgentMaxAvailable }: OnboardingProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [completed, setCompleted] = useState(false);
  const [profileReady, setProfileReady] = useState(() => {
    try {
      return Boolean(localStorage.getItem('AgentMax.developerProfile'));
    } catch {
      return false;
    }
  });

  const step = STEPS[currentStep];
  const isLast = currentStep === STEPS.length - 1;

  const handleNext = useCallback(() => {
    if (isLast) {
      setCompleted(true);
      onComplete();
    } else {
      setCurrentStep((s) => s + 1);
    }
  }, [isLast, onComplete]);

  const handlePrev = useCallback(() => {
    if (currentStep > 0) {
      setCurrentStep((s) => s - 1);
    }
  }, [currentStep]);

  const handleProfileComplete = useCallback((profile: DeveloperProfile) => {
    try {
      localStorage.setItem('AgentMax.developerProfile', JSON.stringify(profile));
    } catch {
      // The profile is optional; continue when storage is unavailable.
    }
    setProfileReady(true);
  }, []);

  if (!profileReady) {
    return (
      <motion.div
        className="onboarding-overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
      >
        <SignInPage onComplete={handleProfileComplete} onDismiss={onDismiss} />
      </motion.div>
    );
  }

  return (
    <motion.div
      className="onboarding-overlay"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <CanvasRevealEffect
        animationSpeed={2.4}
        colors={[[249, 115, 22], [251, 146, 60]]}
        dotSize={1.8}
      />
      <motion.div
        className="onboarding-card"
        initial={{ opacity: 0, scale: 0.92, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ type: 'spring', damping: 22, stiffness: 320 }}
      >
        <button className="onboarding-close" onClick={onDismiss} title="Cerrar onboarding">
          <X size={16} />
        </button>

        <div className="onboarding-header">
          <AgentMaxLogo size="sm" animated />
          <div>
            <h2>Bienvenido</h2>
            <p className="onboarding-subtitle">AgentMax Closed Beta</p>
          </div>
        </div>

        <div className="onboarding-progress">
          {STEPS.map((_, i) => (
            <div
              key={i}
              className={`onboarding-progress-step ${i <= currentStep ? 'onboarding-progress-step--active' : ''}`}
            />
          ))}
        </div>

        {!AgentMaxAvailable && (
          <div className="onboarding-notice">
            <Bot size={14} />
            <span>
              <strong>AgentMax</strong> no esta respondiendo en 127.0.0.1:7790. La interfaz queda lista, pero la prueba local necesita encender ese servidor.
            </span>
          </div>
        )}

        <AnimatePresence mode="wait">
          <motion.div
            key={step.id}
            className="onboarding-content"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
          >
            <div className="onboarding-step-icon">
              <step.icon size={24} />
            </div>
            <div className="onboarding-step-badge">{step.badge}</div>
            <h3>{step.title}</h3>
            <p className="onboarding-step-desc">{step.description}</p>
            <ul className="onboarding-step-list">
              {step.details.map((detail, i) => (
                <li key={i}>
                  <CheckCircle2 size={13} />
                  <span>{detail}</span>
                </li>
              ))}
            </ul>
          </motion.div>
        </AnimatePresence>

        <div className="onboarding-actions">
          {currentStep > 0 && (
            <button className="onboarding-btn onboarding-btn--secondary" onClick={handlePrev}>
              Anterior
            </button>
          )}
          <div style={{ flex: 1 }} />
          <RealismButton className="onboarding-shiny-btn" onClick={handleNext}>
            {isLast ? (
              <>Comenzar <ArrowRight size={14} /></>
            ) : (
              <>Siguiente <ArrowRight size={14} /></>
            )}
          </RealismButton>
        </div>

        <p className="onboarding-footer">
          AgentMax {completed ? '' : `- ${currentStep + 1} de ${STEPS.length}`}
        </p>
      </motion.div>
    </motion.div>
  );
}
