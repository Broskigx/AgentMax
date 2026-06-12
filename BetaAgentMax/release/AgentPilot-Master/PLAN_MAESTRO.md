# NixControl Beta — Plan Maestro de Implementacion

> Sistema completo de entrenamiento distribuido con IA, memoria persistente,
> recoleccion de bugs, y fine-tuning automatico.
> Version: 2.0 | Fecha: 2026-05-22

---

## Tabla de Contenidos

1. [Arquitectura General](#1-arquitectura-general)
2. [Bugs Existentes (Prioridad)](#2-bugs-existentes)
3. [Fase 0: Hardware Detective + Auto-Config](#3-fase-0-hardware-detective)
4. [Fase 1: Sistema de Dispositivos en Red](#4-fase-1-dispositivos-en-red)
5. [Fase 2: Chat + Memoria Ilimitada](#5-fase-2-chat--memoria-ilimitada)
6. [Fase 3: Recolector de Bugs + Feedback Loop](#6-fase-3-recolector-de-bugs)
7. [Fase 4: Base de Datos Cifrada](#7-fase-4-base-de-datos-cifrada)
8. [Fase 5: Fine-tuning Automatico](#8-fase-5-fine-tuning-automatico)
9. [Fase 6: Interfaz Premium Unificada](#9-fase-6-interfaz-premium-unificada)
10. [Roadmap y Tiempos](#10-roadmap)
11. [Arbol de Archivos Final](#11-arbol-de-archivos-final)

---

## 1. Arquitectura General

```
┌──────────────────────────────────────────────────────────────────┐
│                     BetaNixControl v2.0                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐    ┌──────────────────────────────────┐    │
│  │   Tauri UI       │    │     Rust Backend (main.rs)       │    │
│  │  ┌───────────┐  │    │  ┌────────────────────────────┐  │    │
│  │  │ Dashboard  │  │    │  │ spawn_and_pipe()          │  │    │
│  │  │ Chat       │  │    │  │ get_default_train_dir()   │  │    │
│  │  │ Hardware   │  │◄───│  │ detect_local_gpu()        │──┤──┐ │
│  │  │ Disposit. │  │    │  │ start_training()           │  │  │ │
│  │  │ Logs/Stats │  │    │  │ start_coordinator()        │  │  │ │
│  │  └───────────┘  │    │  └────────────────────────────┘  │  │ │
│  └─────────────────┘    └──────────────────────────────────┘  │ │
│                          │                                     │ │
│                          ▼                                     │ │
│  ┌──────────────────────────────────────────────────────────┐  │ │
│  │              Procesos Python                              │  │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐ │◄┘ │
│  │  │train_ddp │ │coordinator│ │contributo│ │hardware_de-  │ │   │
│  │  │.py       │ │.py       │ │r.py      │ │tective.py    │ │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────┬──────┘ │   │
│  │                                                  │        │   │
│  │  ┌───────────────────────────────────────────────┘        │   │
│  │  ▼                                                        │   │
│  │  ┌──────────────────────────────────────────────────┐     │   │
│  │  │           Base de Datos Cifrada                   │     │   │
│  │  │  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │     │   │
│  │  │  │Chat      │ │ Bugs     │ │ Preferencias      │  │     │   │
│  │  │  │Historial │ │ Reportes │ │ Usuario           │  │     │   │
│  │  │  └──────────┘ └──────────┘ └──────────────────┘  │     │   │
│  │  └──────────────────────────────────────────────────┘     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Stack tecnologico final

| Componente | Tecnologia |
|------------|-----------|
| UI | Tauri 2 + HTML/CSS/JS vanilla + Chart.js |
| Backend | Rust (Tauri commands) |
| ML Training | PyTorch + Unsloth + TRL + QLoRA 4-bit |
| Distributed | DDP (torchrun) + Federated (TCP JSON) |
| Database | SQLite + AES-256-GCM (cryptography library) |
| Chat Inference | Modelo Qwen2.5-7B-Instruct fine-tuneado |
| System Detection | nvidia-smi, wmic, Python psutil |
| Graphs | Chart.js |

---

## 2. Bugs Existentes

### 🔴 CRITICAL (arreglar antes de任何 cosa)

| ID | Archivo | Linea | Bug | Impacto | Fix |
|----|---------|-------|-----|---------|-----|
| **C1** | `train_ddp.py` | 121 | `all_hw = [{}] * world_size` crea lista con referencias al MISMO dict | Todas las GPUs aparecen identicas → auto-config calcula mal | `[{} for _ in range(world_size)]` |
| **C2** | `contributor.py` | 50 | `recv_json` crashea si el socket esta vacio (`struct.unpack("",...)`) | Contributor se cae al desconectar coordinador | `if not raw_len: raise ConnectionError` |
| **C3** | `contributor.js` | — | Nunca registra `listen('training:log')` | UI del contributor no muestra logs, stats ni progreso | Agregar listeners Tauri |
| **C4** | `train_ddp.py` | 143 | `broadcast_object_list` sobre lista con shared-ref | Config corrupta en workers no-rank-0 | Fijar C1 primero |

### 🟠 HIGH

| ID | Archivo | Linea | Bug | Impacto | Fix |
|----|---------|-------|-----|---------|-----|
| **H1** | `train_ddp.py` | 193 | `local_rank=rank` usa rango global en vez de local | Multi-node asigna GPU incorrecta | `os.environ.get("LOCAL_RANK",0)` |
| **H2** | `coordinator.py` | 183 | IndexError si `hw` esta vacio | Coordinador crashea sin contribuyentes | `hw[0]["gpu"] if hw else "none"` |
| **H3** | `coordinator.py` | 67,83 | `except:` (bare) atrapa `KeyboardInterrupt` | Ctrl+C no funciona | Usar `except Exception:` |
| **H4** | `main.rs` | 97-103 | Busca `python.exe` en el mismo dir que el EXE | `start_coordinator`/`contributor` fallan | Buscar en `.venv/Scripts/python.exe` |
| **H5** | `main.rs` | — | Sin `Drop` ni handler de cierre | Huerfanos de training quedan en GPU | Matar child al cerrar ventana |
| **H6** | `contributor.py` | 97 | `--master_port=0` puerto aleatorio | Training federado no funciona, cada uno entrena solo | Usar puerto fijo o no usar torchrun |
| **H7** | `app.js` | 100-124 | Tarjetas duplicadas de contribuyentes | UI se llena de repetidos | Dedup por ID |
| **H8** | `optimize.ps1` | 49 | `nvidia-smi --power-limit=999` peligroso | Danio hardware en GPUs desbloqueadas | Usar `power.limit` real |
| **H9** | `generate_dataset.py` | 179 | ~50% errores aleatorios en tool calls | Modelo aprende comportamiento erroneo | Errores solo en escenarios especificos |
| **H10** | `tauri.conf.json` | 24 | `csp: null` sin restricciones | XSS vulnerable | CSP restrictivo |

### 🟡 MEDIUM

| ID | Bug |
|----|-----|
| M1 | CUDA ops sin `torch.cuda.is_available()` |
| M2 | `build_dataset` crashea si no hay archivos |
| M3 | `not dist.is_initialized()` ambiguo sin WORLD_SIZE |
| M4 | `send_json` dentro del lock (bloquea hilos) |
| M5 | `len(self.contributors)` race condition |
| M6 | Timeout sin contribuyentes → loop infinito |
| M7 | `send_json` sin try/except → `BrokenPipeError` |
| M8 | `cfg.get("step",0)` siempre 0 |
| M9 | `import subprocess` dentro del loop training |
| M10 | `child.stdout.take().unwrap()` panic si no hay pipe |
| M11 | Hardcoded `"python"` → falla sin PATH |
| M12 | `connectContributor()` solo muestra instrucciones |
| M13 | `stopProcess()` resetea todos los botones |
| M14 | `optimize.ps1` desactiva servicios permanentemente |
| M15 | `[System.GC]::Collect()` no funciona en otros procesos |
| M16 | `scroll`/`wait` usan `screenshot` result semanticamente mal |
| M17 | `read_file` error result formato inconsistente |
| M18 | Categoria dataset usa primeros 40 chars (fragil) |

---

## 3. Fase 0: Hardware Detective + Auto-Config

### Que hace
Apenas se abre la app, corre un scan completo del hardware en 2do plano.
Muestra en la UI la config optima recomendada para el usuario.

### Archivos nuevos

#### `hardware_detective.py` (~250 lineas)
```python
class HardwareDetective:
    def scan_gpu() -> dict
        # nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu,
        #             temperature.gpu,power.draw,power.limit,driver_version
        # → GPU name, VRAM total/libre, temp, power, driver

    def scan_ram() -> dict
        # psutil.virtual_memory() + wmic memorychip
        # → Total RAM, slots, speed, DDR type

    def scan_cpu() -> dict
        # psutil.cpu_count(logical=True), cpu_freq, wmic cpu
        # → Cores, threads, freq, name

    def benchmark_gpu() -> float
        # Tensor de prueba 4096x4096, forward+backward 10x
        # → TFLOPS reales, VRAM pico

    def full_scan() -> dict
        # Todo lo anterior en un JSON
```

#### `auto_config_engine.py` (~200 lineas)
```python
class AutoConfigEngine:
    VRAM_TABLE = {
        # VRAM minima → (batch_size, lora_rank, workers, max_seq)
        (0, 6):    (1, 8, 2, 2048),    # GPUs debiles / iGPU
        (6, 8):    (1, 16, 4, 4096),   # 6-8 GB
        (8, 10):   (2, 32, 8, 4096),   # 8-10 GB ← RTX 4060 Ti
        (10, 12):  (4, 64, 12, 4096),  # 10-12 GB
        (12, 16):  (4, 64, 12, 6144),  # 12-16 GB
        (16, 24):  (8, 128, 16, 8192), # 16-24 GB
        (24, 999): (8, 128, 16, 8192), # 24+ GB
    }

    def compute(gpu_info, ram_info, benchmark) -> dict
        # 1. Lookup VRAM_TABLE
        # 2. Ajustar workers a CPU cores
        # 3. Ajustar grad_accum segun RAM
        # 4. Recomendar TF32/cuDNN segun GPU
        # 5. Recomendar max_power si VRAM lo permite
```

### Cambios en Rust (main.rs)

```rust
// Nuevo comando
#[tauri::command]
fn run_hardware_scan(app: AppHandle) -> Result<String, String> {
    // Lanza hardware_detective.py como subproceso
    // Pipea stdout como evento "hardware:scan"
    // Retorna JSON completo
}

// Auto-llamado al iniciar
fn main() {
    // ... despues de construir la app
    app.once("ready", || {
        app.invoke("run_hardware_scan", {});
    });
}
```

### UI nueva

```
┌──────────────────────────────────────────────────┐
│  🖥️  TU SISTEMA                                  │
│  ┌────────────────────────────────────────────┐  │
│  │  GPU: NVIDIA RTX 4060 Ti ■■■■■■■□□□ 85%   │  │
│  │  VRAM: 8.6 GB total | 6.2 GB libre         │  │
│  │  RAM: 32 GB | CPU: 12 cores @ 4.2 GHz      │  │
│  │  CUDA 12.1 | Driver 546.01                  │  │
│  │  TFLOPS reales: 14.8 TFLOPS (FP16)          │  │
│  ├────────────────────────────────────────────┤  │
│  │  📊 CONFIG RECOMENDADA                      │  │
│  │  ┌────────────────────────────────────────┐ │  │
│  │  │ Batch/GPU: 2  │ LoRA rank: 32          │ │  │
│  │  │ Workers: 8    │ Max seq: 4096           │ │  │
│  │  │ Grad accum: 4 │ TF32: ✅ cuDNN: ✅     │ │  │
│  │  │ ETA estimada: ~6h (1 GPU)              │ │  │
│  │  └────────────────────────────────────────┘ │  │
│  │  [✅ USAR ESTA CONFIG] [✏️  PERSONALIZAR]   │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### Bugs que arregla esta fase
- M1: Guard CUDA ops con `torch.cuda.is_available()`
- M11: Detectar python.exe en multiples ubicaciones
- H4: Ruta correcta al python del .venv

---

## 4. Fase 1: Sistema de Dispositivos en Red

### Que hace
Cuando el coordinador esta activo, muestra TODOS los dispositivos conectados
con su hardware exacto, estado, y progreso. Tabla en vivo.

### Cambios en protocolo (coordinator.py ↔ contributor.py)

**Handshake mejorado:**
```json
{
  "type": "hello",
  "gpu": "NVIDIA RTX 4060 Ti",
  "vram_gb": 8.6,
  "cores": 12,
  "hostname": "AGUST-PC",
  "device_id": "a1b2c3d4...",  // SHA256(MAC + install_id)
  "cuda_version": "12.1",
  "driver_version": "546.01",
  "os": "Windows 11 Pro",
  "ip": "192.168.196.1"
}
```

**Broadcast periodico:**
```json
{
  "type": "device_list",
  "devices": [
    {"id": 1, "hostname": "AGUST-PC", "gpu": "RTX 4060 Ti", "vram": 8.6,
     "status": "training", "loss": 2.34, "step": 42, "round": 1},
    {"id": 2, "hostname": "PEDRO-RIG", "gpu": "RTX 3080", "vram": 10.0,
     "status": "idle", "loss": null, "step": 0, "round": 1}
  ]
}
```

### UI tabla dispositivos

```
┌──────────────────────────────────────────────────────────────┐
│  🌐 DISPOSITIVOS EN RED (2 conectados)                       │
│  ┌──────┬────────────┬──────────────┬──────┬────────┬──────┐ │
│  │  #   │ HOSTNAME   │ GPU          │ VRAM │ STATUS │ LOSS │ │
│  ├──────┼────────────┼──────────────┼──────┼────────┼──────┤ │
│  │  1   │ AGUST-PC   │ RTX 4060 Ti │ 8.6  │ 🟢 TRAIN │ 2.3 │ │
│  │  2   │ PEDRO-RIG  │ RTX 3080    │ 10.0 │ 🟢 TRAIN │ 2.1 │ │
│  │  3   │ MARIA-LAPT │ RTX 3050    │ 4.0  │ 🟡 IDLE  │  -  │ │
│  └──────┴────────────┴──────────────┴──────┴────────┴──────┘ │
└──────────────────────────────────────────────────────────────┘
```

### Bugs que arregla esta fase
- H2: IndexError en lista vacia
- H7: Tarjetas duplicadas
- M5: Race condition en `len(self.contributors)`

---

## 5. Fase 2: Chat + Memoria Ilimitada

### Que hace
Chat completo con el modelo NixControl DENTRO de la app.
Memoria ILIMITADA: guarda TODO el historial de conversacion en DB cifrada.
El modelo recuerda quien eres, tus preferencias, y conversaciones pasadas.

### Arquitectura del Chat

```
Usuario escribe mensaje
        │
        ▼
UI (chat.html) ──invoke("send_message")──→ Rust main.rs
        │                                        │
        │                                        ▼
        │                              inference.py (subprocess)
        │                              - Carga modelo fine-tuneado
        │                              - Genera respuesta (streaming)
        │                                        │
        │◄───── evento "chat:token" (token a token) ────┤
        │◄───── evento "chat:response" (respuesta completa) ──┤
        │                                        │
        ▼                                        ▼
  UI muestra tokens                     DB Cifrada: guarda
  en tiempo real                        (user_id, session,
                                        role, content, timestamp)
```

### Archivos nuevos

#### `inference.py` (~300 lineas)
```python
"""
Carga el modelo fine-tuneado y genera respuestas.
Modo streaming para mostrar token a token en la UI.
"""
import sys, json, torch
from unsloth import FastLanguageModel

MODEL_PATH = "outputs/NixControl-7b-v1.0/adapter"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"

def load_model():
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=8192,
        load_in_4bit=True,
    )
    # Cargar adapter fine-tuneado si existe
    adapter_path = MODEL_PATH
    if os.path.exists(adapter_path):
        model.load_adapter(adapter_path)
    return model, tokenizer

def generate(prompt, chat_history, user_prefs):
    # Construir context con historial + preferencias
    system = user_prefs.get("system_prompt", DEFAULT_SYSTEM)
    messages = [{"role": "system", "content": system}]
    for h in chat_history[-50:]:  # Ultimos 50 mensajes como contexto
        messages.append(h)
    messages.append({"role": "user", "content": prompt})

    # Generar token a token
    inputs = tokenizer.apply_chat_template(messages, return_tensors="pt")
    outputs = model.generate(inputs, max_new_tokens=2048, stream=True)
    for token in outputs:
        print(json.dumps({"type": "token", "text": token}), flush=True)
```

#### `memory_manager.py` (~150 lineas)
```python
"""
Extrae preferencias del usuario de las conversaciones.
Analiza patrones: tono, temas favoritos, nivel de detalle.
"""
class MemoryManager:
    def extract_preferences(chat_history):
        # Palabras clave: "me gusta", "prefiero", "siempre"
        # → verbosity, tone, topics

    def build_system_prompt(preferences) -> str:
        # Genera system prompt personalizado
        # "Eres NixControl. El usuario prefiere respuestas
        #  detalladas. Sus temas frecuentes: Python, web dev..."
```

#### `tauri-viz/src/chat.html` (~200 lineas)
```html
<!-- Interfaz de chat estilo premium -->
<div id="chat-app">
  <div id="chat-messages">
    <!-- Mensajes con burbujas usuario/asistente -->
  </div>
  <div id="chat-input">
    <textarea placeholder="Escribe tu mensaje..."></textarea>
    <button>Enviar</button>
  </div>
</div>
```

#### `tauri-viz/src/chat.js` (~250 lineas)
```javascript
async function sendMessage(text) {
    addMessage('user', text);
    const response = await invoke('send_message', { text });
}
await listen('chat:token', (event) => appendToken(event.payload));
```

### DB schema (chat)

```sql
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    metadata TEXT
);

CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens_generated INTEGER,
    model_used TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
);
CREATE INDEX idx_messages_session ON chat_messages(session_id, timestamp);

CREATE TABLE user_preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    updated_at TIMESTAMP
);
```

---

## 6. Fase 3: Recolector de Bugs + Feedback Loop

### Que hace
Cada error durante training, cada crash, cada respuesta incorrecta
→ se guarda en DB cifrada → se usa para fine-tuning.

### Archivos nuevos

#### `bug_collector.py` (~200 lineas)
```python
class BugCollector:
    def capture_training_error(exception, config, stage):
        # traceback.format_exc() + config + GPU state
        # → { "type": "training_error", "stage": "dataloader",
        #     "trace": "...", "config": {...}, "vram_at_crash": 8.2 }

    def capture_chat_feedback(original_message, model_response, user_correction):
        # → { "type": "chat_feedback", "input": "...",
        #     "bad_output": "...", "correct_output": "..." }

    def export_for_finetuning() -> list[dict]:
        # Convierte bugs → ejemplos de training
        # training_error → formato tool_call con error
        # chat_feedback → formato messages con correccion
```

#### `auto_finetune.py` (~200 lineas)
```python
class AutoFinetune:
    MIN_EXAMPLES = 20

    def check_and_run():
        bugs = load_bugs_from_db()
        chats = load_chat_feedback()
        total = len(bugs) + len(chats)

        if total < self.MIN_EXAMPLES:
            return {"status": "waiting", "current": total, "needed": MIN_EXAMPLES}

        dataset = convert_to_jsonl(bugs, chats)
        save_dataset(dataset)

        result = subprocess.run([
            sys.executable, "train_ddp.py",
            "--batch_size=2", "--lora_rank=16",
            "--epochs=1", "--mode=local"
        ])

        if result.returncode == 0:
            replace_adapter()
            return {"status": "completed", "samples": total}
```

### Integracion en train_ddp.py

```python
def main():
    try:
        trainer.train()
    except Exception as e:
        from bug_collector import BugCollector
        BugCollector().capture_training_error(e, config, "training")
        raise
```

### Notificacion al iniciar

```
┌──────────────────────────────────────────────────┐
│  🔔 NUEVOS DATOS PARA FINE-TUNING               │
│  • 15 bugs de training capturados               │
│  • 8 correcciones de chat                       │
│  • 3 OOM con config X                           │
│  [🎯 FINE-TUNE AHORA]  [Recordar] [Ignorar]    │
└──────────────────────────────────────────────────┘
```

---

## 7. Fase 4: Base de Datos Cifrada

### Que hace
Base SQLite local con cifrado AES-256-GCM.
Cada registro firmado con HMAC para detectar manipulacion.

### Archivo nuevo: `crypto_db.py` (~300 lineas)

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, hmac
import sqlite3, json, os

class CryptoDB:
    def __init__(self, db_path, password):
        self.key = derive_key(password, salt)  # PBKDF2
        self.hmac_key = derive_key(password + b"_hmac", salt)
        self.conn = sqlite3.connect(db_path)
        self._init_tables()

    def _encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self.key).encrypt(nonce, plaintext.encode(), None)
        return nonce + ct

    def _decrypt(self, data: bytes) -> str:
        nonce, ct = data[:12], data[12:]
        return AESGCM(self.key).decrypt(nonce, ct, None).decode()

    def _sign(self, data: str) -> str:
        h = hmac.HMAC(self.hmac_key, hashes.SHA256())
        h.update(data.encode())
        return h.finalize().hex()

    def save_message(self, role, content, session_id):
        encrypted = self._encrypt(json.dumps({
            "role": role, "content": content, "session": session_id
        }))
        signature = self._sign(content)
        self.conn.execute(
            "INSERT INTO messages (session_id, encrypted, signature) VALUES (?,?,?)",
            (session_id, encrypted, signature)
        )

    def verify_integrity(self, message_id):
        row = self.conn.execute(
            "SELECT encrypted, signature FROM messages WHERE id=?", (message_id,)
        ).fetchone()
        if not row: return False
        content = json.loads(self._decrypt(row[0]))["content"]
        expected = self._sign(content)
        return hmac.compare_digest(row[1], expected)
```

### DB Schema completo

```sql
CREATE TABLE schema_version (version INTEGER, created_at TIMESTAMP);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    encrypted BLOB NOT NULL,
    signature TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, device_id TEXT,
    created_at TIMESTAMP, last_active TIMESTAMP
);
CREATE TABLE bugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_type TEXT NOT NULL,
    encrypted BLOB NOT NULL,
    signature TEXT NOT NULL,
    severity TEXT DEFAULT 'medium',
    fixed_in_version TEXT, created_at TIMESTAMP
);
CREATE TABLE preferences (
    key TEXT PRIMARY KEY,
    encrypted_value BLOB NOT NULL,
    confidence REAL DEFAULT 1.0,
    updated_at TIMESTAMP
);
```

---

## 8. Fase 5: Fine-tuning Automatico

### Al iniciar la app

```
[APP START]
    │
    ▼
┌─ ¿Hay datos nuevos para fine-tuning? ─┐
│   ├── Si → Notificacion con estimacion │
│   └── No → Iniciar normal              │
└────────────────────────────────────────┘
```

### Auto-config del fine-tuning

```python
def recommend_finetune_config(num_examples, gpu_info):
    if num_examples < 50:
        return {"lora_rank": 8, "epochs": 3, "lr": 1e-4}
    elif num_examples < 200:
        return {"lora_rank": 16, "epochs": 2, "lr": 2e-4}
    else:
        return {"lora_rank": 32, "epochs": 1, "lr": 3e-4}
```

---

## 9. Fase 6: Interfaz Premium Unificada

### Layout final

```
┌─────────────────────────────────────────────────────────────────┐
│  🔷 NixControl Beta v2.0            [⚡ Optimizar] [🔌 Salir]  │
├─────────────────────────────────────────────────────────────────┤
│  [📊 Dashboard] [🌐 Red] [💬 Chat] [🐛 Bugs] [⚙️ Config]      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐ │
│  │  🖥️ TU SISTEMA       │  │  🌐 DISPOSITIVOS EN RED         │ │
│  │  GPU: 85% ■■■■■■■□□□ │  │  ┌────┬────────┬────────┬────┐ │ │
│  │  VRAM: 6.2/8.6 GB    │  │  │ #  │ HOST   │ GPU    │ %  │ │ │
│  │  RAM: 32 GB           │  │  ├────┼────────┼────────┼────┤ │ │
│  │  ──────────────────   │  │  │ 1  │ AGUST  │ 4060Ti │ 85 │ │ │
│  │  Config recomendada   │  │  │ 2  │ PEDRO  │ 3080   │ 92 │ │ │
│  │  Batch=2, Rank=32     │  │  └────┴────────┴────────┴────┘ │
│  └──────────────────────┘  │  [➕ Invitar mas]              │
│                             └──────────────────────────────────┘
│  ┌──────────────────────┐  ┌──────────────────────────────────┐
│  │  📉 LOSS             │  │  💬 CHAT                         │
│  │  ┌──────────────┐   │  │  ┌────────────────────────────┐  │
│  │  │ 📈 Chart.js  │   │  │  │ User: Hola                 │  │
│  │  └──────────────┘   │  │  │ Agent: Hola!               │  │
│  │  Step: 42 | Loss:   │  │  │ User: Recuerdas que...      │  │
│  │  2.34 | ETA: 4h     │  │  │ Agent: Si, la vez pasada...│  │
│  └──────────────────────┘  │  └────────────────────────────┘  │
│                             │  [Escribe...] [▶]               │
│  ┌──────────────────────┐  └──────────────────────────────────┘
│  │  📋 LOGS              │
│  │  [SYSTEM] Training... │
│  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Tema Premium

```
🎨 Paleta: #06060e fondo, #0d0d1a paneles, glassmorphism
   Acento: #22c55e verde + #3b82f6 azul + #f59e0b oro

✨ Efectos: hover glow, transiciones 0.3s, border sutil
📱 Responsive: 1 col movil, 2 tablet, 3 desktop
```

---

## 10. Roadmap

| Fase | Contenido | Archivos | Tiempo |
|------|-----------|----------|--------|
| **0** | Hardware Detective + Auto-Config | 2 nuevos + cambios Rust/UI | 3-4h |
| **Bug Fixes** | C1-C4, H1-H10, M1-M18 | modificar existentes | 2h |
| **1** | Dispositivos en Red | mejorar coordinator/UI | 2-3h |
| **2** | Chat + Memoria Ilimitada | 4 nuevos + Rust/UI | 6-8h |
| **3** | Recolector de Bugs | 2 nuevos + integracion | 3-4h |
| **4** | DB Cifrada (AES-256) | 1 nuevo + integracion | 2-3h |
| **5** | Fine-tuning Automatico | 1 nuevo + UI notificacion | 3-4h |
| **6** | UI Premium Unificada | redisenar todo | 4-5h |
| **Total** | | ~15 archivos nuevos | **25-33h** |

---

## 11. Arbol de Archivos Final

```
BetaNixControl/
├── train_ddp.py              ← DDP auto-deteccion (FIXED)
├── coordinator.py            ← Servidor federado (MEJORADO)
├── contributor.py            ← Agente contribuyente (FIXED)
├── hardware_detective.py     ← NUEVO: Scan hardware
├── auto_config_engine.py     ← NUEVO: Config optima
├── inference.py              ← NUEVO: Chat con modelo
├── memory_manager.py         ← NUEVO: Preferencias usuario
├── bug_collector.py          ← NUEVO: Captura errores
├── auto_finetune.py          ← NUEVO: Fine-tuning automatico
├── crypto_db.py              ← NUEVO: DB cifrada AES-256
├── optimize.ps1              ← Admin optimizations (MEJORADO)
├── generate_dataset.py       ← Generador dataset (FIXED)
│
├── NixControl-codetool-7b-v0.1/
│   └── data/
│       ├── train.jsonl
│       ├── valid.jsonl
│       └── feedback.jsonl    ← NUEVO: Feedback recolectado
│
├── tauri-viz/
│   ├── src/
│   │   ├── index.html        ← Dashboard (REDISENADO)
│   │   ├── contributor.html  ← UI contribuyente (MEJORADA)
│   │   ├── chat.html         ← NUEVO: Chat premium
│   │   ├── app.js            ← Logica (MEJORADA)
│   │   ├── contributor.js    ← Logica (FIXED)
│   │   ├── chat.js           ← NUEVO: Logica chat
│   │   ├── style.css         ← Tema premium (REDISENADO)
│   │   └── contributor.css   ← Tema (MEJORADO)
│   └── src-tauri/
│       ├── Cargo.toml
│       ├── tauri.conf.json   ← CSP fijado
│       └── src/main.rs       ← Backend Rust (MEJORADO)
│
├── data/
│   └── NixControl.db         ← NUEVO: DB cifrada (auto-creada)
│
├── outputs/
│   └── NixControl-7b-v1.0/adapter/
│
├── run_master_ui.bat
├── run_contributor_max.bat   ← MEJORADO
├── run_worker.bat
├── run_contributor.bat
├── LEEME_DDP.md
└── PLAN_MAESTRO.md           ← Este documento
```

---

## Apendice: Bugs por Orden de Arreglo

```
1.  C1  ← shared dict (datos corruptos)
2.  C4  ← broadcast sobre lista corrupta
3.  C2  ← empty socket crash
4.  H4  ← python path falla
5.  H1  ← local_rank global
6.  H2  ← IndexError lista vacia
7.  H6  ← master_port 0
8.  H5  ← orphan processes
9.  H3  ← bare except
10. C3  ← contributor.js sin listeners
11. H7  ← tarjetas duplicadas
12. M1-M18 ← resto de bugs medium
13. H10 ← CSP null
14. H8  ← power limit peligroso
15. H9  ← error rate dataset
```
