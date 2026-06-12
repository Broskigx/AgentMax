import { invoke } from '@tauri-apps/api/core';

export type DesktopPlatform = 'windows' | 'linux' | 'macos' | 'unknown';

export interface DesktopToolResult<T> {
  success: boolean;
  data?: T;
  error?: string;
  platform: DesktopPlatform;
  durationMs: number;
  paused: boolean;
}

export interface PermissionState {
  screenCaptureEnabled: boolean;
  mouseControlEnabled: boolean;
  keyboardControlEnabled: boolean;
  automationEnabled: boolean;
}

export interface PermissionPatch {
  screenCaptureEnabled?: boolean;
  mouseControlEnabled?: boolean;
  keyboardControlEnabled?: boolean;
  automationEnabled?: boolean;
}

export interface MonitorInfo {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  primary: boolean;
}

export interface ScreenInfo {
  width: number;
  height: number;
  monitors: MonitorInfo[];
  displayServer?: string;
  automationAvailable: boolean;
  warnings: string[];
}

export interface ScreenshotResult {
  width: number;
  height: number;
  imagePath?: string;
  imageBase64?: string;
  monitorId?: string;
  timestamp: number;
}

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ScreenMatch {
  text?: string;
  bounds: BoundingBox;
  confidence: number;
  source: string;
}

export interface LocateOnScreenResult {
  query: string;
  matches: ScreenMatch[];
  source: string;
  timestamp: number;
}

export interface MousePosition {
  x: number;
  y: number;
}

export interface ActionResult {
  action: string;
  finalX?: number;
  finalY?: number;
  detail: string;
}

export interface InterventionStatus {
  paused: boolean;
  reason?: string;
  lastUserInterventionAt?: number;
  lastAction?: string;
  nativeInputMonitor: NativeInputMonitorStatus;
}

export interface NativeInputMonitorStatus {
  available: boolean;
  running: boolean;
  lastPhysicalInputAt?: number;
  lastSource?: string;
  error?: string;
}

export interface ToolCatalogItem {
  id: string;
  name: string;
  category: string;
  permission: string;
  implemented: boolean;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  manualTest: boolean;
}

export interface ToolStatus {
  permissions: PermissionState;
  intervention: InterventionStatus;
  screen: ScreenInfo;
  tools: ToolCatalogItem[];
}

export type MouseButton = 'left' | 'right' | 'middle';

export function assertDesktopSuccess<T>(result: DesktopToolResult<T>): T {
  if (!result.success || !result.data) {
    throw new Error(result.error || 'Desktop automation command failed.');
  }
  return result.data;
}

export async function getDesktopPermissions(token: string): Promise<DesktopToolResult<PermissionState>> {
  return invoke('desktop_get_permissions', { token });
}

export async function setDesktopPermissions(token: string, patch: PermissionPatch): Promise<DesktopToolResult<PermissionState>> {
  return invoke('desktop_set_permissions', { token, patch });
}

export async function getDesktopToolStatus(token: string): Promise<DesktopToolResult<ToolStatus>> {
  return invoke('desktop_get_tool_status', { token });
}

export async function takeDesktopScreenshot(
  token: string,
  options: { includeBase64?: boolean; saveToDisk?: boolean } = {},
): Promise<DesktopToolResult<ScreenshotResult>> {
  return invoke('desktop_take_screenshot', {
    token,
    includeBase64: options.includeBase64 ?? true,
    saveToDisk: options.saveToDisk ?? false,
  });
}

export async function getDesktopScreenInfo(token: string): Promise<DesktopToolResult<ScreenInfo>> {
  return invoke('desktop_get_screen_info', { token });
}

export async function locateOnDesktopScreen(
  token: string,
  query: string,
  minConfidence = 0.55,
): Promise<DesktopToolResult<LocateOnScreenResult>> {
  return invoke('desktop_locate_on_screen', { token, query, minConfidence });
}

export async function getDesktopMousePosition(token: string): Promise<DesktopToolResult<MousePosition>> {
  return invoke('desktop_get_mouse_position', { token });
}

export async function moveDesktopMouse(
  token: string,
  x: number,
  y: number,
  durationMs = 180,
): Promise<DesktopToolResult<ActionResult>> {
  return invoke('desktop_move_mouse', { token, x, y, durationMs });
}

export async function clickDesktopMouse(
  token: string,
  button: MouseButton = 'left',
  clicks = 1,
  x?: number,
  y?: number,
): Promise<DesktopToolResult<ActionResult>> {
  return invoke('desktop_click', { token, button, clicks, x, y });
}

export async function scrollDesktop(token: string, deltaX: number, deltaY: number): Promise<DesktopToolResult<ActionResult>> {
  return invoke('desktop_scroll', { token, deltaX, deltaY });
}

export async function typeDesktopText(
  token: string,
  text: string,
  intervalMs = 0,
): Promise<DesktopToolResult<ActionResult>> {
  return invoke('desktop_type_text', { token, text, intervalMs });
}

export async function pressDesktopKey(token: string, key: string): Promise<DesktopToolResult<ActionResult>> {
  return invoke('desktop_press_key', { token, key });
}

export async function pressDesktopCombo(token: string, keys: string[]): Promise<DesktopToolResult<ActionResult>> {
  return invoke('desktop_key_combo', { token, keys });
}

export async function resumeDesktopAutomation(token: string): Promise<DesktopToolResult<InterventionStatus>> {
  return invoke('desktop_resume_automation', { token });
}

export async function pauseDesktopAutomation(token: string, reason = 'manual_pause'): Promise<DesktopToolResult<InterventionStatus>> {
  return invoke('desktop_pause_automation', { token, reason });
}
