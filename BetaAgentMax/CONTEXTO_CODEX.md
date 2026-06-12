# AgentMax Beta v2 — Contexto Completo para Codex

## Resumen del Proyecto

Sistema de entrenamiento distribuido para fine-tuning de Qwen2.5-7B-Instruct con Unsloth + QLoRA 4-bit. Permite conectar GPUs de multiples PCs via LAN o ZeroTier usando DDP (torchrun) o Federated Averaging (TCP server). Incluye UI Tauri v2 con dashboard, chat, memoria, recolector de bugs, y auto-config.

---

## Stack

| Capa | Tecnologia |
|------|-----------|
| UI | Tauri 2 + HTML/CSS vanilla + Chart.js |
| Backend | Rust (Tauri commands, subprocess management) |
| ML | PyTorch + Unsloth + TRL + QLoRA 4-bit |
| Distributed | DDP (torchrun) + Federated (TCP JSON sobre ZeroTier/LAN) |
| Database | SQLite + AES-256-GCM (con crypto_db.py) |
| System | nvidia-smi, wmic, psutil |

---

## Arquitectura de Archivos

### Ruta base: `AgentMax/BetaAgentMax/`

#### Rust / Tauri (frontend embehido)
```
tauri-viz/
├── src/                          ← Frontend HTML/CSS/JS
│   ├── index.html                ← Dashboard Master (5 tabs)
│   ├── contributor.html          ← UI contribuyente minimalista
│   ├── app.js                    ← Logica master (charts, hardware, coordinator)
│   ├── contributor.js            ← Logica contribuyente (conectar GPU, logs)
│   ├── style.css                 ← Tema oscuro premium
│   └── contributor.css           ← Tema contribuyente
├── src-tauri/
│   ├── Cargo.toml                ← Dependencias Rust
│   ├── tauri.conf.json           ← Config Tauri (frontendDist, CSP, ventana)
│   ├── build.rs                  ← tauri_build::build()
│   └── src/main.rs               ← BACKEND RUST (473 lineas)
│       ├── struct AppState       ← process, is_contributor, coordinator_process
│       ├── find_python()         ← Busca .venv subiendo directorios
│       ├── find_training_dir()   ← Busca train_ddp.py subiendo desde el exe
│       ├── find_zerotier_cli()   ← Busca ZeroTier CLI en rutas tipicas
│       ├── get_zt_ip()           ← Ejecuta zerotier-cli listnetworks, parsea IP
│       ├── spawn_and_pipe()      ← Lanza subproceso, pipea stdout/stderr como eventos Tauri
│       ├── auto_start_coordinator()  ← Auto-inicia coordinator.py al abrir la app
│       ├── detect_zerotier()     ← Tauri command: lista redes ZT
│       ├── join_zerotier_network()   ← Tauri command: join por Network ID
│       ├── detect_local_gpu()    ← Tauri command: nvidia-smi o torch
│       ├── run_hardware_scan()   ← Tauri command: lanza hardware_detective.py
│       ├── start_contributor()   ← Tauri command: lanza contributor.py
│       ├── start_training()      ← Tauri command: lanza torchrun train_ddp.py
│       ├── start_coordinator()   ← Tauri command: lanza coordinator.py
│       ├── stop_process()         ← Tauri command: mata el proceso activo
│       └── main()                ← setup con auto-start coordinator + ZT deteccion
└── package.json                  ← @tauri-apps/api, @tauri-apps/cli
```

