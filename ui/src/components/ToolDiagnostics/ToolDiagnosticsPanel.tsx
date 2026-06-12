import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Camera,
  CheckCircle2,
  Crosshair,
  Keyboard,
  MousePointer2,
  Pause,
  Play,
  RefreshCcw,
  ShieldCheck,
  SlidersHorizontal,
  Type,
  XCircle,
} from 'lucide-react';
import {
  clickDesktopMouse,
  dragDesktopMouse,
  getDesktopMousePosition,
  getDesktopToolStatus,
  moveDesktopMouse,
  pauseDesktopAutomation,
  pressDesktopCombo,
  resumeDesktopAutomation,
  setDesktopPermissions,
  takeDesktopScreenshot,
  typeDesktopText,
  type DesktopToolResult,
  type PermissionPatch,
  type ScreenshotResult,
  type ToolStatus,
} from '../../lib/desktopAutomationService';
import './ToolDiagnosticsPanel.css';

interface Props {
  ipcToken: string | null;
}

function compactResult(result: DesktopToolResult<unknown> | null) {
  if (!result) return 'No diagnostic run yet.';
  if (result.success) {
    return `success=true platform=${result.platform} duration=${result.durationMs}ms`;
  }
  return `success=false platform=${result.platform} error=${result.error || 'unknown'}`;
}

