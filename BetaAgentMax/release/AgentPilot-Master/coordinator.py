#!/usr/bin/env python3
"""
NixControl Coordinator — Servidor central para entrenamiento federado REAL.

Protocolo TCP:
  <- {"type":"hello","gpu":"...","vram_gb":8.6,"cores":12,"steps":0}
  -> {"type":"welcome","id":1,"n_contributors":N,"min_to_start":M}

  [Si hay pesos semilla de ronda anterior]
  -> {"type":"seed_weights","size":N}  seguido de N bytes comprimidos

  -> {"type":"start","round":R,"config":{...}}
  <- {"type":"progress","step":S,"loss":X,"gpu_util":Y,"vram_used":Z}  (repetido)
  <- {"type":"done","round":R,"steps":S}
  <- <8-byte-uint64-size> <gzip-torch-bytes>  (pesos del adapter)

  [Coordinador promedia los pesos de todos los contributors]
  -> {"type":"averaged","round":R,"size":N}
  -> <8-byte-uint64-size> <gzip-torch-bytes>  (pesos promediados)
  <- {"type":"ready"}

  [Siguiente ronda o fin]
  -> {"type":"completed"}  (al terminar todas las rondas)
"""
import argparse, gzip, io, json, os, socket, struct, sys, threading, time
from dataclasses import dataclass, field
from typing import Optional
import torch

# ── Args ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--port",             type=int, default=12356)
parser.add_argument("--rounds",           type=int, default=3)
parser.add_argument("--epochs_per_round", type=int, default=1)
parser.add_argument("--min_contributors", type=int, default=1)
parser.add_argument("--max_contributors", type=int, default=100)
parser.add_argument("--wait_timeout",     type=int, default=300, help="Seg esperando contributors al inicio")
parser.add_argument("--weights_timeout",  type=int, default=900, help="Seg esperando pesos de cada ronda")
parser.add_argument("--train_dir",        default=".")
args = parser.parse_args()

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
OUTPUT_DIR = os.path.join(args.train_dir, "outputs", "federated")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Logging con timestamp ────────────────────────────────────────────────────
def log(tag: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{tag}] {msg}", flush=True)


# ── Transferencia de datos binarios grandes ──────────────────────────────────
def send_large(conn: socket.socket, data: bytes):
    """Envía un bloque de bytes con prefijo de 8 bytes (uint64)."""
    size = len(data)
    conn.sendall(struct.pack("!Q", size))
    sent = 0
    chunk = 65536
    while sent < size:
        end = min(sent + chunk, size)
        conn.sendall(data[sent:end])
        sent = end

def recv_large(conn: socket.socket, timeout: float = 600.0) -> Optional[bytes]:
    """Recibe un bloque de bytes con prefijo de 8 bytes."""
    try:
        conn.settimeout(timeout)
        raw = conn.recv(8)
        if not raw or len(raw) < 8:
            return None
        size = struct.unpack("!Q", raw)[0]
        if size > 2 * 1024 ** 3:   # sanity: max 2 GB
            log("ERROR", f"recv_large: tamaño sospechoso {size/1e9:.1f} GB")
            return None
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


# ── JSON helper ──────────────────────────────────────────────────────────────
def send_json(conn: socket.socket, data: dict):
    try:
        msg_bytes = (json.dumps(data, ensure_ascii=False) + "\n").encode()
        conn.sendall(struct.pack("!I", len(msg_bytes)) + msg_bytes)
    except Exception:
        pass

def recv_json(conn: socket.socket, timeout: float = 30.0) -> Optional[dict]:
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


