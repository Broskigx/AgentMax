#!/usr/bin/env python3
"""
AgentMax Contributor v2.0 — Max Power Mode.

- Usa TODA la potencia disponible de la PC (CPU + GPU + RAM)
- Pide permisos de administrador si no los tiene (para max GPU power)
- Conexion real al coordinador con intercambio de pesos FedAvg
- Reconexion automatica en caso de perdida de conexion
- Monitoreo en tiempo real de GPU/CPU/VRAM

Uso:
  python contributor.py --coordinator_ip 192.168.196.1 --coordinator_port 12356
  python contributor.py --coordinator_ip 192.168.196.1 --max_power
"""
import argparse
import ctypes
import gzip
import io
import json
import os
import platform
import socket
import struct
import subprocess
import sys
import time

# ── Args ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--coordinator_ip",   required=True)
parser.add_argument("--coordinator_port", type=int, default=12356)
parser.add_argument("--train_dir",        default=".")
parser.add_argument("--max_power",        action="store_true",
                    help="Activa TODAS las optimizaciones de rendimiento (recomendado)")
parser.add_argument("--power_target",     type=float, default=90.0,
                    help="Porcentaje objetivo de uso real de la PC conectada (10-95, default 90)")
parser.add_argument("--torchrun",         default=None)
parser.add_argument("--reconnect_delay",  type=int, default=10,
                    help="Segundos entre intentos de reconexion")
parser.add_argument("--max_reconnects",   type=int, default=5)
args = parser.parse_args()

POWER_TARGET_PERCENT = max(10.0, min(float(args.power_target), 95.0))

def power_fraction() -> float:
    return POWER_TARGET_PERCENT / 100.0

def target_threads() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(cpu_count, int((cpu_count * power_fraction()) + 0.999)))

IS_WINDOWS = platform.system() == "Windows"
ADAPTER_DIR = os.path.join(args.train_dir, "outputs", "federated", "current_adapter")
os.makedirs(ADAPTER_DIR, exist_ok=True)


# ── Logging ───────────────────────────────────────────────────────────────────
def log(tag: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{tag}] {msg}", flush=True)


# ── Admin check ───────────────────────────────────────────────────────────────
def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        return os.geteuid() == 0

def request_admin_and_relaunch():
    """Relanza el script como administrador en Windows."""
    if not IS_WINDOWS:
        log("WARN", "No Windows: ejecuta con 'sudo' para max poder")
        return
    log("ADMIN", "Solicitando permisos de ADMINISTRADOR para maxima potencia GPU...")
    params = " ".join([f'"{a}"' for a in sys.argv])
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1
    )
    sys.exit(0)


