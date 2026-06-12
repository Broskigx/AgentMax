# AgentMax Technical Beta Audit

Fecha: 2026-05-23

## Estado General

AgentMax es una app desktop con frontend React/Vite, runtime Tauri/Rust y backend Python. El proyecto ya tiene una base real para chat, screenshot nativo, ejecución PowerShell sandboxed, supervisor Python, registry de tools, dataset local de AgentMax y pruebas Python. El estado previo no era una beta técnica cerrada porque varias capacidades estaban dispersas entre UI, backend de prueba y core Python sin una vista única de permisos, tokens, tools, logs y readiness.

## Estructura Detectada

- `ui/`: frontend React 18 + Vite + Zustand + Tauri API.
- `ui/src-tauri/`: app Tauri v2 en Rust, comandos nativos, screenshot GDI, shell sandbox, tray y servidor local.
- `core/`: runtime Python, agentes, tools, seguridad, memoria, visión y supervisor.
- `backend/`: API FastAPI/licensing/telemetry/admin.
- `scripts/AgentMax_test_server.py`: servidor local de primera corrida con tokens, dataset y endpoints OpenAI-compatible.
- `scripts/dataset/`: generadores/auditores de dataset SFT.
- `tests/`: pruebas Python de core, tools, seguridad, memoria, UI compatibility.
- `models/` y `AgentMax_V2_1_ADAPTER_FOR_BETA.tar.gz`: assets/model adapter locales.

## Capacidades Existentes

- Chat principal en `ui/src/components/MainWindow/MainWindow.tsx`.
- Estado de servidor, modelo, tokens y dataset desde `http://127.0.0.1:7790`.
- Screenshot real en Tauri mediante `screenshot_base64`.
- Shell sandboxed en Rust mediante `run_shell_command`.
- Registry Python en `core/ai/tools.json` y `core/tools/registry.py`.
- Supervisor/seguridad parcial en `core/agents/security_agent.py` y `core/tools/executor.py`.
- Token manager Python en `core/security/token_manager.py`.
- Dataset local de prueba en `scripts/AgentMax_test_server.py`.

## Brechas Encontradas

- La UI no mostraba una cabina completa tipo Codex/Claude Code con thinking, checklist, tools y screenshot en un solo flujo.
- Tokens existían en servidor de prueba/core, pero faltaba arquitectura frontend clara `tokenService`/`usageStore`/config de planes.
- Faltaba sanitizer frontend explícito para filtrar `<think>` antes de mostrar respuestas.
- Faltaba supervisor central testeable e independiente para políticas de tools locales.
- Faltaban scripts formales para redacción/auditoría/construcción de dataset desde logs reales aprobados.
- Faltaba modo rescate externo documentado y ejecutable.
- OCR/browser quedan como unavailable/stub seguro si no hay conector instalado.

## Riesgos

- `core/security/*` tiene archivos borrados en el worktree antes de esta intervención; no se revirtieron.
- `BetaAgentMax/tauri-viz/node_modules` está dentro del repo y puede inflar búsquedas/builds si se incluye accidentalmente.
- El servidor `scripts/AgentMax_test_server.py` es útil para beta, pero no sustituye un backend productivo.
- Tauri/Rust usa permisos nativos sensibles; cualquier tool de input debe pasar por aprobación y user-idle.
- Los modelos locales pueden no estar disponibles; la UI debe mostrar `model unavailable` o endpoint configurable.

## Comandos Para Correr

```powershell
cd C:\path\to\AgentMax   ;# user-specific paths removed for any-installer compatibility
python scripts\AgentMax_test_server.py
cd ui
npm run dev
npm run build
cd src-tauri
cargo check
```

## Rutas Importantes

- UI principal: `ui/src/components/MainWindow/MainWindow.tsx`
- Estado UI: `ui/src/store/agentStore.ts`
- Tokens frontend: `ui/src/lib/tokenService.ts`, `ui/src/store/usageStore.ts`
- Tools frontend: `ui/src/lib/toolRegistry.ts`, `ui/src/lib/toolSupervisor.ts`
- Screenshot frontend: `ui/src/lib/screenshotService.ts`
- Sanitizer frontend: `ui/src/lib/responseSanitizer.ts`
- Supervisor Python: `core/tools/safety_supervisor.py`
- Sanitizer Python: `core/ai/response_sanitizer.py`
- Redactor Python: `core/data_collection/redactor.py`
- Rescue helper: `scripts/rescue/AGENTMAX_rescue_mode.py`

## Mejoras Priorizadas

1. Mantener supervisor en el camino de ejecución para toda tool destructiva.
2. Conectar `AgentMax V2.1` mediante endpoint configurable y estado visible de modelo.
3. Convertir logs approved en dataset real con revisión humana.
4. Añadir tests E2E de UI/Tauri cuando el entorno lo permita.
5. Separar server de prueba de runtime productivo antes de distribución pública.
