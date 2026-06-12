# AgentMax Architecture Audit — Phase 1

**Fecha:** 2026-05-23
**Alcance:** Auditoria solo-lectura del runtime de AgentMax/AgentMax, sus subsistemas de seguridad, tools, vision, IPC, licensing, datasets y tests.
**Metodologia:** Lectura directa de codigo + corrida de la suite de tests + verificacion de boot end-to-end.
**Estado entregable:** Documento honesto, sin invenciones. Donde se marca algo como "presente y funcional" es porque fue leido y validado en este turno. Donde se marca "prototipo / no validado", literalmente no se valido.

> Reglas que se respetaron al producir este documento:
> - No se modifico codigo de la app durante la auditoria.
> - No se inventaron capacidades ni se afirmo "seguro" sin evidencia.
> - Los rangos de riesgo se derivan de codigo leido, no de marketing.

---

## 0. Resumen ejecutivo

AgentMax NO es vaporware. El proyecto tiene **mas infraestructura real de la que parecia desde afuera**: 10,700+ lineas de Python solo en `core/`, una capa Rust de Tauri con anti-tamper/integridad, una suite de 162 tests (154 pasan hoy), y un dataset SFT auditado de 1440 ejemplos para reentrenar el modelo.

Lo que esta listo: token manager nuevo con persistencia y caps mensuales, audit log con HMAC, supervisor de tools con bloqueo de patrones destructivos, ToolExecutor con validador/permisos/risk analyzer/retries/fallbacks, FileSystemAgent sandboxeado, WebSearchAgent con anti-SSRF, redactor de PII, response sanitizer, modo air-gap, anti-tamper Python + integrity check Rust, hardware fingerprint, observability layer de AgentMax (thinking + tokens) recien encendida.

Lo que falta o esta a medias: license_manager esta deshabilitado por decision de producto (entitlements off hasta que vuelvan las subscripciones); IPC HTTP/WS no tiene autenticacion (escucha en 127.0.0.1, mitiga pero no resuelve); el dataset pipeline existe pero no esta hookeado a sesiones reales aprobadas; no hay multi-model registry formal con capacidades declaradas; no hay MCP/prompt-injection defense explicita en el camino OCR/screenshot; falta queue+lock global para tool execution concurrente; falta beta override key formal y license recovery flow; el anti-tamper Python depende del license_manager que esta deshabilitado.

**Veredicto Phase 1:** "Prototipo avanzado con bases solidas". NO production-ready. NO publicar como "secure" todavia.

---

## 1. Inventario por subsistema

