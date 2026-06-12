import { useCallback, useEffect, useState } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { GradientText } from '../brand/GradientText';
import { useAgentStore } from '../../store/agentStore';
import './TitleBar.css';

function isTauri() {
  return typeof window !== 'undefined' && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

export function TitleBar() {
  const [isMaximized, setIsMaximized] = useState(false);
  const [isTauriApp, setIsTauriApp] = useState(false);
  const computerControlActive = useAgentStore((s) => s.computerControlActive);
  const backendOnline = useAgentStore((s) => s.backendOnline);
  const aiStatus = useAgentStore((s) => s.aiStatus);
  const lmstudioModels = useAgentStore((s) => s.lmstudioModels);

  const hasModel = (aiStatus?.has_loaded_model ?? false) || (aiStatus?.health?.ok ?? false) || lmstudioModels.length > 0;
  const aiOnline = backendOnline && (aiStatus?.health?.ok !== false) && (aiStatus?.backend !== 'lmstudio' || hasModel);

  useEffect(() => {
    setIsTauriApp(isTauri());
    if (!isTauri()) return undefined;
    const win = getCurrentWindow();
    void win.isMaximized().then(setIsMaximized);
    const unlisten = win.onResized(() => {
      void win.isMaximized().then(setIsMaximized);
    });
    return () => {
      void unlisten.then((fn) => fn());
    };
  }, []);

  const minimize = useCallback(() => {
    if (!isTauriApp) return;
    void getCurrentWindow().minimize();
  }, [isTauriApp]);

  const toggleMaximize = useCallback(() => {
    if (!isTauriApp) return;
    void getCurrentWindow().toggleMaximize();
  }, [isTauriApp]);

  const close = useCallback(() => {
    if (!isTauriApp) return;
    void getCurrentWindow().close();
  }, [isTauriApp]);

  return (
    <header className="am-titlebar">
      <div className="am-titlebar__brand" data-tauri-drag-region>
        <span className="am-titlebar__icon" aria-hidden="true">
          <span className="am-titlebar__icon-dot" />
        </span>
        <span className="am-titlebar__name">AgentMax</span>
        <span className="am-titlebar__version">v0.1</span>
      </div>

      <div className="am-titlebar__meta" data-tauri-drag-region>
        <span
          className={`am-titlebar__status ${aiOnline ? 'am-titlebar__status--on' : 'am-titlebar__status--off'}`}
          title={aiOnline ? 'IA detectada' : 'IA no detectada'}
          aria-label={aiOnline ? 'IA en línea' : 'IA sin conexión'}
        />
        <span className="am-titlebar__mode-label">AgentMax</span>
        <GradientText
          className={`am-titlebar__computermax ${computerControlActive ? 'am-titlebar__computermax--active' : ''}`}
          animated={computerControlActive}
        >
          ComputerMax
        </GradientText>
      </div>

      {isTauriApp ? (
        <div className="am-titlebar__controls">
          <button type="button" className="am-titlebar__control" onClick={minimize} aria-label="Minimizar">
            <span />
          </button>
          <button type="button" className="am-titlebar__control" onClick={toggleMaximize} aria-label={isMaximized ? 'Restaurar' : 'Maximizar'}>
            {isMaximized ? <span className="am-titlebar__restore" /> : <span className="am-titlebar__maximize" />}
          </button>
          <button type="button" className="am-titlebar__control am-titlebar__control--close" onClick={close} aria-label="Cerrar">
            <span className="am-titlebar__close" />
          </button>
        </div>
      ) : null}
    </header>
  );
}