# ── Max Power: optimizaciones del sistema ─────────────────────────────────────
class MaxPowerManager:
    """Aplica optimizaciones reales respetando un objetivo de potencia."""

    def __init__(self, target_percent: float = POWER_TARGET_PERCENT):
        self.target_percent = max(10.0, min(float(target_percent), 95.0))
        self.fraction = self.target_percent / 100.0
        self.cpu_threads = target_threads()
        self.applied: list[str] = []
        self.failed: list[str] = []

    def apply_all(self):
        log("POWER", "=" * 50)
        log("POWER", f"Aplicando POWER TARGET REAL: {self.target_percent:.0f}%")
        log("POWER", f"Admin: {'SI' if is_admin() else 'NO (limitado)'}")
        log("POWER", "=" * 50)

        self._set_process_priority()
        self._set_cuda_env()
        self._set_gpu_max_power()
        self._set_cpu_affinity()
        self._disable_gc()
        self._set_torch_threads()
        self._clear_vram_cache()

        log("POWER", f"Optimizaciones aplicadas: {', '.join(self.applied)}")
        if self.failed:
            log("POWER", f"No aplicadas (ignorar si son opcionales): {', '.join(self.failed)}")

    def _set_process_priority(self):
        """Prioridad de proceso: alta sin bloquear al sistema operativo."""
        try:
            if IS_WINDOWS:
                priority = 0x00000080  # HIGH_PRIORITY_CLASS
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                result = ctypes.windll.kernel32.SetPriorityClass(handle, priority)
                if result:
                    self.applied.append("process_priority=HIGH")
                else:
                    self.failed.append("process_priority")
            else:
                os.nice(-5 if is_admin() else -3)
                self.applied.append("process_nice")
        except Exception as e:
            self.failed.append(f"priority({e})")

    def _set_cuda_env(self):
        """Variables de entorno CUDA/CPU ajustadas al porcentaje objetivo."""
        cuda_vars = {
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:512,roundup_power2_divisions:8",
            "CUDA_LAUNCH_BLOCKING": "0",
            "OMP_NUM_THREADS": str(self.cpu_threads),
            "MKL_NUM_THREADS": str(self.cpu_threads),
            "TOKENIZERS_PARALLELISM": "false",
            "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
            "CUDA_DEVICE_MAX_CONNECTIONS": "8",
        }
        for k, v in cuda_vars.items():
            os.environ[k] = v
        self.applied.append(f"cuda_env({len(cuda_vars)} vars)")

    def _set_gpu_max_power(self):
        """nvidia-smi: persistence mode + power limit al porcentaje objetivo."""
        if not is_admin():
            self.failed.append("gpu_power_limit(no admin)")
            return
        try:
            # Persistence mode: reduce latency de inicializacion de kernel
            r = subprocess.run(
                ["nvidia-smi", "--persistence-mode=1"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                self.applied.append("gpu_persistence_mode")

            # Obtener power limit maximo de la GPU
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=power.max_limit",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                max_watts_raw = r.stdout.strip().split("\n")[0].strip()
                if max_watts_raw:
                    max_watts = float(max_watts_raw)
                    target_watts = max(1, int(round(max_watts * self.fraction)))
                    r2 = subprocess.run(
                        ["nvidia-smi", f"--power-limit={target_watts}"],
                        capture_output=True, text=True, timeout=5
                    )
                    if r2.returncode == 0:
                        self.applied.append(
                            f"gpu_power_limit={target_watts}W({self.target_percent:.0f}%)"
                        )

            # Auto boost ON para maxima frecuencia de GPU
            subprocess.run(
                ["nvidia-smi", "--auto-boost-default=1"],
                capture_output=True, timeout=5
            )
            self.applied.append("gpu_auto_boost")

            # Desactivar throttling por temperatura (si se puede)
            subprocess.run(
                ["nvidia-smi", "--gom=0"],  # Graphics Optimization Mode: all on
                capture_output=True, timeout=5
            )

        except FileNotFoundError:
            self.failed.append("nvidia-smi(no encontrado)")
        except Exception as e:
            self.failed.append(f"gpu_power({e})")

    def _set_cpu_affinity(self):
        """Usar una fraccion real de los nucleos disponibles."""
        try:
            cpu_count = os.cpu_count() or 1
            use_count = max(1, min(cpu_count, int((cpu_count * self.fraction) + 0.999)))
            if IS_WINDOWS:
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                mask = (1 << min(use_count, 64)) - 1
                ctypes.windll.kernel32.SetProcessAffinityMask(handle, mask)
                self.applied.append(f"cpu_affinity={use_count}/{cpu_count}cores")
            else:
                import psutil
                p = psutil.Process()
                p.cpu_affinity(list(range(use_count)))
                self.applied.append(f"cpu_affinity={use_count}/{cpu_count}cores")
        except Exception as e:
            self.failed.append(f"cpu_affinity({e})")

    def _disable_gc(self):
        """Desactivar GC de Python durante entrenamiento (lo hacemos manual)."""
        try:
            import gc
            gc.disable()
            self.applied.append("gc_disabled")
        except Exception:
            pass

    def _set_torch_threads(self):
        """Configurar torch para el objetivo de potencia."""
        try:
            import torch
            torch.set_num_threads(self.cpu_threads)
            torch.set_num_interop_threads(max(1, self.cpu_threads // 2))
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            torch.backends.cudnn.deterministic = False
            torch.set_float32_matmul_precision("high")
            self.applied.append(f"torch_threads={self.cpu_threads}")

            if torch.cuda.is_available():
                torch.cuda.set_per_process_memory_fraction(self.fraction, device=0)
                self.applied.append(f"vram_fraction={self.fraction:.2f}")
        except Exception as e:
            self.failed.append(f"torch_config({e})")

    def _clear_vram_cache(self):
        """Limpiar cache de VRAM antes de entrenar."""
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
                self.applied.append("vram_cache_cleared")
        except Exception:
            pass


# ── Deteccion de hardware ─────────────────────────────────────────────────────
def detect_hardware() -> dict:
    hw = {
        "hostname": platform.node() or "unknown",
        "gpu": "CPU (no GPU detected)",
        "vram_gb": 0.0,
        "cores": os.cpu_count() or 1,
        "ram_gb": 0.0,
        "cuda": "N/A",
        "power_target": round(POWER_TARGET_PERCENT, 1),
        "cpu_threads_target": target_threads(),
    }
    try:
        import torch
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            hw["gpu"] = p.name
            hw["vram_gb"] = round(p.total_memory / 1e9, 1)
            hw["cuda"] = torch.version.cuda or "?"
    except Exception:
        pass
    try:
        import psutil
        hw["ram_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass
    return hw


# ── Monitoreo de GPU en tiempo real ──────────────────────────────────────────
def get_gpu_stats() -> dict:
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            parts = [p.strip() for p in r.stdout.strip().split(",")]
            if len(parts) >= 5:
                return {
                    "gpu_util": float(parts[0]),
                    "vram_used": round(float(parts[1]) / 1000, 1),
                    "vram_total": round(float(parts[2]) / 1000, 1),
                    "temp_c": float(parts[3]),
                    "power_w": float(parts[4]),
                }
    except Exception:
        pass
    return {"gpu_util": 0, "vram_used": 0, "vram_total": 0, "temp_c": 0, "power_w": 0}


def get_system_stats() -> dict:
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "cpu_util": round(float(psutil.cpu_percent(interval=None)), 1),
            "ram_used_gb": round(float(mem.used) / 1e9, 1),
            "ram_total_gb": round(float(mem.total) / 1e9, 1),
            "ram_pct": round(float(mem.percent), 1),
        }
    except Exception:
        return {"cpu_util": 0, "ram_used_gb": 0, "ram_total_gb": 0, "ram_pct": 0}


# ── Torchrun ──────────────────────────────────────────────────────────────────
def find_torchrun() -> str:
    if args.torchrun:
        return args.torchrun
    candidates = [
        os.path.join(sys.exec_prefix, "Scripts", "torchrun.exe"),
        os.path.join(os.path.dirname(sys.executable), "torchrun.exe"),
        os.path.join(sys.exec_prefix, "Scripts", "torchrun"),
        os.path.join(os.path.dirname(sys.executable), "torchrun"),
        "torchrun.exe", "torchrun",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "torchrun"


# ── Transferencia de datos binarios ──────────────────────────────────────────
def send_json_msg(conn: socket.socket, data: dict):
    msg_bytes = (json.dumps(data, ensure_ascii=False) + "\n").encode()
    conn.sendall(struct.pack("!I", len(msg_bytes)) + msg_bytes)

def recv_json_msg(conn: socket.socket, timeout: float = 60.0) -> dict | None:
    try:
        conn.settimeout(timeout)
        raw_len = conn.recv(4)
        if not raw_len or len(raw_len) < 4:
            return None
        msg_len = struct.unpack("!I", raw_len)[0]
        data = b""
        while len(data) < msg_len:
            chunk = conn.recv(msg_len - len(data))
            if not chunk:
                return None
            data += chunk
        conn.settimeout(None)
        return json.loads(data.decode())
    except Exception:
        return None

def send_large(conn: socket.socket, data: bytes):
    conn.sendall(struct.pack("!Q", len(data)))
    sent, chunk = 0, 65536
    while sent < len(data):
        conn.sendall(data[sent:sent+chunk])
        sent += chunk

def recv_large(conn: socket.socket, timeout: float = 600.0) -> bytes | None:
    try:
        conn.settimeout(timeout)
        raw = conn.recv(8)
        if not raw or len(raw) < 8:
            return None
        size = struct.unpack("!Q", raw)[0]
        data = bytearray()
        while len(data) < size:
            chunk = conn.recv(min(65536, size - len(data)))
            if not chunk:
                return None
            data.extend(chunk)
        conn.settimeout(None)
        return bytes(data)
    except Exception as e:
        log("ERROR", f"recv_large: {e}")
        return None


def build_progress_payload(round_n: int, steps_done: int, loss: float | None) -> dict:
    stats = get_gpu_stats()
    sys_stats = get_system_stats()
    payload = {
        "type": "progress",
        "round": round_n,
        "step": steps_done,
        "loss": round(loss, 4) if loss is not None else None,
        "gpu_util": stats["gpu_util"],
        "vram_used": stats["vram_used"],
        "vram_total": stats["vram_total"],
        "temp_c": stats["temp_c"],
        "power_w": stats["power_w"],
        "power_target": round(POWER_TARGET_PERCENT, 1),
        "cpu_threads_target": target_threads(),
        **sys_stats,
    }
    return payload


def emit_local_stats(payload: dict):
    visible = dict(payload)
    visible.pop("type", None)
    print("[LOCAL_STATS]" + json.dumps(visible, ensure_ascii=False), flush=True)


def publish_progress(conn: socket.socket, payload: dict):
    try:
        send_json_msg(conn, payload)
    except Exception:
        pass
    emit_local_stats(payload)


# ── Pesos del adapter ─────────────────────────────────────────────────────────
def pack_adapter_weights(adapter_dir: str) -> bytes | None:
    """Lee el adapter guardado por Unsloth/PEFT y lo comprime para envio."""
    try:
        import torch

        state_dict = {}

        # Buscar archivos de pesos (safetensors o bin)
        for fname in sorted(os.listdir(adapter_dir)):
            fpath = os.path.join(adapter_dir, fname)
            if fname.endswith(".safetensors"):
                try:
                    from safetensors.torch import load_file
                    state_dict.update(load_file(fpath, device="cpu"))
                except ImportError:
                    # Fallback: leer con torch si no hay safetensors
                    sd = torch.load(fpath, map_location="cpu", weights_only=True)
                    state_dict.update(sd)
            elif fname == "adapter_model.bin":
                sd = torch.load(fpath, map_location="cpu", weights_only=True)
                state_dict.update(sd)

        if not state_dict:
            log("WARN", f"No se encontraron pesos en {adapter_dir}")
            return None

        # Convertir a fp16 para reducir tamaño de transferencia a la mitad
        state_dict_fp16 = {
            k: v.half() if v.is_floating_point() else v
            for k, v in state_dict.items()
        }

        # Serializar + comprimir
        buf = io.BytesIO()
        torch.save(state_dict_fp16, buf)
        compressed = gzip.compress(buf.getvalue(), compresslevel=1)

        size_mb = len(compressed) / 1024 / 1024
        params_m = sum(v.numel() for v in state_dict_fp16.values()) / 1e6
        log("PACK", f"Adapter listo: {params_m:.1f}M params | {size_mb:.1f} MB comprimido")
        return compressed

    except Exception as e:
        log("ERROR", f"pack_adapter_weights: {e}")
        return None


def unpack_and_save_weights(data: bytes, adapter_dir: str) -> bool:
    """Descomprime y guarda pesos promediados recibidos del coordinador."""
    try:
        import torch
        raw = gzip.decompress(data)
        state_dict = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
        os.makedirs(adapter_dir, exist_ok=True)
        out_path = os.path.join(adapter_dir, "adapter_model.bin")
        torch.save(state_dict, out_path)
        size_mb = len(data) / 1024 / 1024
        params_m = sum(v.numel() for v in state_dict.values()) / 1e6
        log("LOAD", f"Pesos promediados guardados: {params_m:.1f}M params | {size_mb:.1f} MB | {out_path}")
        return True
    except Exception as e:
        log("ERROR", f"unpack_and_save_weights: {e}")
        return False


# ── Entrenamiento local ───────────────────────────────────────────────────────
def run_training_round(cfg: dict, round_n: int, cid: int,
                       conn: socket.socket, torchrun_path: str) -> tuple[bool, int]:
    """
    Lanza train_ddp.py via torchrun y reporta progress al coordinador.
    Devuelve (exito, steps_completados).
    """
    train_script = os.path.join(args.train_dir, "train_ddp.py")
    if not os.path.exists(train_script):
        log("ERROR", f"No se encuentra {train_script}")
        return False, 0

    # Verificar si hay pesos seed para resumir
    resume_flag = []
    adapter_bin = os.path.join(ADAPTER_DIR, "adapter_model.bin")
    if os.path.exists(adapter_bin) and round_n > 1:
        resume_flag = [f"--resume_adapter={ADAPTER_DIR}"]
        log("TRAIN", f"Reanudando desde pesos promediados de ronda {round_n - 1}")

    requested_workers = int(cfg.get("num_workers", 4) or 4)
    num_workers = min(requested_workers, target_threads()) if args.max_power else requested_workers

    cmd = [
        torchrun_path,
        "--nnodes", "1",
        "--nproc_per_node", "1",
        "--node_rank", "0",
        "--master_addr", "127.0.0.1",
        "--master_port", str(12355 + cid),  # puerto unico por contributor
        train_script,
        f"--batch_size={cfg.get('batch_size', 1)}",
        f"--lora_rank={cfg.get('lora_rank', 16)}",
        f"--max_seq_length={cfg.get('max_seq_length', 2048)}",
        f"--num_workers={num_workers}",
        f"--learning_rate={cfg.get('learning_rate', 2e-4)}",
        f"--gradient_accumulation_steps={cfg.get('gradient_accumulation_steps', 4)}",
        f"--epochs={cfg.get('epochs', 1)}",
        f"--power_target={POWER_TARGET_PERCENT:.1f}",
        "--no-auto",
        "--mode=local",
        *resume_flag,
    ]

    if args.max_power:
        cmd.append("--max_power")

    log("TRAIN", f"Ronda {round_n} | cmd: {' '.join(cmd[-6:])}")

    proc_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        "cwd": args.train_dir,
    }

    # En Windows: HIGH priority para el proceso hijo
    if IS_WINDOWS and is_admin():
        proc_kwargs["creationflags"] = 0x00000080  # HIGH_PRIORITY_CLASS

    proc = subprocess.Popen(cmd, **proc_kwargs)

    steps_done = 0
    last_report = 0.0
    report_interval = 5  # segundos entre reportes de progress
    last_loss: float | None = None

    # Filtros para reenviar al coordinador las lineas mas utiles del log unsloth
    _LOG_KEYWORDS = (
        "loss", "epoch", "step", "unsloth", "lora", "grad",
        "learning_rate", "torch", "cuda", "vram", "memory",
        "warning", "error", "trainer", "%|", "it/s", "saved",
        "checkpoint", "dataset", "batch", "loading", "compiled",
    )

    def _should_forward(s: str) -> bool:
        low = s.lower()
        return any(k in low for k in _LOG_KEYWORDS)

    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip()
        if not line:
            continue
        print(f"  [TRAIN-{round_n}] {line}", flush=True)

        # Reenviar lineas relevantes al coordinador para que el Master las vea
        if _should_forward(line):
            try:
                send_json_msg(conn, {
                    "type": "log",
                    "round": round_n,
                    "line": line[:500],  # cap por seguridad
                })
            except Exception:
                pass

        # Parsear steps del log de Hugging Face Trainer
        if "'loss':" in line or '"loss":' in line:
            try:
                # Formato: {'loss': 2.345, 'learning_rate': ...}
                for key in ("'loss': ", '"loss": '):
                    if key in line:
                        idx = line.index(key) + len(key)
                        end = line.find(",", idx)
                        if end == -1:
                            end = line.find("}", idx)
                        loss = float(line[idx:end].strip())
                        last_loss = loss
                        steps_done += 1

                        # Reportar progreso al coordinador
                        now = time.time()
                        if now - last_report >= report_interval:
                            publish_progress(conn, build_progress_payload(round_n, steps_done, loss))
                            last_report = now
                        break
            except Exception:
                pass

    proc.wait()
    success = proc.returncode == 0

    publish_progress(conn, build_progress_payload(round_n, steps_done, last_loss))

    if success:
        log("TRAIN", f"Ronda {round_n} completada en {steps_done} steps ✓")
    else:
        log("ERROR", f"Ronda {round_n} fallo con codigo {proc.returncode}")

    return success, steps_done


# ── Loop principal de contribucion ────────────────────────────────────────────
def contribute():
    hw = detect_hardware()
    torchrun_path = find_torchrun()

    log("HW", f"GPU: {hw['gpu']} ({hw['vram_gb']} GB VRAM, {hw['ram_gb']} GB RAM, {hw['cores']} cores CPU)")
    log("HW", f"CUDA: {hw['cuda']} | torchrun: {torchrun_path}")

    if args.max_power:
        pm = MaxPowerManager(POWER_TARGET_PERCENT)
        pm.apply_all()
    else:
        log("INFO", "Modo estandar. Usa --max_power para maxima potencia.")

    reconnects = 0
    while reconnects <= args.max_reconnects:
        try:
            log("CONN", f"Conectando a {args.coordinator_ip}:{args.coordinator_port}...")
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Keep-alive para detectar desconexiones
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            conn.settimeout(30)
            conn.connect((args.coordinator_ip, args.coordinator_port))
            conn.settimeout(None)
            reconnects = 0  # reset al conectar exitosamente
            log("CONN", "Conectado al coordinador ✓")

            # Handshake
            send_json_msg(conn, {"type": "hello", **hw})
            resp = recv_json_msg(conn, timeout=30.0)
            if not resp or resp.get("type") != "welcome":
                raise ConnectionError("Handshake fallido")

            cid = resp.get("id", 0)
            n_contributors = resp.get("n_contributors", 1)
            log("CONN", f"Asignado ID #{cid} | Total contributors: {n_contributors}")
            log("CONN", "Esperando instrucciones del coordinador...")

            # Loop de rondas
            while True:
                msg = recv_json_msg(conn, timeout=600.0)
                if msg is None:
                    raise ConnectionError("Conexion perdida esperando instrucciones")

                mtype = msg.get("type", "")

                # Recibir pesos semilla antes de entrenar
                if mtype == "seed_weights":
                    size = msg.get("size", 0)
                    log("SEED", f"Recibiendo pesos semilla ({size/1024/1024:.1f} MB)...")
                    seed_data = recv_large(conn, timeout=300.0)
                    if seed_data:
                        if unpack_and_save_weights(seed_data, ADAPTER_DIR):
                            log("SEED", "Pesos semilla cargados, listo para entrenar")
                        else:
                            log("WARN", "Error cargando pesos semilla, entreno desde cero")
                    continue

                if mtype == "start":
                    r = msg.get("round", 1)
                    cfg = msg.get("config", {})

                    log("ROUND", f"{'='*20} RONDA {r} {'='*20}")
                    log("ROUND", f"Config: batch={cfg.get('batch_size')} rank={cfg.get('lora_rank')} "
                        f"seq={cfg.get('max_seq_length')} workers={cfg.get('num_workers')}")

                    # Entrenar
                    t_start = time.time()
                    success, steps = run_training_round(cfg, r, cid, conn, torchrun_path)
                    elapsed = time.time() - t_start
                    log("ROUND", f"Entrenamiento: {elapsed/60:.1f} min | {steps} steps")

                    # Notificar fin
                    send_json_msg(conn, {"type": "done", "round": r, "steps": steps})

                    # Enviar pesos del adapter
                    log("SEND", "Empaquetando y enviando pesos del adapter...")
                    packed = pack_adapter_weights(
                        os.path.join(args.train_dir, "outputs", "AgentMax-7b-v1.0", "adapter")
                    )
                    if packed:
                        send_large(conn, packed)
                    else:
                        log("WARN", "No se pudieron empaquetar los pesos. Enviando buffer vacio.")
                        send_large(conn, b"")

                    # Esperar pesos promediados
                    log("WAIT", "Esperando pesos promediados del coordinador...")
                    avg_msg = recv_json_msg(conn, timeout=600.0)
                    if avg_msg and avg_msg.get("type") == "averaged":
                        size = avg_msg.get("size", 0)
                        if size > 0:
                            log("RECV", f"Recibiendo pesos promediados ({size/1024/1024:.1f} MB)...")
                            avg_data = recv_large(conn, timeout=300.0)
                            if avg_data:
                                unpack_and_save_weights(avg_data, ADAPTER_DIR)
                                log("LOAD", "Pesos promediados listos para siguiente ronda")
                        else:
                            log("INFO", "Sin pesos promediados (solo 1 contributor)")

                    # Confirmar listo
                    send_json_msg(conn, {"type": "ready"})

                elif mtype == "completed":
                    log("DONE", "Entrenamiento federado completado! Gracias por contribuir.")
                    conn.close()
                    return

                elif mtype == "heartbeat":
                    send_json_msg(conn, {"type": "heartbeat"})

                else:
                    log("WARN", f"Mensaje desconocido: {mtype}")

        except ConnectionRefusedError:
            reconnects += 1
            log("RETRY", f"Coordinador no disponible. Intento {reconnects}/{args.max_reconnects} "
                f"en {args.reconnect_delay}s...")
            time.sleep(args.reconnect_delay)

        except (ConnectionError, OSError) as e:
            reconnects += 1
            log("RETRY", f"Conexion perdida: {e}. Intento {reconnects}/{args.max_reconnects} "
                f"en {args.reconnect_delay}s...")
            time.sleep(args.reconnect_delay)

        except KeyboardInterrupt:
            log("INFO", "Detenido por usuario")
            return

        except Exception as e:
            log("ERROR", f"Error inesperado: {e}")
            import traceback
            traceback.print_exc()
            reconnects += 1
            time.sleep(args.reconnect_delay)

    log("ERROR", f"Max reconexiones alcanzadas ({args.max_reconnects}). Abortando.")


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log("INFO", "AgentMax Contributor v2.0")
    log("INFO", f"Coordinador: {args.coordinator_ip}:{args.coordinator_port}")
    log("INFO", f"Max Power: {'SI' if args.max_power else 'NO (usa --max_power para activar)'}")
    log("INFO", f"Potencia objetivo real: {POWER_TARGET_PERCENT:.0f}%")
    log("INFO", f"Admin: {'SI' if is_admin() else 'NO'}")

    if args.max_power and not is_admin() and IS_WINDOWS:
        log("ADMIN", "Para ajustar limite de potencia GPU se necesitan permisos de administrador.")
        answer = input("Solicitar permisos de admin ahora? (s/N): ").strip().lower()
        if answer == "s":
            request_admin_and_relaunch()
        else:
            log("INFO", "Continuando sin admin (potencia reducida)...")

    contribute()