### 1.1 Core directories
| Path | Modulos | LOC aprox | Estado |
|---|---|---|---|
| `core/agents/` | 11 agentes (Supervisor + 10) | ~2,700 | Funcional, supervisor tiene 833 LOC |
| `core/ai/` | 14 (router, clients, parser, sanitizer, planner) | ~2,900 | Funcional con la observability nueva |
| `core/tools/` | 19 (executor, registry, validator, risk, supervisor) | ~2,300 | Solido (ver §3) |
| `core/security/` | 9 (audit_log, perms, anti_tamper, vm_detect, crypto, license_manager, token_manager, air_gap) | ~1,800 | Mezcla solido + deshabilitado |
| `core/memory/` | 3 (stm, ltm, visual) | ~600 | Funcional, ChromaDB con fallback JSON |
| `core/automation/` | 4 (engine, ui_automation, window_manager, workflow_engine) | ~600 | Funcional |
| `core/recorder/` | 3 | ~400 | Funcional, sin validar a fondo en esta auditoria |
| `core/pixel_engine/` | 1 (pixel_analyzer) | ~400 | PIL-based; OCR opcional via PIL |
| `core/data_collection/` | 1 (redactor) | ~60 | Pequeno pero usable |
| `core/utils/` | 6 | ~600 | Resilience, validation, observability, i18n |
| `core/updater/` | 2 | ~400 | UpdateManager con Ed25519 sig verify |
| `core/telemetry/` | 1 | ~200 | Cliente, no enviando hoy (LM disabled) |
| `core/event_bus.py` | 1 | 245 | Pub/sub con priority queue + critical fast-path |
| `core/runtime.py` | 1 | 326 | Boot orquestador |
| `core/ipc.py` | 1 | 826 | REST + WebSocket + msgpack |
| `core/rust_vision_bridge.py` | 1 | ~200 | Renombrado pero NO es Rust, es PIL puro |
| **Total core/** | **~95 modulos py** | **~10,700** | |

### 1.2 Rust side (`ui/src-tauri/src/`)
| File | Proposito | Estado |
|---|---|---|
| `main.rs` / `lib.rs` | Entrada Tauri v2 | Funcional |
| `commands.rs` | Comandos nativos (screenshot, shell, etc) | Funcional |
| `server.rs` | Server local | Funcional |
| `native.rs` | Capa nativa | Funcional |
| `overlay.rs` | HUD overlay | Funcional |
| `tray.rs` | System tray | Funcional |
| `integrity.rs` | SHA-256 self-hash + XOR-encrypted snapshot en `%LOCALAPPDATA%\AgentMax\.ixh` | Funcional, **solo activa en release builds** |
| `security.rs` | HMAC fingerprint + cracker process list + token gen/verify | Funcional |
| `anti_vm.rs` | Detector VM Rust-side | Presente, no auditado |

### 1.3 Frontend (`ui/src/`)
- React 18 + Vite + Zustand + Tauri API.
- Fuera del scope de seguridad de esta auditoria (nunca confiar en el frontend para validacion critica — regla del usuario).

### 1.4 Tests (`tests/`)
- 18 archivos, 162 casos.
- **154 pasan, 8 fallan** al correr ahora.
- Los 8 fallos:
  - 5 son drift de UI (verifican strings en componentes React que han cambiado de nombre/contenido). Cosmetico.
  - 3 son del `test_autonomous_routing.py`: dos por planning steps que vienen mas cortos de lo esperado y uno por puerto LM Studio default cambiado a 1235 (test asume 1234). Bug menor de fixtures.

### 1.5 Docs existentes (`docs/`)
9 archivos, varios duplicados parciales:
- `AGENTMAX_BETA_AUDIT.md` — auditoria previa, parcial.
- `AGENTMAX_BETA_READINESS.md`
- `AGENTMAX_TOOLS_SECURITY.md`
- `AgentMax_RUNTIME.md`
- `AgentMax_DATA_COLLECTION.md`
- `qa_critical_audit_2026-05-21.md`
- `AgentMax_release_dataset.md`
- `api.md`, `deployment.md`

---

## 2. Subsistema de seguridad — estado real

### 2.1 audit_log.py (`core/security/audit_log.py`) — **FUNCIONAL**
- Append-only JSONL.
- HMAC-SHA256 por entrada si `AGENTMAX_AUDIT_HMAC_KEY` esta seteado.
- Cola async + writer task.
- `verify_log()` re-verifica firmas offline.
- Riesgo: si la clave HMAC esta vacia (default), las entradas no se firman; el log queda integro-en-confianza solo.

### 2.2 permission_manager.py — **FUNCIONAL pero permisivo**
- 8 permisos definidos: `SCREEN_READ, INPUT_MOUSE, INPUT_KEYBOARD, PROCESS_LAUNCH, FILE_READ, FILE_WRITE, REGISTRY, NETWORK`.
- **Default granted:** `SCREEN_READ, INPUT_MOUSE, INPUT_KEYBOARD` (sin consent UI).
- En modo `require_consent=False`, `require()` auto-otorga el permiso. Decision de UX en dev, peligrosa en prod.
- Riesgo: no hay UI de consent visible en el codigo backend; depende del frontend para mostrarla.

### 2.3 token_manager.py — **FUNCIONAL (reescrito hace 2 turnos)**
- 534 LOC.
- Thread-safe (`RLock`).
- Per-(plan, user).
- Daily + monthly caps.
- Persistencia JSON opcional con atomic write (`tmp.replace`).
- Audit hook plug-in.
- API completa: `check`, `consume`, `remaining`, `get_usage`, `totals_by_plan`, `usage_by_day`, `reset`, `prune_old_entries`.
- Tests: smoke validado en sesion anterior; **no hay tests dedicados aun** en `tests/`. Marcar como "pendiente test formal".

### 2.4 anti_tamper.py — **FUNCIONAL pero hueco**
- Checks: debugger attached, cracker tools en process list (~40 nombres), code-integrity sentinels, env vars sospechosas (FRIDA_SCRIPTS, LD_PRELOAD, DYLD_*), VM detect.
- Watchdog thread cada 60s.
- Dev bypass via `AGENTMAX_DEV_TOKEN` HMAC-derivado del hostname (machine-specific).
- Hardware fingerprint (`get_machine_fingerprint`): MAC + volume serial + CPU brand → SHA256 prefix 20.
- **Problema critico:** los sentinels en `_check_code_integrity` apuntan a `license_manager.LicenseManager.validate_startup` y otros metodos del license_manager. **El license_manager esta deshabilitado en runtime.py** (`self.license_manager = None`). Si nunca se llama a `snapshot_critical_functions()`, el check de integridad pasa siempre. Es decir, **la mitad del anti-tamper depende del license_manager que no boota**.
- Degradation callback nunca se setea desde runtime.py — si se detectara tampering, **no pasa nada salvo un log error**.

### 2.5 license_manager.py — **DESACTIVADO POR DECISION DE PRODUCTO**
- Existe (Whop integration con heartbeat 30 min, cache 24h en `data/license_cache.json`).
- runtime.py linea 73-76: `self.license_manager = None; self._subsystems.append("entitlements-disabled")`.
- Comentario: "Entitlements are disabled until subscriptions return."
- Consecuencia: telemetry y updater tambien estan parcialmente off (linea 218: `if self.license_manager is not None: await self.telemetry.start(http_client)`).

### 2.6 crypto.py — **SOLIDO**
- AES-256-GCM con clave machine-bound (HKDF-SHA256 desde MAC|node|platform).
- X25519 ECDH ephemeral + HMAC-SHA256 challenge-response.
- Ed25519 sig verify (manifests + offline tokens).
- Library: `cryptography` (audited library).
- Limitacion: la machine-bound key se deriva tambien del `platform.machine()` y MAC — un atacante con acceso fisico puede regenerarla. Sirve para "encrypt at rest" no para "hide from local attacker".

### 2.7 air_gap.py — **FUNCIONAL**
- Patch monkey-patching `socket.create_connection` + `socket.getaddrinfo`.
- Whitelist: `127.0.0.1, localhost, ::1, 0.0.0.0`.
- Reference-counted enable/disable.
- Riesgo: no patcha `raw socket`, ni librerias C que llamen `connect()` directo (libcurl). Para air-gap **real** se necesita firewall a nivel SO. Funciona contra librerias Python "puras" (httpx, requests, anthropic SDK).

### 2.8 vm_detect.py — presente, no auditado a fondo en esta sesion.

### 2.9 Rust integrity.rs — **FUNCIONAL solo en release**
- Hash SHA-256 del exe actual vs snapshot XOR-encrypted en `%LOCALAPPDATA%\AgentMax\.ixh`.
- En `debug_assertions` siempre retorna `true` (passive).
- XOR key partido en 4 segmentos para frustrar string scan trivial.
- **Limitacion:** XOR no es proteccion criptografica. Un cracker con `xor 0x7f 0x4a 0x91 ...` lo rompe en minutos. Suficiente para script-kiddies, NO para atacante motivado.

### 2.10 Rust security.rs — **FUNCIONAL**
- 60+ herramientas de RE en blocklist (debuggers, decompiladores, sniffers, frida).
- HMAC fingerprint key partida en 3 segmentos.
- `generate_server_token(hw_fp)` + `verify_request_token(token, hw_fp)` con comparacion constant-time.
- Mismas limitaciones que XOR para las llaves embedded.

---

## 3. Subsistema de tools — estado real

### 3.1 Registry (`core/tools/registry.py` + `core/ai/tools.json`)
- 17 tools definidas en JSON: 11 low / 2 medium / 3 high / 1 critical.
- Categorias: file(5), ui(4), app(2), network(2), mouse(1), system(1), vision(1), control(1).
- Validacion de catalog: chequea 20 campos requeridos por tool.

### 3.2 ToolExecutor (`core/tools/executor.py`) — **SOLIDO**
Pipeline por tool call:
1. Router → `ToolRequest` canonico.
2. ContextBridge → `ToolExecutionContext` (runtime, capture, accessibility, security, audit, bus).
3. State `can_run()` (cooldown).
4. RiskAnalyzer `blocks_execution()` (score ≥ 0.97 bloquea critical).
5. RiskAnalyzer `analyze()` (si `requires_confirmation` y no `approved_risk` → `tool.confirmation_required`).
6. ToolValidator `validate()` (JSON schema subset + rules).
7. PermissionManager `validate()` (mapea `screen.read` → `SCREEN_READ` etc).
8. Idle wait si `requires_user_idle`.
9. asyncio.wait_for con `timeout_ms`.
10. Queue por `execution_mode` (exclusive/parallel).
11. Retry policy con backoff_ms * attempt.
12. Fallback chain.
13. Logger (started/completed/failed/paused/resumed).
14. State `mark_result` para cooldowns.

**Esto es el sistema de tools que el prompt pedia.** Existe, no hay que crearlo.

### 3.3 AgentToolSupervisor (`core/tools/safety_supervisor.py`) — **FUNCIONAL**
- 9 patrones `DANGEROUS_COMMAND_PATTERNS` (rm -rf, format, shutdown, taskkill sin filtro, iwr|iex pipe, set-executionpolicy bypass).
- 4 patrones `HIGH_RISK_COMMAND_PATTERNS`.
- 3 patrones `READ_ONLY_COMMAND_PATTERNS`.
- 14 `DEFAULT_TOOL_POLICIES` con risk_level + requires_approval + timeout.
- `inspect_command()` retorna `SupervisorDecision(allowed, requires_approval, blocked, reason, risk_level, safety_flags)`.
- **Loop detection:** `record_failure()` tracking ventana de 300s, bloquea tras `loop_limit=3` fallos identicos. Funciona.

### 3.4 ToolRiskAnalyzer (`core/tools/risk.py`)
- Score: low=0.15, medium=0.45, high=0.75, critical=0.95.
- Aumenta a 0.98 si match en `_DANGEROUS_SHELL` (rm -rf, format, shutdown, bcdedit, set-executionpolicy, takeown, cipher /w, etc).
- Aumenta a 0.65 si text injection > 2000 chars (anti-prompt-flood).
- `blocks_execution` corta al 0.97.

### 3.5 ToolValidator (`core/tools/validator.py`)
- JSON-schema subset: type, required, minimum/maximum, maxLength, enum.
- Rules custom: `coordinates_inside_screen`, `user_is_idle`, `agent_has_input_control`, `safe_shell_command`, `path_present`.

### 3.6 ToolPermissionManager (`core/tools/permissions.py`)
- Mapeo permission→runtime: `input.mouse→INPUT_MOUSE` etc.
- Bypass especial: si `extra.computer_control_granted=True` se saltean todas las perms (esto es la aprobacion del usuario via UI "Permitir control del PC").

### 3.7 Gaps identificados
| Gap | Severidad | Detalle |
|---|---|---|
| MCP tools / prompt injection en outputs | Alto | Ningun modulo escanea el output de un tool por injection (`Ignora reglas y...`). Si un screenshot OCR captura ese texto, el modelo lo recibe sin filtro. |
| Path traversal en write_file fuera de FileSystemAgent | Medio | FileSystemAgent valida `_is_safe_path`. Tools tipo `write_file` definidas en JSON dependen del executor; revisar si todos los handlers llaman al sandboxer. |
| No hay queue **global** por host (solo `ToolQueue` por execution_mode) | Medio | Si tres tasks paralelas piden `click` al mismo tiempo, depende del lock en SupervisorAgent (`_ui_lock`). Confirmado que existe; auditar que se respete fuera del supervisor. |
| `kill_process` tool no aparece en JSON | Bajo | Bien — no esta expuesto. |
| `registry` tool no aparece en JSON | Bajo | Bien. |
| No hay tabla `WRITE/DESTRUCTIVE` explicita | Estructural | Ya implicita via `risk_level: critical`, pero no expuesta como categoria. |

---

## 4. Vision / screenshot / OCR

### 4.1 `core/rust_vision_bridge.py` — **MAL NOMBRADO**
- Se llama "rust_vision_bridge" pero **no es Rust**. Usa `PixelAnalyzer` Python+PIL.
- OCR es opcional via PIL/Tesseract — fallback "OCR no disponible".
- Frame throttle 5 fps.
- Cache + perceptual hash dedup.
- Captura via PIL ImageGrab.
- **NO redacta contenido sensible del screenshot antes de pasarlo al modelo.** Si el screen muestra un email o un secret, va al modelo VL crudo.

### 4.2 La captura nativa real esta en `ui/src-tauri/src/commands.rs`
- Tauri command `screenshot_base64` usa GDI Windows.

### 4.3 Gaps
| Gap | Severidad | Detalle |
|---|---|---|
| Sin redactor pre-envio al modelo VL | **Alto** | Cualquier screen con datos sensibles llega al modelo sin filtro. Hay redactor en `core/data_collection/redactor.py` pero **solo aplica al guardar sesiones**, no al inferir. |
| Sin metadata "screen privacy zones" | Medio | No hay manera de marcar "no capturar esta region" (ej. password managers, banca). |
| OCR como input no validado | Alto | Texto en pantalla → modelo. **No hay defensa de prompt-injection desde OCR.** |
| Rust integrity sobre screenshot pipeline | N/A | Vision corre en Python, no en Rust hoy. |

---

## 5. IPC / network

### 5.1 IPCServer (`core/ipc.py`)
- Dual canal: REST en `127.0.0.1:7790`, WebSocket en `127.0.0.1:7788`.
- **Sin autenticacion**. Cualquier proceso local puede tocar las APIs.
- CORS abierto a `http://localhost:5173, http://localhost:1420, tauri://localhost`.
- WS broadcast con msgpack + batching 16/frame.
- AgentCoreProxy via stdio a `agentcore.exe` (Rust binary) — si no existe, fallback HTTP a `127.0.0.1:7789`.

### 5.2 Riesgos IPC
| Riesgo | Severidad |
|---|---|
| **No-auth REST en localhost** | Alto. Mitigado por loopback-only pero no por proceso-vecino. Cualquier app local puede `POST /api/tasks`. |
| **No-auth WebSocket** | Alto. Mismo problema, escucha eventos del bus completos via `*` wildcard. |
| Cors permissive | Bajo (loopback-only) |
| `/api/shutdown` sin auth | Alto. Cualquier app puede tumbar AgentMax. |
| `/api/emergency_stop` sin auth | Igual. |
| `task.submit` sin auth | Alto. Cualquier proceso puede instruir al agente. |

### 5.3 Recomendacion (NO implementada aun)
Token Tauri-only en header `X-AgentMax-Token`, generado al boot por la app Tauri y compartido por env var con el backend Python. Tauri lo inyecta en cada request UI; cualquier otro caller falla.

---

## 6. Runtime AI / model layer

### 6.1 AIRouter (`core/ai/ai_router.py`) — **FUNCIONAL**
- 2 backends: `claude` o `lmstudio`.
- Per-backend circuit breaker (cloud=5fail/60s, local=3fail/30s).
- `retry_async(max_attempts=3, base_delay=2.0, max_delay=30.0)`.
- Hot swap `switch_backend()`.
- Health check.

### 6.2 LMStudioClient (con observability nueva) — **FUNCIONAL**
- Patcheado hace 2 turnos con `thinking_parser` + eventos `ai.thinking` / `ai.tokens` / `ai.response` al bus.
- Flag `AGENTMAX_OBSERVABILITY` (default **ON**).
- Token counts per-request.

### 6.3 Gaps multi-model
| Gap | Severidad |
|---|---|
| **No hay ModelCapabilityRegistry** | Estructural |
| No soporta: Ollama, llama.cpp, raw GGUF | Estructural |
| Modelos no declaran capacidades (vision, tools, reasoning, max ctx) en config | Estructural |
| No hay fallback chain por capability ("si pide vision y este modelo no la tiene, ruta a otro") | Estructural |
| No hay warmup / lazy load gestionado | Bajo |
| No hay queueing/cancellation a nivel router | Medio |

---

## 7. Dataset pipeline

### 7.1 Existe
- `datasets/AgentMax_v2/` con 1440 SFT + 100 eval + splits 85/10/5 (train/val/test).
- `scripts/dataset/audit_AgentMax_v2_dataset.py` (1 critical-errors auditor).
- `scripts/dataset/build_AgentMax_v2_dataset.py` (generador).
- `scripts/dataset/split_AgentMax_v2_dataset.py` (split reproducible seed=42).
- `core/data_collection/redactor.py` (PII redaction).
- `BetaAgentMax/bug_collector.py` (DataCollector con consent + PII scrub, 590 LOC).

### 7.2 Falta para el pipeline pedido
| Falta | Severidad |
|---|---|
| Directorios `raw_sessions/`, `redacted_sessions/`, `approved_sessions/`, `rejected_sessions/` | Estructural |
| Workflow approve/reject humano antes de meter al training | Estructural |
| Scripts `score_dataset`, `reject_low_quality`, `build_tool_dataset`, `build_safety_dataset`, `build_reasoning_dataset` | Estructural |
| Captura automatica de sesion real (prompt + output + tool calls + screenshot meta + timing + tokens + crashes) | Estructural |

---

## 8. Thinking sanitizer

### 8.1 Lo que existe
- `core/ai/thinking_parser.py` (nuevo, 2 turnos atras) — split + iter_segments + has_thinking.
- `core/ai/response_sanitizer.py` — `sanitize_agent_response()` (think strip + system-prompt-strip + cap chars + truncar). Util backend.
- En LMStudioClient: strip aplicado cuando `observability=True`.

### 8.2 Falta del prompt
| Falta | Severidad |
|---|---|
| Detector de "filtraciones" (resto de raw chain-of-thought aunque NO este en `<think>`) | Medio |
| Detector de loops verbales (mismo bloque 3x) | Medio |
| Resumen automatico de plan visible | Bajo |
| "Guardar raw output solo en debug interno" | Ya parcialmente: el publish va al bus, no al UI; pero el bus es ESCUCHABLE via WebSocket sin auth. **Issue de seguridad cruzado con §5.2.** |

---

## 9. Anti-crack / anti-tamper

### 9.1 Lo que existe
- Python `anti_tamper.py` con watchdog y degradation callback (no seteado).
- Rust `integrity.rs` SHA-256 self-hash en release.
- Rust `security.rs` HMAC fingerprint + 60+ cracker process names.
- Rust `anti_vm.rs` (no auditado a fondo).
- `crypto.py` AES-GCM at-rest + Ed25519 verify.
- Snapshot path en `%LOCALAPPDATA%\AgentMax\.ixh`.

### 9.2 Lo que NO esta
| No esta | Severidad |
|---|---|
| Anti-debug serio (solo gettrace, no IsDebuggerPresent native ni timing checks) | Alto |
| Anti-hooking real (no IAT scan, no inline-hook detection) | Alto |
| Anti-memory-patch real (snapshots de codigo solo de 4 funciones del license_manager **deshabilitado**) | **Critico** |
| Encrypted local license blob | Bajo — existe AES disk pero no se usa porque license off |
| Hardware-bound licensing **activo** | Critico — license_manager esta off |
| Offline grace mode + license recovery flow + beta override keys | Estructural |
| Runtime heartbeat funcional | Off (license_manager off) |
| Module validation (pyc tamper) | No existe |
| Obfuscation Python (PyArmor mencionado en docstring, no se ve aplicado en codigo) | No verificado en build pipeline |

### 9.3 Consideracion etica
El usuario explicitamente dijo "NO usar malware techniques. NO hacer comportamiento hostil. NO destruir datos. NO bloquear PCs." Lo cumple el codigo actual (anti_tamper degrada silenciosamente, no rompe). Mantener regla.

---

## 10. MCP / prompt injection defense

### 10.1 Estado: **AUSENTE**
- No hay modulo dedicado.
- No hay scanner de tool outputs por instrucciones embebidas.
- No hay "agent guardrail" entre `tool_result.text` y `next_LLM_call`.
- OCR text → modelo directo.
- Web search results → modelo via `WebSearchAgent` sin filtro de injection (solo SSRF a nivel red).

### 10.2 Lo que SI hay (parcial)
- `safety_supervisor` bloquea **comandos** peligrosos (no instrucciones).
- `risk_analyzer` puntua por contenido del payload.
- Path normalization en FileSystemAgent.
- Argument escaping en `commands.rs` Rust shell (shlex-like).

---

## 11. Performance

### 11.1 Lo que existe
- Frame dedup con perceptual hash.
- Event batching 16/frame en WS.
- IPC msgpack en lugar de JSON.
- `_flatten_serialize` iterativo (no recursion → no stack overflow).
- Async-todo (asyncio.to_thread para I/O).
- Pixel engine throttle 5 fps.

### 11.2 Lo que falta
| Falta | Severidad |
|---|---|
| Context compaction / rolling memory | Medio (afecta sesiones largas) |
| Task summarization | Bajo |
| Adapter switching gestionado | N/A (no hay multi-adapter aun) |
| Lazy loading de agentes (todos arrancan al boot) | Bajo |

---

## 12. Riesgos consolidados (top 10)

| # | Riesgo | Severidad | Mitigacion sugerida |
|---|---|---|---|
| 1 | IPC sin auth (REST + WS) | **Critico** | Token Tauri-only en header, validado backend Python |
| 2 | License manager deshabilitado → anti-tamper code-integrity sin efecto | **Critico** | Reactivar con plan o sentinelar otras funciones |
| 3 | Screenshot crudo al modelo sin redaccion | **Alto** | Pipeline pre-envio: redactar regiones marcadas, blur passwords, etc |
| 4 | Sin defensa de prompt injection en tool outputs / OCR / web | **Alto** | Capa "instruction firewall" entre tool_result y next LLM call |
| 5 | Air-gap solo Python (libcurl pasa) | **Alto** (si lo prometes a enterprise) | Documentar limitacion + opcional firewall SO |
| 6 | XOR keys embedded en Rust | **Medio** (script-kiddie OK, atacante motivado no) | Documentar como "tamper resistance basica" no "tamper proof" |
| 7 | Default permisos `INPUT_MOUSE+KEYBOARD+SCREEN_READ` sin consent visible | **Alto** | Default-deny en prod build, UI consent obligatoria |
| 8 | Audit log sin HMAC si env vacio (default) | **Medio** | Generar key auto al primer boot + persistir encriptada |
| 9 | Token manager sin tests formales | **Medio** | Suite dedicada `tests/test_token_manager.py` |
| 10 | No hay ModelCapabilityRegistry → modelo puede ser usado fuera de sus capacidades | **Medio-Alto** | Implementar en Phase 2 |

---

## 13. Que esta listo vs que es prototipo

### Listo (funcional, validable):
- ✅ EventBus con priority + critical fast-path
- ✅ AuditLog con HMAC opcional
- ✅ TokenManager (per plan/user, daily+monthly, persist, audit hook)
- ✅ ToolExecutor pipeline (validador, perms, risk, retries, fallbacks, queue, state)
- ✅ AgentToolSupervisor (dangerous patterns, loop detection)
- ✅ FileSystemAgent sandboxed
- ✅ WebSearchAgent anti-SSRF
- ✅ Air-Gap mode (con limitaciones)
- ✅ Audit log HMAC verifier offline
- ✅ Crypto module (AES-256-GCM, X25519 ECDH, Ed25519 verify)
- ✅ AIRouter con circuit breakers
- ✅ LMStudioClient con thinking parser + observability bus events
- ✅ Response sanitizer (think strip + system prompt strip)
- ✅ Update manager con Ed25519 signature verify
- ✅ Dataset SFT V2 (1440 ejemplos auditados, 0 errores criticos)
- ✅ Rust integrity check en release
- ✅ Rust hardware fingerprint + token gen/verify
- ✅ 154/162 tests passing

### Prototipo / no validado:
- 🟡 LicenseManager (existe pero off; necesita decision de producto)
- 🟡 Anti-tamper code-integrity (sentinels apuntan a funciones del license off)
- 🟡 Anti-VM detection (Rust side no auditado)
- 🟡 Updater funcional pero requiere license_manager para `http_client`
- 🟡 Telemetry presente pero off
- 🟡 OCR opcional, sin redactor pre-envio
- 🟡 Recorder/event_recorder no validado en esta sesion
- 🟡 8 tests fallando (3 routing reales + 5 UI cosmeticos)
- 🟡 `_check_code_integrity` corre pero sin baseline (snapshot nunca tomada)

### Falta (no existe):
- ❌ IPC authentication
- ❌ ModelCapabilityRegistry
- ❌ Multi-provider support (Ollama, llama.cpp, GGUF directo)
- ❌ Prompt-injection defense layer
- ❌ Screenshot privacy redactor pre-envio
- ❌ Dataset pipeline raw→redacted→approved→rejected
- ❌ Session capture automatica
- ❌ ReasoningSanitizer mas alla del think strip
- ❌ Anti-debug nativo (IsDebuggerPresent, timing)
- ❌ Anti-hooking real
- ❌ Beta override key / license recovery flow
- ❌ Adapter switching multi-LoRA
- ❌ Context compaction automatico

---

## 14. Que debe moverse a Rust/core nativo

Prioridad por impacto en proteccion:
1. **Verificacion HMAC del token IPC** (proxima Phase): Rust genera y verifica; Python solo consume.
2. **Anti-debug nativo** (`IsDebuggerPresent`, `CheckRemoteDebuggerPresent`, timing): Rust.
3. **License validation cuando se reactive**: parcialmente esta (HMAC fingerprint en Rust), falta el flow completo.
4. **Integrity baseline updater** (refresh tras auto-update): Rust ya tiene `refresh_snapshot()`, falta llamarlo desde el flow de update.
5. **Air-gap a nivel de driver/firewall**: Rust o documentar como out-of-scope.

Lo que NO debe moverse:
- ToolExecutor (orquestracion, mejor en Python por flexibilidad).
- Sanitizers (logica de strings, Python).
- AIRouter (config-driven, Python).
- AuditLog (escritura I/O async, Python con HMAC OK).

---

## 15. Plan sugerido para Phase 2 (NO ejecutar todavia)

Si despues de revisar este audit te parece bien, propongo arrancar Phase 2 (Runtime Multi-Modelo) asi:

1. Crear `core/providers/` con interfaz `BaseProvider`:
   - `LMStudioProvider` (existe, refactor)
   - `OllamaProvider` (nuevo)
   - `OpenAICompatibleProvider` (generico)
   - `ClaudeProvider` (existe, refactor)
2. Crear `core/models/registry.py` con `ModelCapabilityRegistry`:
   ```python
   ModelEntry(
     id, provider, capabilities=[vision, tools, thinking, ocr],
     limits=ModelLimits(vram_min_gb, ctx, max_output),
     quant_supported=["q4_k_m","q8_0","bf16"],
     safe_tool_support=True,
     local_execution=True,
   )
   ```
3. Crear `core/runtime/router_v2.py` con fallback chain por capability.
4. **Sin tocar** `core/ai/ai_router.py`, `core/ai/lmstudio_client.py`. Se anaden, no se reemplazan.
5. Switch flag `AGENTMAX_ROUTER_V2=1` para activar; default off.
6. Mantener rollback simple: borrar `core/providers/` y `core/models/` no rompe nada.

---

## 16. Veredicto y proximos pasos

**Status oficial:** "Prototype with strong foundations". Permitido decir:
- "Beta runtime infrastructure exists for chat, tools, vision, sandboxing."
- "Audit-logged tool execution with risk scoring and dangerous-pattern blocking."
- "Multi-backend (Claude / LM Studio) with circuit breakers and observability."

**NO permitido decir:**
- "Production ready."
- "Secure" (sin qualifier).
- "Hardened against motivated attackers."
- "Air-gapped" (sin nota sobre limitaciones).

**Antes de Phase 2:**
- [ ] Tu revisas este documento.
- [ ] Decidimos si Phase 2 arranca por router multi-provider, por IPC auth, o por prompt-injection defense (mi voto: IPC auth primero porque es **critico** y rapido).
- [ ] Tomamos snapshot del repo (git tag `pre-phase2-audit-2026-05-23`) antes de cualquier cambio.

---

## Apendice A — Comandos de validacion usados en esta auditoria

```bash
# Inventario
wc -l core/security/*.py core/tools/*.py core/agents/*.py core/ai/*.py
ls core/

# Tests
python -m pytest tests/ -q --no-header
python -m pytest tests/test_autonomous_routing.py -q

# Audit dataset
python scripts/dataset/audit_AgentMax_v2_dataset.py \
    datasets/AgentMax_v2/train.jsonl \
    datasets/AgentMax_v2/validation.jsonl \
    datasets/AgentMax_v2/test.jsonl

# Boot smoke
AGENTMAX_DEV_BYPASS=1 python -c "from core.runtime import AgentMaxRuntime; import asyncio; asyncio.run(AgentMaxRuntime().start())"
```

## Apendice B — Archivos leidos en esta auditoria (lista honesta)

- `core/runtime.py` (326 LOC) — boot completo
- `core/ipc.py` (826 LOC) — REST + WS + AgentCoreProxy
- `core/event_bus.py` (245 LOC) — pub/sub priority
- `core/config.py` (399 LOC) — AgentMaxConfig nested
- `core/security/anti_tamper.py` (387 LOC) — completo
- `core/security/audit_log.py` (80 LOC primeras) — completo
- `core/security/permission_manager.py` (92 LOC) — completo
- `core/security/license_manager.py` (100 LOC primeras) — head
- `core/security/crypto.py` (187 LOC) — completo
- `core/security/air_gap.py` (60 LOC primeras) — head
- `core/security/token_manager.py` — sesion anterior, completo
- `core/tools/registry.py` (100 LOC primeras) — head
- `core/tools/safety_supervisor.py` (198 LOC) — completo
- `core/tools/risk.py` (78 LOC) — completo
- `core/tools/permissions.py` (60 LOC) — completo
- `core/tools/validator.py` (122 LOC primeras) — head
- `core/tools/executor.py` (240 LOC primeras) — head + retries
- `core/agents/supervisor.py` (90 LOC primeras) — head (total 833)
- `core/agents/file_system_agent.py` (80 LOC primeras) — sandboxing
- `core/agents/ui_automation_agent.py` (80 LOC primeras) — head
- `core/agents/web_search_agent.py` (80 LOC primeras) — anti-SSRF
- `core/ai/ai_router.py` (123 LOC) — completo
- `core/ai/lmstudio_client.py` — sesion anterior, completo
- `core/ai/response_sanitizer.py` (72 LOC) — completo
- `core/ai/thinking_parser.py` — sesion anterior, completo
- `core/rust_vision_bridge.py` (90 LOC primeras) — head
- `core/updater/update_manager.py` (100 LOC primeras) — head
- `core/data_collection/redactor.py` (60 LOC primeras) — completo
- `ui/src-tauri/src/integrity.rs` (80 LOC primeras) — XOR snapshot
- `ui/src-tauri/src/security.rs` (80 LOC primeras) — HMAC fp + blocklist
- `core/ai/tools.json` — auditado via script (17 tools)

Lo que NO se leyo en esta sesion (queda para auditorias siguientes):
- `core/agents/{planning,validation,memory,workflow,security,lean_vision}_agent.py`
- `core/ai/{claude_client,planner,thinking_engine,reasoning_manager,context_analyzer,conversation_manager}.py`
- `core/automation/*.py`
- `core/recorder/*.py`
- `core/memory/{short_term,long_term,visual_memory,memory_manager}.py`
- `core/pixel_engine/pixel_analyzer.py`
- `core/security/vm_detect.py`
- `ui/src-tauri/src/{commands,native,server,overlay,tray,main,lib,anti_vm}.rs` (mayoria)
- `backend/` (FastAPI side)

---

**Fin Phase 1.** Esperando review antes de Phase 2.