#### Python (scripts de entrenamiento y servidor)
```
├── coordinator.py                ← 543 lineas. Servidor TCP federado.
│                                   Protocolo JSON: hello/welcome/start/progress/done/averaged/completed
│                                   Recibe pesos gzip(torch.state_dict), promedia con FedAvg, redistribuye
│                                   Acepta contribuyentes via TCP, N rondas de entrenamiento
│
├── contributor.py                ← ~200 lineas. Cliente TCP que conecta al coordinator.
│                                   Envia hello, recibe start, entrena, envia pesos, recibe averaged
│
├── train_ddp.py                  ← ~250 lineas. DDP con auto-deteccion de hardware.
│                                   Usa torchrun, gather de GPUs, config optima segun GPU mas debil
│
├── hardware_detective.py         ← ~150 lineas. Scanea GPU (nvidia-smi), RAM (wmic), CPU, benchmark
│
├── auto_config_engine.py         ← ~100 lineas. Tabla VRAM → batch/lora_rank/workers/max_seq
│
├── generate_dataset.py           ← Genera dataset en formato tool_calls para fine-tuning
│
├── bug_collector.py              ← Captura errores de training y feedback de chat
│
├── crypto_db.py                  ← Base SQLite + AES-256-GCM + HMAC para datos cifrados
│
├── optimize.ps1                  ← Script admin: power plan, GPU persistence, Defender exclusion
│
├── unsloth_train.py              ← Entrenamiento sin DDP (1 GPU)
├── unsloth_train_max.py          ← Entrenamiento sin DDP con max power
│
├── Lanzar-Servidor.ps1           ← 207 lineas. Lanzador completo: ZeroTier + firewall + coordinator
│
├── Forzar-Inicio.ps1             ← Lanzador admin con auto-elevacion
├── Forzar-Inicio.bat             ← Wrapper para Forzar-Inicio.ps1
├── Iniciar-Todo.bat              ← Wrapper simple: solo lanza el exe
├── AgentMax-Master.bat         ← Lanzador master con setup de venv + dependencias
├── AgentMax-Contributor.bat    ← Lanzador contribuyente simplificado
└── PLAN_MAESTRO.md               ← 6 fases de implementacion con bugs documentados
```

#### Dataset
```
AgentMax-codetool-7b-v0.1/
└── data/
    └── train.jsonl               ← 433 KB, ejemplos tool_calls para fine-tuning
```

#### Release folders (para distribuir)
```
release/
├── AgentMax-Master/             ← Zip para el usuario master
│   └── tauri-viz/src-tauri/target/release/tauri-viz.exe  (10.1 MB)
└── AgentMax-Contributor/        ← Zip para amigos contribuyentes
    ├── tauri-viz/src-tauri/target/release/contributor.exe (10.1 MB)
    ├── AgentMax-Contributor.bat ← Launcher simplificado
    ├── start_contributor_smart.bat ← Launcher con auto-setup de Python
    ├── contributor.py             ← Fallback si no hay .exe
    ├── train_ddp.py               ← Necesario para entrenar
    └── LEEME.txt                  ← Instrucciones
```

---

## Protocolo Coordinator ↔ Contributor (TCP JSON)

```
1. Contributor → "hello" {gpu, vram_gb, cores}
2. Coordinator → "welcome" {id, n_contributors, min_to_start}
3. Coordinator → "seed_weights" (si hay ronda anterior)
4. Coordinator → "start" {round, config, has_seed}
5. Contributor → "progress" {step, loss, gpu_util, vram_used} [repetido]
6. Contributor → "done" {round, steps}
7. Contributor → <8-byte-size> <gzip-torch-bytes>  (pesos del adapter)
8. Coordinator → "averaged" {round, size}
9. Coordinator → <8-byte-size> <gzip-torch-bytes>  (pesos promediados)
10. Contributor → "ready"
11. [repetir 4-10 por cada ronda]
12. Coordinator → "completed"
```

---

## Eventos Tauri (Rust → Frontend)

| Evento | Payload | Trigger |
|--------|---------|---------|
| `training:log` | string (linea de stdout/stderr) | spawn_and_pipe() |
| `training:loss` | f64 | parseo de "'loss': X" en log |
| `zerotier:ip` | string (IP: 10.147.x.x) | auto_start_coordinator() |

## Tauri Commands (Frontend → Rust)

| Comando | Args | Returns |
|---------|------|---------|
| `get_app_mode` | - | "master" o "contributor" |
| `get_default_train_dir` | - | ruta absoluta |
| `detect_local_gpu` | - | "Name,TotalGB,Driver" |
| `detect_zerotier` | - | "nwid\|name\|OK\|ip" o error |
| `join_zerotier_network` | networkId | mensaje de exito |
| `run_hardware_scan` | trainDir | confirmacion |
| `start_coordinator` | trainDir, port, min, max, rounds, epochs | confirmacion |
| `start_contributor` | coordinatorIp, port, trainDir, torchrunPath | confirmacion |
| `start_training` | mode, addr, port, python, dir, params... | confirmacion |
| `stop_process` | - | "Proceso detenido" |

