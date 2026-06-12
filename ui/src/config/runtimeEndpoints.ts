/**
 * Single source of truth for local runtime ports (desktop closed beta).
 * Keep in sync with agentmax.config.json and ui/src-tauri/src/commands.rs.
 */
export const RUNTIME_ENDPOINTS = {
  pythonApi: 'http://127.0.0.1:7790',
  rustApi: 'http://127.0.0.1:7789',
  ws: 'ws://127.0.0.1:7788',
  lmStudio: 'http://127.0.0.1:1234',
  llamaCpp: 'http://127.0.0.1:8080',
} as const;

export type RuntimeEndpointKey = keyof typeof RUNTIME_ENDPOINTS;