# ── Federated Averaging ──────────────────────────────────────────────────────
def average_weights(weight_buffers: list[bytes]) -> bytes:
    """
    Promedia los state dicts de adapters LoRA.
    Cada buffer es gzip(torch.save(state_dict)).
    Devuelve gzip(torch.save(averaged_state_dict)).
    """
    if not weight_buffers:
        return b""
    if len(weight_buffers) == 1:
        log("AVG", "Solo 1 contributor — sin promedio necesario")
        return weight_buffers[0]

    log("AVG", f"Promediando {len(weight_buffers)} adapters...")
    t0 = time.time()

    state_dicts = []
    for i, buf in enumerate(weight_buffers):
        try:
            raw = gzip.decompress(buf)
            sd = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
            # Convertir a float32 para el promedio
            sd_f32 = {k: v.float() for k, v in sd.items()}
            state_dicts.append(sd_f32)
            log("AVG", f"  Adapter {i+1}: {len(sd)} tensors, {sum(v.numel() for v in sd.values())/1e6:.1f}M params")
        except Exception as e:
            log("AVG", f"  [WARN] No se pudo cargar adapter {i+1}: {e}")

    if not state_dicts:
        return b""

    # Promedio simple (FedAvg igual weight)
    avg_dict = {}
    for key in state_dicts[0]:
        tensors = [sd[key] for sd in state_dicts if key in sd]
        if tensors:
            avg_dict[key] = torch.stack(tensors).mean(dim=0).half()  # guardar en fp16

    # Serializar + comprimir
    buf = io.BytesIO()
    torch.save(avg_dict, buf)
    compressed = gzip.compress(buf.getvalue(), compresslevel=1)  # nivel 1 = rapido

    elapsed = time.time() - t0
    size_mb = len(compressed) / 1024 / 1024
    params = sum(v.numel() for v in avg_dict.values()) / 1e6
    log("AVG", f"Promedio listo: {params:.1f}M params | {size_mb:.1f} MB comprimido | {elapsed:.1f}s")
    return compressed


# ── Contributor dataclass ────────────────────────────────────────────────────
@dataclass
class Contributor:
    id: int
    conn: socket.socket
    addr: str
    hostname: str = ""
    gpu: str = ""
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    cores: int = 0
    cuda: str = ""
    status: str = "waiting"
    round: int = 0
    step: int = 0
    loss: Optional[float] = None
    gpu_util: float = 0.0
    vram_used: float = 0.0
    vram_total: float = 0.0
    cpu_util: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_pct: float = 0.0
    temp_c: float = 0.0
    power_w: float = 0.0
    power_target: float = 90.0
    cpu_threads_target: int = 0
    steps_done: int = 0
    alive: bool = True
    last_seen: float = field(default_factory=time.time)
    # Sincronizacion por ronda
    weights_ready: threading.Event = field(default_factory=threading.Event)
    averaged_ready: threading.Event = field(default_factory=threading.Event)
    weights_data: Optional[bytes] = None
    averaged_data: Optional[bytes] = None