---

## Bugs Conocidos (del PLAN_MAESTRO.md)

### 🔴 CRITICAL (no arreglados)
- **C1**: `[{}] * world_size` en train_ddp.py:121 → shared dict reference
- **C2**: `recv_json` en contributor.py:50 → crashea en socket vacio
- **C3**: contributor.js sin listener `training:log` (YA FIJADO)
- **C4**: broadcast_object_list sobre shared-ref (depende de C1)

### 🟠 HIGH
- **H1**: `local_rank=rank` en vez de LOCAL_RANK env var
- **H2**: IndexError si hw vacio en coordinator.py
- **H3**: `except:` bare atrapa KeyboardInterrupt
- **H5**: orphans en GPU al cerrar (YA FIJADO en main.rs)
- **H6**: `--master_port=0` puerto aleatorio
- **H7**: tarjetas duplicadas (YA FIJADO en app.js)
- **H8**: power-limit=999 peligroso
- **H9**: 50% errores aleatorios en dataset
- **H10**: CSP null (YA FIJADO en tauri.conf.json)

---

## Estado Actual

### Lo que funciona
- Build Tauri exitoso (10.1 MB, frontend embebido correctamente)
- Auto-deteccion de ZeroTier al iniciar la app
- Auto-inicio del coordinator.py como subproceso
- ZeroTier badge en header + ZT card en coordinador
- Join a red ZeroTier desde la UI
- Contributor UI con deteccion de ZT
- GPU detection (nvidia-smi + torch)
- Hardware scan (hardware_detective.py)
- Pipeline de build: `cd tauri-viz && npx tauri build`

### Lo que NO funciona / falta
- **C1**: Bug shared dict en train_ddp.py (training federado tiene datos corruptos)
- **C2**: Contributor.py crashea si el socket esta vacio
- **Chat + Memoria Ilimitada** (Fase 2 del plan): inference.py, memory_manager.py, chat.html, chat.js
- **Bug Collector integrado** (Fase 3): falta conectar bug_collector.py con la UI
- **DB Cifrada** (Fase 4): crypto_db.py existe pero no esta integrada
- **Fine-tuning Automatico** (Fase 5): auto_finetune.py no creado
- **UI Premium** (Fase 6): rediseno completo con glassmorphism
- **El servidor TCP coordinator.py necesita rewrite en Rust** para eliminar dependencia de Python en el servidor

### Próximos Pasos Prioritarios
1. Arreglar C1 (shared dict) y C2 (empty socket) — bloquean training federado
2. Re-escribir coordinator.py en Rust (embebido en main.rs) para servidor sin Python
3. Fase 2: Chat + Memoria (inference.py, memory_manager.py, chat UI)
4. Fase 3: Bug collector UI integration

---

## Como Compilar

```powershell
cd AgentMax/BetaAgentMax/tauri-viz
npm install
npx tauri build
# Exe en: tauri-viz/src-tauri/target/release/tauri-viz.exe
# Copiar como contributor.exe para modo contribuyente
```

## Como Usar

**Master (tu PC):**
```cmd
Iniciar-Todo.bat     ← abre servidor + GUI
```
O directo: `tauri-viz.exe` (auto-inicia servidor)

**Contribuyente (amigo):**
```cmd
AgentMax-Contributor.bat  ← abre GUI, pide IP del master
```
O directo: `contributor.exe` + ingresa IP ZeroTier

---

## Variables de entorno importantes

```
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
CUDA_LAUNCH_BLOCKING=0
OMP_NUM_THREADS=%NUMBER_OF_PROCESSORS%
MKL_NUM_THREADS=%NUMBER_OF_PROCESSORS%
TOKENIZERS_PARALLELISM=false
CUDA_DEVICE_MAX_CONNECTIONS=8
```
