import { useEffect, useState, Component, ReactNode, useCallback } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { PremiumBackground } from './components/MainWindow/PremiumBackground';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { MainWindow } from './components/MainWindow/MainWindow';
import { AgentControlCursor } from './components/AgentControlCursor/AgentControlCursor';
import { Overlay } from './components/Overlay/Overlay';
import { HUDCanvas } from './components/HUD/HUDCanvas';
import { ComputerMaxPrompt } from './components/ComputerMaxPrompt/ComputerMaxPrompt';
import { CommandPalette } from './components/CommandPalette/CommandPalette';
import { Onboarding } from './components/Onboarding/Onboarding';
import { applyVisualCompatibilityProfile } from './lib/visualCompatibility';
import { recoveryLogFrontend } from './lib/recoveryService';
import { useAgentStore } from './store/agentStore';
import './App.css';

// ── Error Boundary with detailed log display ─────────────────────────────────

interface ErrorBoundaryProps { children: ReactNode; }
interface ErrorBoundaryState { hasError: boolean; error: Error | null; info: React.ErrorInfo | null; }

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null, info: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error, info: null };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.setState({ info });
    // Log to console for debugging
    console.error('[AgentMax] Error caught by boundary:', error);
    console.error('[AgentMax] Component stack:', info.componentStack);
    void recoveryLogFrontend('error', 'react.boundary', error.message, {
      name: error.name,
      stack: error.stack,
      componentStack: info.componentStack,
    });
  }

  render() {
    if (this.state.hasError) {
      return <ErrorScreen error={this.state.error} info={this.state.info} />;
    }
    return this.props.children;
  }
}

// ── Error Screen (clean) ─────────────────────────────────────────────────────
// Technical details are captured automatically in Recovery. Keep this UI minimal and professional.

function ErrorScreen({ error, info: _info }: { error: Error | null; info: React.ErrorInfo | null }) {
  const errorName = error?.name || 'Error';
  const errorMessage = error?.message || 'Ocurrió un problema inesperado.';

  const restart = useCallback(() => {
    window.location.reload();
  }, []);

  const openRecovery = useCallback(() => {
    // Best effort: try to open recovery window for full diagnostics/logs
    try {
      // @ts-ignore - dynamic import to avoid circulars
      import('./lib/recoveryService').then(m => m.openRecoveryWindow?.());
    } catch {}
    // Fallback reload
    setTimeout(() => window.location.reload(), 1200);
  }, []);

  return (
    <div style={{
      width: '100vw', height: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: '#0a0a0a', color: '#f4f4f5',
      fontFamily: 'Inter, system-ui, sans-serif', padding: 24,
    }}>
      <div style={{
        width: 'min(460px, 92vw)',
        background: 'rgba(20,20,20,0.96)', border: '1px solid rgba(249,115,22,0.2)',
        borderRadius: 14, boxShadow: '0 20px 60px rgba(0,0,0,0.6)',
        overflow: 'hidden',
      }}>
        <div style={{ padding: '20px 22px 14px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: 'rgba(248,113,113,0.15)', color: '#f87171',
              display: 'grid', placeItems: 'center', fontSize: 20, fontWeight: 700,
            }}>!</div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 16 }}>Algo salió mal</div>
              <div style={{ fontSize: 12, color: '#6b7a8d' }}>{errorName}</div>
            </div>
          </div>
        </div>

        <div style={{ padding: '16px 22px', fontSize: 13, color: '#e4e4e7', lineHeight: 1.45 }}>
          {errorMessage}
        </div>

        <div style={{ padding: '12px 22px 18px', display: 'flex', gap: 8, background: 'rgba(0,0,0,0.2)' }}>
          <button onClick={openRecovery} style={{
            flex: 1, padding: '9px 14px', borderRadius: 8,
            border: '1px solid rgba(251,146,60,0.3)', background: 'rgba(251,146,60,0.1)',
            color: '#fb923c', fontSize: 12, fontWeight: 600, cursor: 'pointer',
          }}>
            Abrir Recovery (logs y diagnóstico)
          </button>
          <button onClick={restart} style={{
            padding: '9px 16px', borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.04)',
            color: '#d1d5db', fontSize: 12, fontWeight: 600, cursor: 'pointer',
          }}>
            Reiniciar
          </button>
        </div>
      </div>
    </div>
  );
}

// ── App ──────────────────────────────────────────────────────────────────────

function getWindowLabel() {
  try {
    return getCurrentWindow().label;
  } catch {
    return 'main';
  }
}

