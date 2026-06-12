import type { ChatAttachment } from '../types';
import { assertDesktopSuccess, takeDesktopScreenshot } from './desktopAutomationService';

export interface ScreenshotCapture {
  id: string;
  dataUrl: string;
  width: number;
  height: number;
  bytes: number;
  timestamp: number;
  sentToAgent: boolean;
  stored: boolean;
  metadata?: {
    appName?: string;
    windowTitle?: string;
    resolution?: string;
  };
}

function newId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj?.randomUUID) return `shot-${cryptoObj.randomUUID()}`;
  return `shot-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Capture a screenshot.
 * In Tauri environment, uses Rust IPC. Falls back to HTML Canvas capture
 * or returns a placeholder when unavailable.
 */
export async function captureScreenshot(ipcToken: string | null): Promise<ScreenshotCapture> {
  const ts = Date.now();

  // Try Tauri IPC first
  if (ipcToken) {
    try {
      const nativeShot = assertDesktopSuccess(await takeDesktopScreenshot(ipcToken, {
        includeBase64: true,
        saveToDisk: true,
      }));
      const b64 = nativeShot.imageBase64;
      if (b64 && b64.length > 100) {
        const bytes = Math.round(b64.length * 0.75);
        return {
          id: newId(),
          dataUrl: `data:image/png;base64,${b64}`,
          width: nativeShot.width,
          height: nativeShot.height,
          bytes,
          timestamp: ts,
          sentToAgent: false,
          stored: Boolean(nativeShot.imagePath),
          metadata: {
            resolution: `${nativeShot.width}x${nativeShot.height}`,
            windowTitle: nativeShot.imagePath,
          },
        };
      }
    } catch (error) {
      throw error instanceof Error ? error : new Error(String(error));
    }
  }

  // Try legacy Tauri IPC for older builds
  if (ipcToken) {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const b64 = await invoke<string>('screenshot_base64', { token: ipcToken });
      if (b64 && b64.length > 100) {
        const bytes = Math.round(b64.length * 0.75);
        return {
          id: newId(),
          dataUrl: `data:image/png;base64,${b64}`,
          width: 0,
          height: 0,
          bytes,
          timestamp: ts,
          sentToAgent: false,
          stored: false,
          metadata: { resolution: 'via legacy Tauri IPC' },
        };
      }
    } catch {
      // Fall through to fallback
    }
  }

  // Try navigator.mediaDevices (screen capture API)
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      preferCurrentTab: false,
      video: { displaySurface: 'monitor' },
    } as DisplayMediaStreamOptions & { preferCurrentTab?: boolean });
    const track = stream.getVideoTracks()[0];
    const settings = track.getSettings();
    const width = settings.width || 1920;
    const height = settings.height || 1080;
    const imageCapture = new (window as any).ImageCapture(track);
    const bitmap = await imageCapture.grabFrame();
    track.stop();
    stream.getTracks().forEach(t => t.stop());

    // Draw to canvas and get data URL
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D not available');
    ctx.drawImage(bitmap, 0, 0);
    const dataUrl = canvas.toDataURL('image/jpeg', 0.8);
    const bytes = Math.round(dataUrl.length * 0.75);
    bitmap.close();

    return {
      id: newId(),
      dataUrl,
      width: bitmap.width,
      height: bitmap.height,
      bytes,
      timestamp: ts,
      sentToAgent: false,
      stored: false,
      metadata: { resolution: `${bitmap.width}x${bitmap.height}` },
    };
  } catch {
    // Fall through to placeholder
  }

  // No capture available — return a descriptive placeholder
  throw new Error(
    'Screenshot capture requires Tauri desktop environment or browser screen capture permissions. ' +
    'Use the Tauri app (npm run tauri dev) or grant screen capture permission.'
  );
}

/**
 * Convert a ScreenshotCapture to a ChatAttachment for sending to the agent.
 */
export function screenshotToAttachment(shot: ScreenshotCapture): ChatAttachment {
  return {
    id: shot.id,
    kind: 'image',
    name: `screenshot-${new Date(shot.timestamp).toISOString().slice(0, 19).replace(/[:]/g, '-')}.jpg`,
    mime: 'image/jpeg',
    size: shot.bytes,
    dataUrl: shot.dataUrl,
    width: shot.width,
    height: shot.height,
  };
}
