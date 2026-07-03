#!/usr/bin/env python3
"""
HardwareDetective — escanea GPU, CPU, RAM y hace benchmark real.
Emite JSON por stdout para que Rust lo pueda parsear via training:log.
"""
import json
import os
import platform
import subprocess
import sys
import time


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def scan_gpu() -> dict:
    fields = "name,memory.total,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit,driver_version,cuda_version"
    raw = _run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"])
    if not raw:
        return {"available": False, "gpu": "No GPU NVIDIA", "vram_total_gb": 0, "vram_free_gb": 0}

    parts = [p.strip() for p in raw.split(",")]
    try:
        return {
            "available":      True,
            "gpu":            parts[0],
            "vram_total_gb":  round(float(parts[1]) / 1000, 1),
            "vram_free_gb":   round(float(parts[2]) / 1000, 1),
            "gpu_util_pct":   float(parts[3]) if parts[3] != "[N/A]" else 0,
            "temp_c":         float(parts[4]) if parts[4] != "[N/A]" else 0,
            "power_draw_w":   float(parts[5]) if parts[5] != "[N/A]" else 0,
            "power_limit_w":  float(parts[6]) if parts[6] != "[N/A]" else 0,
            "driver_version": parts[7],
            "cuda_version":   parts[8] if len(parts) > 8 else "?",
        }
    except (ValueError, IndexError):
        return {"available": False, "gpu": raw[:60], "vram_total_gb": 0, "vram_free_gb": 0}


def scan_cpu() -> dict:
    cores_logical = os.cpu_count() or 1
    try:
        import psutil
        freq = psutil.cpu_freq()
        freq_ghz = round(freq.max / 1000, 2) if freq else 0
        cores_physical = psutil.cpu_count(logical=False) or cores_logical
    except ImportError:
        freq_ghz = 0
        cores_physical = cores_logical

    name = "?"
    if platform.system() == "Windows":
        name = _run(["wmic", "cpu", "get", "Name", "/value"], timeout=4)
        name = name.replace("Name=", "").strip().splitlines()[0] if "Name=" in name else "?"
    elif platform.system() == "Linux":
        raw = _run(["grep", "-m1", "model name", "/proc/cpuinfo"])
        name = raw.split(":")[1].strip() if ":" in raw else "?"

    return {
        "name":           name,
        "cores_physical": cores_physical,
        "cores_logical":  cores_logical,
        "freq_max_ghz":   freq_ghz,
    }


def scan_ram() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {
            "total_gb":  round(vm.total / 1e9, 1),
            "free_gb":   round(vm.available / 1e9, 1),
            "used_pct":  vm.percent,
        }
    except ImportError:
        pass
    # Fallback Windows
    raw = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"], timeout=4)
    if "TotalPhysicalMemory=" in raw:
        try:
            total = int(raw.split("=")[1].strip())
            return {"total_gb": round(total / 1e9, 1), "free_gb": 0, "used_pct": 0}
        except Exception:
            pass
    return {"total_gb": 0, "free_gb": 0, "used_pct": 0}


def benchmark_gpu() -> dict:
    """Quick FP16 matmul benchmark → TFLOPS estimate."""
    try:
        import torch
        if not torch.cuda.is_available():
            return {"tflops_fp16": 0, "vram_peak_gb": 0, "available": False}

        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

        size = 4096
        warmup, iters = 3, 10
        a = torch.randn(size, size, dtype=torch.float16, device="cuda")
        b = torch.randn(size, size, dtype=torch.float16, device="cuda")

        for _ in range(warmup):
            torch.matmul(a, b)
        torch.cuda.synchronize()

        t0 = time.perf_counter()
        for _ in range(iters):
            torch.matmul(a, b)
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - t0

        # 2 * N^3 flops per matmul
        flops = 2 * (size ** 3) * iters
        tflops = flops / elapsed / 1e12
        peak_gb = torch.cuda.max_memory_allocated() / 1e9

        del a, b
        torch.cuda.empty_cache()

        return {
            "tflops_fp16":  round(tflops, 2),
            "vram_peak_gb": round(peak_gb, 2),
            "available":    True,
        }
    except Exception as e:
        return {"tflops_fp16": 0, "vram_peak_gb": 0, "available": False, "error": str(e)}


def full_scan(run_benchmark: bool = True) -> dict:
    gpu  = scan_gpu()
    cpu  = scan_cpu()
    ram  = scan_ram()
    bench = benchmark_gpu() if run_benchmark and gpu.get("available") else {"available": False}

    return {
        "gpu":       gpu,
        "cpu":       cpu,
        "ram":       ram,
        "benchmark": bench,
        "platform":  platform.system(),
        "python":    sys.version.split()[0],
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--no-benchmark", action="store_true")
    p.add_argument("--json", action="store_true", help="Emitir JSON puro en vez de bonito")
    args = p.parse_args()

    print("[HARDWARE_SCAN] Iniciando deteccion...", flush=True)
    result = full_scan(run_benchmark=not args.no_benchmark)

    if args.json:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    else:
        gpu  = result["gpu"]
        cpu  = result["cpu"]
        ram  = result["ram"]
        bench = result["benchmark"]

        print(f"[HARDWARE] GPU:  {gpu.get('gpu', 'N/A')}", flush=True)
        if gpu.get("available"):
            print(f"[HARDWARE]   VRAM: {gpu['vram_free_gb']}/{gpu['vram_total_gb']} GB libre", flush=True)
            print(f"[HARDWARE]   Temp: {gpu['temp_c']}°C  Power: {gpu['power_draw_w']}/{gpu['power_limit_w']}W", flush=True)
            print(f"[HARDWARE]   Driver: {gpu['driver_version']}  CUDA: {gpu['cuda_version']}", flush=True)
        print(f"[HARDWARE] CPU:  {cpu['name']} ({cpu['cores_physical']}P/{cpu['cores_logical']}L cores @ {cpu['freq_max_ghz']} GHz)", flush=True)
        print(f"[HARDWARE] RAM:  {ram['free_gb']}/{ram['total_gb']} GB libre ({ram['used_pct']}% usado)", flush=True)
        if bench.get("available"):
            print(f"[HARDWARE] BENCH: {bench['tflops_fp16']} TFLOPS FP16 (peak VRAM: {bench['vram_peak_gb']} GB)", flush=True)

        # Emit JSON for Tauri to parse
        print(f"[HARDWARE_JSON] {json.dumps(result, ensure_ascii=False)}", flush=True)
        print("[HARDWARE_SCAN] Completado.", flush=True)
