import {
  getDesktopMousePosition,
  getDesktopScreenInfo,
  getDesktopToolStatus,
  moveDesktopMouse,
  takeDesktopScreenshot,
  type ScreenInfo,
} from './desktopAutomationService';

export interface ScreenPoint {
  x: number;
  y: number;
}

function smoothstep(t: number) {
  return t * t * (3 - 2 * t);
}

export function emitAgentCursorTarget(
  target: ScreenPoint,
  screenWidth: number,
  screenHeight: number,
  label: string,
) {
  if (typeof window === 'undefined') return;
  const viewportX = Math.round((target.x / Math.max(1, screenWidth)) * window.innerWidth);
  const viewportY = Math.round((target.y / Math.max(1, screenHeight)) * window.innerHeight);
  window.dispatchEvent(new CustomEvent('AgentMax:agent-cursor-target', {
    detail: {
      x: Math.min(window.innerWidth - 24, Math.max(24, viewportX)),
      y: Math.min(window.innerHeight - 24, Math.max(24, viewportY)),
      label,
    },
  }));
}

export async function animateAgentCursor(
  from: ScreenPoint,
  to: ScreenPoint,
  screen: ScreenInfo,
  label: string,
  durationMs = 420,
) {
  const steps = Math.max(10, Math.round(durationMs / 28));
  const stepDelay = durationMs / steps;
  for (let i = 0; i <= steps; i += 1) {
    const t = smoothstep(i / steps);
    emitAgentCursorTarget(
      {
        x: Math.round(from.x + (to.x - from.x) * t),
        y: Math.round(from.y + (to.y - from.y) * t),
      },
      screen.width,
      screen.height,
      label,
    );
    if (i < steps) {
      await new Promise((resolve) => window.setTimeout(resolve, stepDelay));
    }
  }
}

export function parseCoordinates(text: string): ScreenPoint | null {
  const patterns = [
    /(?:x\s*[=:]\s*)?(-?\d{2,5})\s*[,;\s]+\s*(?:y\s*[=:]\s*)?(-?\d{2,5})/i,
    /\((-?\d{2,5})\s*[,;]\s*(-?\d{2,5})\)/,
    /(?:coordenadas?|posici[oó]n)\s+(-?\d{2,5})\s*[,;\s]+(-?\d{2,5})/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (!match) continue;
    const x = Number.parseInt(match[1], 10);
    const y = Number.parseInt(match[2], 10);
    if (Number.isFinite(x) && Number.isFinite(y)) {
      return { x: Math.max(0, x), y: Math.max(0, y) };
    }
  }
  return null;
}

export function resolveMouseTarget(command: string, screen: ScreenInfo): ScreenPoint | null {
  // Pure coord resolution for OpenAI Computer Use style. No word heuristics.
  // Model must provide exact x,y from screenshot via vision.
  const explicit = parseCoordinates(command);
  if (explicit) {
    return clampPoint(explicit, screen);
  }
  return null;
}

function clampPoint(point: ScreenPoint, screen: ScreenInfo): ScreenPoint {
  const margin = 12;
  return {
    x: Math.min(screen.width - margin, Math.max(margin, point.x)),
    y: Math.min(screen.height - margin, Math.max(margin, point.y)),
  };
}

export async function fetchScreenInfo(token: string): Promise<ScreenInfo> {
  const result = await getDesktopScreenInfo(token);
  if (result.success && result.data) {
    return result.data;
  }
  return {
    width: Math.max(800, window.screen?.width || window.innerWidth || 1366),
    height: Math.max(600, window.screen?.height || window.innerHeight || 768),
    monitors: [],
    automationAvailable: false,
    warnings: [result.error || 'No se pudo leer la pantalla desde Tauri.'],
  };
}

export async function ensureAutomationReady(token: string) {
  const status = await getDesktopToolStatus(token);
  if (!status.success || !status.data) {
    throw new Error(status.error || 'No se pudo verificar el estado de automatizacion.');
  }
  if (status.data.intervention.paused) {
    throw new Error(
      `La automatizacion esta pausada por intervencion del usuario: ${
        status.data.intervention.reason || 'user_input'
      }`,
    );
  }
}

export async function runMouseMoveTool(
  token: string,
  command: string,
  screen: ScreenInfo,
  durationMs = 480,
) {
  await ensureAutomationReady(token);

  const target = resolveMouseTarget(command, screen);
  if (!target) {
    throw new Error('La herramienta de mouse requiere coordenadas x,y verificadas por vision.');
  }
  const current = await getDesktopMousePosition(token);
  const from = current.success && current.data
    ? current.data
    : { x: Math.round(screen.width / 2), y: Math.round(screen.height / 2) };

  const movePromise = moveDesktopMouse(token, target.x, target.y, durationMs);
  await animateAgentCursor(from, target, screen, 'ComputerMax', durationMs);
  const moveResult = await movePromise;

  if (!moveResult.success) {
    throw new Error(moveResult.error || 'La herramienta move_mouse falló.');
  }

  const verified = await getDesktopMousePosition(token);
  const finalPoint = verified.success && verified.data ? verified.data : target;

  return {
    target,
    finalPoint,
    detail: moveResult.data?.detail || `Mouse en (${finalPoint.x}, ${finalPoint.y})`,
  };
}

export async function runScreenshotTool(token: string, screen: ScreenInfo) {
  await ensureAutomationReady(token);

  const scanPoints: ScreenPoint[] = [
    { x: Math.round(screen.width * 0.28), y: Math.round(screen.height * 0.32) },
    { x: Math.round(screen.width * 0.72), y: Math.round(screen.height * 0.32) },
    { x: Math.round(screen.width * 0.5), y: Math.round(screen.height * 0.5) },
    { x: Math.round(screen.width * 0.5), y: Math.round(screen.height * 0.5) },
  ];

  for (let i = 0; i < scanPoints.length - 1; i += 1) {
    await animateAgentCursor(scanPoints[i], scanPoints[i + 1], screen, 'Escaneando', 160);
  }

  const shot = await takeDesktopScreenshot(token, { includeBase64: true, saveToDisk: true });
  if (!shot.success || !shot.data?.imageBase64) {
    throw new Error(shot.error || 'La herramienta take_screenshot no devolvió imagen.');
  }

  emitAgentCursorTarget(
    { x: Math.round(screen.width / 2), y: Math.round(screen.height / 2) },
    screen.width,
    screen.height,
    'Captura lista',
  );

  return shot.data;
}

export function isMouseCommand(text: string) {
  return /\b(mouse|mause|cursor|rat[oó]n|puntero|mueve|mover|click|clic)\b/i.test(text);
}

export function isScreenshotCommand(text: string) {
  return /\b(captura|screenshot|pantalla|screen|observa|mirar)\b/i.test(text);
}