# ── Coordinator ──────────────────────────────────────────────────────────────
class Coordinator:
    def __init__(self):
        self.contributors: dict[int, Contributor] = {}
        self.lock = threading.Lock()
        self.next_id = 1
        self.running = True
        self.current_round = 0

        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.server_sock.bind(("0.0.0.0", args.port))
        self.server_sock.listen(args.max_contributors)
        self.server_sock.settimeout(1.0)

    # ── Hardware y configuracion optima ──────────────────────────────────────
    def get_hardware_summary(self) -> list[dict]:
        with self.lock:
            return [
                {
                    "id": c.id,
                    "hostname": c.hostname or f"node-{c.id}",
                    "gpu": c.gpu,
                    "vram_gb": c.vram_gb,
                    "ram_gb": c.ram_gb,
                    "cores": c.cores,
                    "cuda": c.cuda,
                    "addr": c.addr,
                    "status": c.status,
                    "round": c.round,
                    "step": c.step,
                    "loss": c.loss,
                    "gpu_util": c.gpu_util,
                    "vram_used": c.vram_used,
                    "vram_total": c.vram_total,
                    "cpu_util": c.cpu_util,
                    "ram_used_gb": c.ram_used_gb,
                    "ram_total_gb": c.ram_total_gb,
                    "ram_pct": c.ram_pct,
                    "temp_c": c.temp_c,
                    "power_w": c.power_w,
                    "power_target": c.power_target,
                    "cpu_threads_target": c.cpu_threads_target,
                    "last_seen_sec": round(time.time() - c.last_seen, 1),
                    "alive": c.alive,
                }
                for c in self.contributors.values() if c.alive
            ]

    def emit_device_list(self):
        print("[DEVICE_LIST]" + json.dumps(self.get_hardware_summary(), ensure_ascii=False), flush=True)

    def telemetry_loop(self):
        while self.running:
            self.emit_device_list()
            time.sleep(2)

    def compute_optimal_config(self) -> dict:
        hw = self.get_hardware_summary()
        if not hw:
            return {"batch_size": 1, "lora_rank": 16, "max_seq_length": 2048,
                    "num_workers": 4, "learning_rate": 2e-4,
                    "gradient_accumulation_steps": 4, "epochs": args.epochs_per_round,
                    "n_contributors": 0, "total_vram_gb": 0, "min_vram_gb": 0,
                    "weakest_gpu": "none"}

        min_vram = min(h["vram_gb"] for h in hw)
        total_vram = sum(h["vram_gb"] for h in hw)
        min_cores = min((h.get("cpu_threads_target") or h["cores"]) for h in hw)

        # Escalar batch y rank segun la GPU mas debil
        if   min_vram >= 24: bs, lr = 8, 128
        elif min_vram >= 16: bs, lr = 4, 64
        elif min_vram >= 12: bs, lr = 4, 64
        elif min_vram >= 10: bs, lr = 2, 32
        elif min_vram >= 8:  bs, lr = 2, 32
        elif min_vram >= 6:  bs, lr = 1, 16
        else:                bs, lr = 1, 8

        return {
            "batch_size": bs,
            "lora_rank": lr,
            "max_seq_length": 4096,
            "num_workers": min(min_cores, 12),
            "learning_rate": 2e-4,
            "gradient_accumulation_steps": 4,
            "epochs": args.epochs_per_round,
            "n_contributors": len(hw),
            "total_vram_gb": round(total_vram, 1),
            "min_vram_gb": round(min_vram, 1),
            "weakest_gpu": next(h["gpu"] for h in hw if h["vram_gb"] == min_vram),
        }

    # ── Broadcast y unicast ───────────────────────────────────────────────────
    def broadcast(self, data: dict):
        with self.lock:
            dead = []
            for cid, c in self.contributors.items():
                if c.alive:
                    try:
                        send_json(c.conn, data)
                    except Exception:
                        dead.append(cid)
                else:
                    dead.append(cid)
            for cid in dead:
                self.contributors.pop(cid, None)

    def broadcast_large(self, data: bytes):
        """Enviar bloque binario grande a todos los contributors."""
        with self.lock:
            dead = []
            for cid, c in self.contributors.items():
                if c.alive:
                    try:
                        send_large(c.conn, data)
                    except Exception as e:
                        log("COORD", f"Error enviando pesos a #{cid}: {e}")
                        dead.append(cid)
            for cid in dead:
                self.contributors.pop(cid, None)

    # ── Handler por contributor (corre en hilo separado) ─────────────────────
    def handle_contributor(self, conn: socket.socket, addr: str):
        # Handshake
        hello = recv_json(conn, timeout=30.0)
        if not hello or hello.get("type") != "hello":
            conn.close()
            return

        with self.lock:
            cid = self.next_id
            self.next_id += 1
            c = Contributor(
                id=cid, conn=conn, addr=addr,
                hostname=hello.get("hostname", f"node-{cid}"),
                gpu=hello.get("gpu", "?"),
                vram_gb=hello.get("vram_gb", 0),
                ram_gb=hello.get("ram_gb", 0),
                cores=hello.get("cores", 0),
                cuda=hello.get("cuda", ""),
                power_target=float(hello.get("power_target", 90) or 90),
                cpu_threads_target=int(hello.get("cpu_threads_target", 0) or 0),
            )
            self.contributors[cid] = c

        n = len(self.contributors)
        log("CONN", f"#{cid} conectado: {c.hostname} | {c.gpu} ({c.vram_gb} GB, {c.cores} cores) desde {addr}")
        log("CONN", f"Total contributors: {n}/{args.max_contributors}")
        self.emit_device_list()

        send_json(conn, {
            "type": "welcome", "id": cid,
            "n_contributors": n,
            "min_to_start": args.min_contributors,
        })

        # Loop de mensajes entrantes
        while self.running and c.alive:
            msg_data = recv_json(conn, timeout=60.0)
            if msg_data is None:
                break

            c.last_seen = time.time()
            mtype = msg_data.get("type", "")

            if mtype == "progress":
                step = msg_data.get("step", 0)
                loss = msg_data.get("loss")
                gpu_u = msg_data.get("gpu_util", 0)
                vram_u = msg_data.get("vram_used", 0)
                c.status = "training"
                c.round = int(msg_data.get("round", c.round or self.current_round))
                c.step = int(step or 0)
                c.loss = float(loss) if isinstance(loss, (int, float)) else None
                c.gpu_util = float(gpu_u or 0)
                c.vram_used = float(vram_u or 0)
                c.vram_total = float(msg_data.get("vram_total", 0) or 0)
                c.cpu_util = float(msg_data.get("cpu_util", 0) or 0)
                c.ram_used_gb = float(msg_data.get("ram_used_gb", 0) or 0)
                c.ram_total_gb = float(msg_data.get("ram_total_gb", 0) or 0)
                c.ram_pct = float(msg_data.get("ram_pct", 0) or 0)
                c.temp_c = float(msg_data.get("temp_c", 0) or 0)
                c.power_w = float(msg_data.get("power_w", 0) or 0)
                c.power_target = float(msg_data.get("power_target", c.power_target) or c.power_target)
                c.cpu_threads_target = int(msg_data.get("cpu_threads_target", c.cpu_threads_target) or 0)
                shown_loss = f"{c.loss:.4f}" if c.loss is not None else "?"
                log("PROG", f"#{cid} step={c.step} loss={shown_loss} gpu={c.gpu_util}% cpu={c.cpu_util}% vram={c.vram_used}GB")
                self.emit_device_list()

            elif mtype == "log":
                # Log streaming en vivo desde el contributor (unsloth/trainer)
                line = str(msg_data.get("line", ""))[:500]
                rnd = msg_data.get("round", c.round or 0)
                if line:
                    log("UNSLOTH", f"#{cid}[r{rnd}] {line}")

            elif mtype == "done":
                rnd = msg_data.get("round", 0)
                steps = msg_data.get("steps", 0)
                c.status = "uploading"
                c.round = int(rnd or c.round)
                c.steps_done = int(steps or 0)
                self.emit_device_list()
                log("DONE", f"#{cid} termino ronda {rnd} ({steps} steps). Recibiendo pesos...")

                # Recibir pesos del adapter
                weight_data = recv_large(conn, timeout=float(args.weights_timeout))
                if weight_data:
                    size_mb = len(weight_data) / 1024 / 1024
                    log("RECV", f"#{cid} pesos recibidos: {size_mb:.1f} MB")
                    c.weights_data = weight_data
                    c.weights_ready.set()
                else:
                    log("WARN", f"#{cid} no envio pesos (timeout o desconexion)")
                    c.weights_ready.set()  # igual liberar para no bloquear
                c.status = "averaging"
                self.emit_device_list()

                # Esperar pesos promediados del coordinator
                if not c.averaged_ready.wait(timeout=float(args.weights_timeout)):
                    log("WARN", f"#{cid} timeout esperando promedio")
                    break

                # Enviar pesos promediados al contributor
                if c.averaged_data:
                    size_mb = len(c.averaged_data) / 1024 / 1024
                    log("SEND", f"#{cid} enviando pesos promediados: {size_mb:.1f} MB")
                    send_json(conn, {"type": "averaged", "round": rnd, "size": len(c.averaged_data)})
                    send_large(conn, c.averaged_data)
                    # Resetear para siguiente ronda
                    c.averaged_data = None
                    c.averaged_ready.clear()
                    c.weights_ready.clear()
                    c.weights_data = None
                else:
                    send_json(conn, {"type": "averaged", "round": rnd, "size": 0})

            elif mtype == "ready":
                c.status = "ready"
                log("REDY", f"#{cid} cargo pesos promediados, listo para siguiente ronda")
                self.emit_device_list()

            elif mtype == "heartbeat":
                send_json(conn, {"type": "heartbeat_ack"})

        # Desconexion
        with self.lock:
            c.alive = False
            c.weights_ready.set()   # liberar si estaba bloqueado
            c.averaged_ready.set()
            self.contributors.pop(cid, None)
        log("DISC", f"#{cid} desconectado. Contributors activos: {len(self.contributors)}")
        self.emit_device_list()
        try:
            conn.close()
        except Exception:
            pass

    # ── Esperar contributors iniciales ────────────────────────────────────────
    def wait_for_contributors(self):
        log("WAIT", f"Esperando contributors en puerto {args.port}...")
        start = time.time()
        while self.running:
            elapsed = time.time() - start
            n = len(self.contributors)
            if n >= args.min_contributors:
                log("WAIT", f"{n} contributors listos en {elapsed:.0f}s")
                break
            if elapsed > args.wait_timeout and n > 0:
                log("WAIT", f"Timeout {args.wait_timeout}s con {n} contributors, empezando...")
                break
            if elapsed % 10 < 0.5:
                log("WAIT", f"{n}/{args.min_contributors} conectados ({elapsed:.0f}s)...")
                self.emit_device_list()
            time.sleep(1)

    # ── Recolectar pesos de todos los contributors de la ronda ────────────────
    def collect_and_average(self, round_n: int, active_ids: list[int]) -> bytes:
        """
        Espera pesos de todos los contributors activos,
        los promedia y los distribuye de vuelta.
        """
        log("AVG", f"Esperando pesos de {len(active_ids)} contributors (timeout={args.weights_timeout}s)...")
        deadline = time.time() + args.weights_timeout

        # Esperar a que cada contributor sete su weights_ready
        with self.lock:
            contributors_snapshot = {
                cid: c for cid, c in self.contributors.items()
                if cid in active_ids and c.alive
            }

        for cid, c in contributors_snapshot.items():
            remaining = max(0.0, deadline - time.time())
            if not c.weights_ready.wait(timeout=remaining):
                log("WARN", f"#{cid} no envio pesos a tiempo")

        # Recoger pesos disponibles
        weight_buffers = []
        with self.lock:
            for cid in active_ids:
                c = self.contributors.get(cid)
                if c and c.weights_data:
                    weight_buffers.append(c.weights_data)
                    log("AVG", f"  Usando pesos de #{cid} ({len(c.weights_data)/1024/1024:.1f} MB)")

        if not weight_buffers:
            log("WARN", "No se recibieron pesos de ningun contributor")
            return b""

        # Promedio real con torch
        averaged = average_weights(weight_buffers)

        # Guardar checkpoint
        checkpoint_path = os.path.join(OUTPUT_DIR, f"round_{round_n}_avg.pt.gz")
        with open(checkpoint_path, "wb") as f:
            f.write(averaged)
        log("SAVE", f"Checkpoint guardado: {checkpoint_path} ({len(averaged)/1024/1024:.1f} MB)")

        # Distribuir pesos promediados a cada contributor
        with self.lock:
            for cid in active_ids:
                c = self.contributors.get(cid)
                if c and c.alive:
                    c.averaged_data = averaged
                    c.status = "syncing"
                    c.averaged_ready.set()

        log("AVG", f"Pesos promediados distribuidos a {len(active_ids)} contributors")
        self.emit_device_list()
        return averaged

    # ── Loop principal ────────────────────────────────────────────────────────
    def run(self):
        # Hilo aceptador
        def accept_loop():
            while self.running:
                try:
                    conn, addr = self.server_sock.accept()
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    t = threading.Thread(
                        target=self.handle_contributor,
                        args=(conn, addr[0]),
                        daemon=True
                    )
                    t.start()
                except socket.timeout:
                    continue
                except Exception:
                    break

        threading.Thread(target=accept_loop, daemon=True).start()
        threading.Thread(target=self.telemetry_loop, daemon=True).start()
        self.wait_for_contributors()

        # Resumen de hardware
        hw = self.get_hardware_summary()
        cfg = self.compute_optimal_config()
        n = len(hw)

        log("INFO", "=" * 60)
        log("INFO", f"{n} contributors listos")
        for h in hw:
            lbl = "Master" if h["id"] == 1 else f"Worker-{h['id']}"
            log("INFO", f"  [{lbl}] #{h['id']}: {h['gpu']} ({h['vram_gb']} GB, {h['cores']} cores) @ {h['addr']}")
        log("INFO", f"Config optima: batch={cfg['batch_size']} rank={cfg['lora_rank']} "
            f"seq={cfg['max_seq_length']} workers={cfg['num_workers']}")
        log("INFO", f"GPU mas debil: {cfg['weakest_gpu']} ({cfg['min_vram_gb']} GB) | "
            f"VRAM total: {cfg['total_vram_gb']} GB")
        log("INFO", f"Rondas: {args.rounds} | Epochs/ronda: {args.epochs_per_round}")
        log("INFO", "=" * 60)

        # Rondas de entrenamiento federado
        seed_weights: Optional[bytes] = None
        for r in range(1, args.rounds + 1):
            self.current_round = r
            t_round_start = time.time()

            with self.lock:
                active_ids = [cid for cid, c in self.contributors.items() if c.alive]

            if not active_ids:
                log("WARN", "No hay contributors activos. Deteniendo.")
                break

            log("ROUND", f"{'='*20} RONDA {r}/{args.rounds} {'='*20}")
            log("ROUND", f"Contributors activos: {active_ids}")
            with self.lock:
                for cid in active_ids:
                    c = self.contributors.get(cid)
                    if c:
                        c.status = "starting"
                        c.round = r
                        c.step = 0
                        c.loss = None
            self.emit_device_list()

            # Si hay pesos de la ronda anterior, enviarlos antes de start
            if seed_weights:
                log("SEED", f"Enviando pesos semilla a todos ({len(seed_weights)/1024/1024:.1f} MB)...")
                send_json_to_all = lambda d: self.broadcast(d)
                self.broadcast({"type": "seed_weights", "size": len(seed_weights)})
                self.broadcast_large(seed_weights)
                log("SEED", "Pesos semilla enviados")

            # Señal de inicio
            self.broadcast({"type": "start", "round": r, "config": cfg, "has_seed": seed_weights is not None})

            # Recolectar y promediar pesos al final de la ronda
            seed_weights = self.collect_and_average(r, active_ids)

            elapsed = time.time() - t_round_start
            log("ROUND", f"Ronda {r} completada en {elapsed/60:.1f} min")

        # Fin
        log("INFO", "Entrenamiento federado completado!")
        self.broadcast({"type": "completed"})
        with self.lock:
            for c in self.contributors.values():
                c.status = "completed"
        self.emit_device_list()
        self.running = False

        # Resumen final
        if seed_weights:
            final_path = os.path.join(OUTPUT_DIR, "final_adapter.pt.gz")
            with open(final_path, "wb") as f:
                f.write(seed_weights)
            log("SAVE", f"Adapter final: {final_path} ({len(seed_weights)/1024/1024:.1f} MB)")

    def stop(self):
        self.running = False


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log("INFO", f"NixControl Coordinator v2.0 — FedAvg Real")
    log("INFO", f"Puerto: {args.port} | Max contributors: {args.max_contributors}")
    log("INFO", f"Rondas: {args.rounds} | Epochs/ronda: {args.epochs_per_round}")
    log("INFO", f"Min contributors: {args.min_contributors} | Output: {OUTPUT_DIR}")

    coord = Coordinator()
    try:
        coord.run()
    except KeyboardInterrupt:
        log("INFO", "Detenido por usuario")
        coord.stop()
    except Exception as e:
        log("ERROR", f"Error fatal: {e}")
        raise
