# AgentMax Beta v2.0 — Como usar

Sistema de fine-tuning distribuido con interfaz Tauri.
Vos sos el **Master** (controlas todo) y tus amigos son **Contributors**
(prestan su GPU).

---

## 🚀 Setup en 3 pasos

### Paso 1: Build los 2 paquetes (solo una vez)

```
build_release.bat
```

Esto compila la UI Tauri y produce 2 ZIPs en `dist/`:

- `dist/AgentMax-Master.zip` — Para vos
- `dist/AgentMax-Contributor.zip` — Para tus amigos

> Si la UI ya esta compilada, podes saltar el build:
> `powershell -File scripts\build_release.ps1 -SkipBuild`

### Paso 2: Arranca tu Master

Descomprime `AgentMax-Master.zip` donde quieras y doble-click en:

```
AgentMax-Master.bat
```

La primera vez tarda 5-15 min (instala torch, unsloth, etc).
Se abre la UI. En la consola aparece tu IP local.

### Paso 3: Pasale el ZIP a tus amigos

Mandales `AgentMax-Contributor.zip` (por WeTransfer, Drive, etc).
Ellos:

1. Descomprimen el ZIP
2. Doble-click en `AgentMax-Contributor.bat`
3. Ponen tu IP cuando la pide
4. Listo — su GPU empieza a entrenar

---

## 🖥️ Tabs de la UI Master

| Tab | Para que |
|-----|----------|
| 🌐 **Coordinador** | Server federado FedAvg. Aca se conectan los amigos. |
| 🖥️ **Hardware**    | Escaneo automatico de GPU/CPU/RAM + benchmark + config optima |
| 📡 **Dispositivos** | Tabla en vivo de todos los conectados (loss, step, etc) |
| ⚡ **DDP**          | Modo clasico con 1 amigo via ZeroTier |
| ⚙️ **Config**      | Modelo base, output dir, bug collector |

---

## 🔌 Networking

Para que tus amigos se conecten necesitas que tu IP sea alcanzable.
Opciones:

- **LAN local** (mismo wifi) — funciona de una
- **ZeroTier** (recomendado para internet) — instala
  [ZeroTier](https://www.zerotier.com/download/) en vos y tus amigos,
  todos se unen a la misma red, usas tu IP virtual de ZeroTier
- **Hamachi / Tailscale** — alternativas a ZeroTier
- **Port forwarding** — abrir puerto 12356 en el router (no recomendado)

---

## 🐛 Modulos nuevos (Beta v2.0)

| Archivo | Funcion |
|---------|---------|
| `hardware_detective.py` | Scan GPU/CPU/RAM + benchmark FP16 TFLOPS |
| `auto_config_engine.py` | Tabla de config optima por VRAM/CPU/RAM |
| `crypto_db.py`          | SQLite cifrada AES-256-GCM (chat + bugs + prefs) |
| `bug_collector.py`      | Captura errores de training y feedback de chat |

Se exponen via la UI o se pueden usar standalone:

```bash
python hardware_detective.py
python auto_config_engine.py --vram 8 --ram 32 --cores 12
python bug_collector.py
```

---

## 🐞 Bugs arreglados en esta version

- **C1** — `train_ddp.py`: shared dict reference en `all_hw`
- **H1** — `train_ddp.py`: `local_rank` usaba rank global
- **H4** — `main.rs`: busca python.exe en `.venv` correctamente
- **H5** — `main.rs`: mata procesos hijos al cerrar la ventana
- **H7** — `app.js`: tarjetas de contribuyentes duplicadas
- **H10** — `tauri.conf.json`: CSP definido (antes `null`)
- **C3** — `contributor.js`: listeners `training:log` registrados
- **M10** — `main.rs`: stdout/stderr `.take().unwrap()` → safe
- **M12** — `app.js`: `connectContributor()` invoca el comando real
- **M13** — `app.js`: stop solo resetea los botones del proceso activo

---

## 📦 Estructura

```
BetaAgentMax/
├── AgentMax-Master.bat          ← Launcher para vos
├── AgentMax-Contributor.bat     ← Launcher para amigos
├── build_release.bat              ← Build + package en ZIPs
├── COMO_USAR.md                   ← Este archivo
├── PLAN_MAESTRO.md                ← Plan de implementacion original
│
├── train_ddp.py                   ← Training DDP (FIXED C1, H1)
├── coordinator.py                 ← Server federado FedAvg
├── contributor.py                 ← Cliente contribuyente
├── hardware_detective.py          ← NUEVO: HW scan + benchmark
├── auto_config_engine.py          ← NUEVO: config optima
├── crypto_db.py                   ← NUEVO: SQLite cifrada AES-256
├── bug_collector.py               ← NUEVO: captura bugs para FT
├── generate_dataset.py            ← Generador del dataset
│
├── scripts/
│   └── build_release.ps1          ← Build script en PowerShell
│
├── tauri-viz/                     ← UI premium (glassmorphism)
│   ├── src/
│   │   ├── index.html             ← UI redisenada
│   │   ├── style.css              ← Tema premium
│   │   ├── app.js                 ← Logica (FIXED H7, M12, M13)
│   │   ├── contributor.html
│   │   └── contributor.js         ← FIXED C3
│   └── src-tauri/
│       ├── tauri.conf.json        ← FIXED H10 (CSP)
│       └── src/main.rs            ← FIXED H4, H5, M10
│
└── dist/
    ├── AgentMax-Master/         ← Folder listo (post-build)
    ├── AgentMax-Master.zip      ← ZIP para vos
    ├── AgentMax-Contributor/    ← Folder listo
    └── AgentMax-Contributor.zip ← ZIP para amigos
```

---

## ✨ Tips

- Activa **Max Power** en la pestaña DDP para que tu GPU vuele
- El **escaneo de hardware** se ejecuta automaticamente al abrir la UI
- El **bug collector** guarda crashes en `data/AgentMax.db` (cifrado)
- Si tu amigo tiene Python 3.13, que use 3.12 (Unsloth aun no soporta 3.13)