function installInteractionGuards(emergencyStop: () => void, showCommandPalette: boolean, setShowCommandPalette: (value: boolean) => void) {
  (window as Window & { __AGENTMAX_GUARDS_ACTIVE?: boolean }).__AGENTMAX_GUARDS_ACTIVE = true;
  document.documentElement.dataset.AgentMaxGuards = 'active';

  const blockBrowserDevtoolsShortcut = (event: KeyboardEvent) => {
    const key = event.key.toLowerCase();
    const blocked =
      event.key === 'F12' ||
      (event.ctrlKey && event.shiftKey && event.key === 'Escape') ||
      (event.ctrlKey && event.shiftKey && ['i', 'j', 'c'].includes(key)) ||
      (event.metaKey && event.altKey && ['i', 'j', 'c'].includes(key)) ||
      (event.ctrlKey && key === 'u') ||
      (event.metaKey && key === 'u');

    if (blocked) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'F12' || (event.ctrlKey && event.shiftKey && event.key === 'Escape')) {
        emergencyStop();
      }
      return;
    }

    if (event.ctrlKey && event.key === 'k') {
      event.preventDefault();
      setShowCommandPalette(!showCommandPalette);
    }
    if (event.key === 'Escape' && showCommandPalette) {
      setShowCommandPalette(false);
    }
  };

  const blockContextMenu = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
  };

  window.addEventListener('keydown', blockBrowserDevtoolsShortcut, true);
  window.addEventListener('contextmenu', blockContextMenu, true);
  document.addEventListener('contextmenu', blockContextMenu, true);

  return () => {
    window.removeEventListener('keydown', blockBrowserDevtoolsShortcut, true);
    window.removeEventListener('contextmenu', blockContextMenu, true);
    document.removeEventListener('contextmenu', blockContextMenu, true);
  };
}

function AppContent() {
  const windowLabel = getWindowLabel();

  useEffect(() => applyVisualCompatibilityProfile(), []);

  if (windowLabel === 'hud') {
    return (
      <div style={{ width: '100vw', height: '100vh', background: 'transparent', overflow: 'hidden' }}>
        <HUDCanvas />
      </div>
    );
  }

  const {
    connect,
    initToken,
    refreshConnectionStatus,
    agentState,
    showOverlay,
    showCommandPalette,
    setShowCommandPalette,
    emergencyStop,
    connectionBanner,
    backendOnline,
  } = useAgentStore();
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try {
      return (
        localStorage.getItem('AgentMax.onboarding.force') === 'true' ||
        localStorage.getItem('AgentMax.onboarding.complete') !== 'true'
      );
    } catch {
      return true;
    }
  });
  const [AgentMaxAvailable, setAgentMaxAvailable] = useState(false);

  useEffect(() => {
    let active = true;
    void (async () => {
      await initToken();
      if (active) connect();
    })();
    const statusPoll = window.setInterval(() => {
      void refreshConnectionStatus();
    }, 8000);

    try {
      localStorage.setItem('AgentMax.activeBackend', 'AgentMax');
    } catch {
      // ignore storage errors in restricted webviews
    }

    const cleanupGuards = installInteractionGuards(
      emergencyStop,
      showCommandPalette,
      setShowCommandPalette,
    );

    return () => {
      active = false;
      window.clearInterval(statusPoll);
      cleanupGuards?.();
    };
  }, [connect, initToken, refreshConnectionStatus, emergencyStop, showCommandPalette, setShowCommandPalette]);

  useEffect(() => {
    setAgentMaxAvailable(backendOnline);
  }, [backendOnline]);

  const handleOnboardingComplete = useCallback(() => {
    try {
      localStorage.setItem('AgentMax.onboarding.complete', 'true');
    } catch {}
    setShowOnboarding(false);
  }, []);

  const handleOnboardingDismiss = useCallback(() => {
    handleOnboardingComplete();
  }, [handleOnboardingComplete]);

  return (
    <div className="app-root">
      <PremiumBackground />
      <div className="app-root__content">
        <AnimatePresence mode="wait">
          {connectionBanner ? (
            <motion.div
              key="banner"
              role="status"
              className={`am-connection-banner ${backendOnline ? 'am-connection-banner--online' : 'am-connection-banner--offline'}`}
              initial={{ opacity: 0, y: -12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ type: 'spring', stiffness: 380, damping: 28 }}
            >
              {connectionBanner}
            </motion.div>
          ) : null}
        </AnimatePresence>
        <AnimatePresence>
          {showOnboarding && (
            <Onboarding
              onComplete={handleOnboardingComplete}
              onDismiss={handleOnboardingDismiss}
              AgentMaxAvailable={AgentMaxAvailable}
            />
          )}
        </AnimatePresence>
        <Overlay visible={showOverlay} agentState={agentState} />
        <MainWindow />
        <AgentControlCursor />
        <ComputerMaxPrompt />
        <CommandPalette />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppContent />
    </ErrorBoundary>
  );
}