export function ToolDiagnosticsPanel({ ipcToken }: Props) {
  const [status, setStatus] = useState<ToolStatus | null>(null);
  const [lastResult, setLastResult] = useState<DesktopToolResult<unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [screenshot, setScreenshot] = useState<ScreenshotResult | null>(null);
  const [typedValue, setTypedValue] = useState('');
  const testInputRef = useRef<HTMLInputElement>(null);
  const clickPadRef = useRef<HTMLButtonElement>(null);
  const dragPadRef = useRef<HTMLButtonElement>(null);

  const refresh = useCallback(async (preserveResult = false) => {
    if (!ipcToken) return;
    const result = await getDesktopToolStatus(ipcToken);
    if (!preserveResult) {
      setLastResult(result as DesktopToolResult<unknown>);
    }
    if (result.success && result.data) setStatus(result.data);
  }, [ipcToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = useCallback(async <T,>(label: string, action: () => Promise<DesktopToolResult<T>>) => {
    setBusy(label);
    try {
      const result = await action();
      setLastResult(result as DesktopToolResult<unknown>);
      await refresh(true);
      return result;
    } catch (error) {
      setLastResult({
        success: false,
        error: error instanceof Error ? error.message : String(error),
        platform: 'unknown',
        durationMs: 0,
        paused: false,
      });
      return null;
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const patchPermission = useCallback(async (patch: PermissionPatch) => {
    if (!ipcToken) return;
    await run('permissions', () => setDesktopPermissions(ipcToken, patch));
  }, [ipcToken, run]);

  const testScreenshot = useCallback(async () => {
    if (!ipcToken) return;
    const result = await run('screenshot', () => takeDesktopScreenshot(ipcToken, { includeBase64: true, saveToDisk: true }));
    if (result?.success && result.data) setScreenshot(result.data);
  }, [ipcToken, run]);

  const testMove = useCallback(async () => {
    if (!ipcToken) return;
    const pos = await getDesktopMousePosition(ipcToken);
    if (!pos.success || !pos.data) {
      setLastResult(pos as DesktopToolResult<unknown>);
      return;
    }
    const screen = status?.screen;
    const maxX = Math.max(1, (screen?.width || window.screen.availWidth || 1200) - 1);
    const maxY = Math.max(1, (screen?.height || window.screen.availHeight || 800) - 1);
    const nextX = Math.min(maxX, Math.max(0, pos.data.x + 24));
    const nextY = Math.min(maxY, Math.max(0, pos.data.y));
    await run('move', () => moveDesktopMouse(ipcToken, nextX, nextY, 220));
  }, [ipcToken, run, status?.screen]);

  const testClick = useCallback(async () => {
    if (!ipcToken || !clickPadRef.current) return;
    const rect = clickPadRef.current.getBoundingClientRect();
    const x = Math.round(window.screenX + rect.left + rect.width / 2);
    const y = Math.round(window.screenY + rect.top + rect.height / 2);
    await run('click', () => clickDesktopMouse(ipcToken, 'left', 1, x, y));
  }, [ipcToken, run]);

  const testDrag = useCallback(async () => {
    if (!ipcToken || !dragPadRef.current) return;
    const rect = dragPadRef.current.getBoundingClientRect();
    const fromX = Math.round(window.screenX + rect.left + rect.width * 0.35);
    const toX = Math.round(window.screenX + rect.left + rect.width * 0.65);
    const y = Math.round(window.screenY + rect.top + rect.height / 2);
    await run('drag', () => dragDesktopMouse(ipcToken, fromX, y, toX, y, 350));
  }, [ipcToken, run]);

  const testType = useCallback(async () => {
    if (!ipcToken) return;
    setTypedValue('');
    testInputRef.current?.focus();
    await run('type', () => typeDesktopText(ipcToken, 'AgentMax test', 0));
  }, [ipcToken, run]);

  const testHotkey = useCallback(async () => {
    if (!ipcToken) return;
    testInputRef.current?.focus();
    const modifier = /mac/i.test(navigator.platform) ? 'cmd' : 'ctrl';
    await run('hotkey', () => pressDesktopCombo(ipcToken, [modifier, 'a']));
  }, [ipcToken, run]);

  const permissions = status?.permissions;
  const intervention = status?.intervention;
  const monitor = intervention?.nativeInputMonitor;
  const canUseNative = Boolean(ipcToken);

  return (
    <section className="pilot-panel tool-diagnostics-panel" aria-label="Tool Diagnostics">
      <div className="pilot-panel-heading">
        <div>
          <div className="pilot-panel-title">Tool Diagnostics</div>
          <strong>{canUseNative ? lastResult?.platform || 'Native IPC' : 'Browser mode'}</strong>
        </div>
        <SlidersHorizontal size={18} />
      </div>

      <div className="tooldiag-permissions">
        <label>
          <input
            type="checkbox"
            checked={Boolean(permissions?.screenCaptureEnabled)}
            disabled={!canUseNative}
            onChange={(event) => patchPermission({ screenCaptureEnabled: event.currentTarget.checked })}
          />
          Screen
        </label>
        <label>
          <input
            type="checkbox"
            checked={Boolean(permissions?.automationEnabled)}
            disabled={!canUseNative}
            onChange={(event) => patchPermission({ automationEnabled: event.currentTarget.checked })}
          />
          Automation
        </label>
        <label>
          <input
            type="checkbox"
            checked={Boolean(permissions?.mouseControlEnabled)}
            disabled={!canUseNative}
            onChange={(event) => patchPermission({ mouseControlEnabled: event.currentTarget.checked })}
          />
          Mouse
        </label>
        <label>
          <input
            type="checkbox"
            checked={Boolean(permissions?.keyboardControlEnabled)}
            disabled={!canUseNative}
            onChange={(event) => patchPermission({ keyboardControlEnabled: event.currentTarget.checked })}
          />
          Keyboard
        </label>
      </div>

      <div className="tooldiag-status">
        <span className={intervention?.paused ? 'tooldiag-bad' : 'tooldiag-good'}>
          {intervention?.paused ? <XCircle size={14} /> : <CheckCircle2 size={14} />}
          {intervention?.paused ? `Paused: ${intervention.reason || 'user intervention'}` : 'Ready'}
        </span>
        <span>
          <ShieldCheck size={14} />
          {status?.screen.automationAvailable ? 'Automation available' : 'Limited platform'}
        </span>
        <span className={monitor?.running ? 'tooldiag-good' : undefined}>
          <ShieldCheck size={14} />
          {monitor?.available
            ? monitor.running
              ? 'Input monitor active'
              : `Input monitor inactive${monitor.error ? ': ' + monitor.error : ''}`
            : 'Input monitor unavailable'}
        </span>
      </div>

      {status?.screen.warnings?.length || monitor?.error ? (
        <div className="tooldiag-warning">{monitor?.error || status?.screen.warnings[0]}</div>
      ) : null}

      <div className="tooldiag-actions">
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'screenshot'} onClick={testScreenshot}>
          <Camera size={15} />
          Test Screenshot
        </button>
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'move'} onClick={testMove}>
          <MousePointer2 size={15} />
          Test Move
        </button>
        <button ref={clickPadRef} className="pilot-secondary-button" disabled={!canUseNative || busy === 'click'} onClick={testClick}>
          <Crosshair size={15} />
          Test Click
        </button>
        <button ref={dragPadRef} className="pilot-secondary-button" disabled={!canUseNative || busy === 'drag'} onClick={testDrag}>
          <MousePointer2 size={15} />
          Test Drag
        </button>
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'type'} onClick={testType}>
          <Type size={15} />
          Test Type
        </button>
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'hotkey'} onClick={testHotkey}>
          <Keyboard size={15} />
          Test Hotkey
        </button>
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'pause'} onClick={() => run('pause', () => pauseDesktopAutomation(ipcToken!, 'manual_pause'))}>
          <Pause size={15} />
          Pause
        </button>
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'resume'} onClick={() => run('resume', () => resumeDesktopAutomation(ipcToken!))}>
          <Play size={15} />
          Resume
        </button>
        <button className="pilot-secondary-button" disabled={!canUseNative || busy === 'refresh'} onClick={() => void refresh()}>
          <RefreshCcw size={15} />
          Refresh
        </button>
      </div>

      <input
        ref={testInputRef}
        className="tooldiag-input"
        value={typedValue}
        onChange={(event) => setTypedValue(event.target.value)}
        placeholder="Keyboard test field"
      />

      {screenshot?.imageBase64 ? (
        <img
          className="tooldiag-preview"
          src={`data:image/png;base64,${screenshot.imageBase64}`}
          alt="Latest diagnostics screenshot"
        />
      ) : null}

      <code className="tooldiag-result">{compactResult(lastResult)}</code>
    </section>
  );
}
