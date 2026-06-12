#!/usr/bin/env python3
"""AgentMax local backend — production entry point for the desktop app."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("AGENTMAX_PRODUCT_MODE", "1")
os.environ.setdefault("AGENTMAX_RUNTIME_MODE", "full")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BETA_SCRIPT = Path(__file__).resolve().parent / "agentmax_beta_server.py"
_DEFAULT_MODE = "auto"


def _health_url() -> str:
    port = os.environ.get("AGENTMAX_API_PORT", "7790")
    host = os.environ.get("AGENTMAX_HOST", "127.0.0.1")
    return f"http://{host}:{port}/health"


def _fetch_health_sync(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=2) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body) if body else {}
        return resp.status, data


async def _probe_health(timeout_sec: float = 25.0, interval: float = 0.5) -> dict | None:
    """Probe /health without blocking the runtime asyncio loop (uvicorn)."""
    deadline = time.monotonic() + timeout_sec
    url = _health_url()
    while time.monotonic() < deadline:
        try:
            status, data = await asyncio.to_thread(_fetch_health_sync, url)
            if status == 200 and data.get("ok"):
                return data
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            pass
        await asyncio.sleep(interval)
    return None


def _run_beta_fallback(reason: str) -> None:
    print(f"[agentmax_server] falling back to beta HTTP server ({reason})", flush=True)
    os.environ["AGENTMAX_FALLBACK_REASON"] = reason
    import runpy

    runpy.run_path(str(_BETA_SCRIPT), run_name="__main__")


async def _run_runtime(*, block: bool = True) -> tuple[bool, str, bool]:
    from core.runtime import AgentMaxRuntime

    rt = AgentMaxRuntime()
    try:
        await rt.start()
    except (ImportError, ModuleNotFoundError) as exc:
        print(f"[agentmax_server] runtime start failed: {exc}", flush=True)
        try:
            await rt.shutdown()
        except Exception:
            pass
        return False, str(exc), True
    except Exception as exc:
        print(f"[agentmax_server] runtime start rejected: {exc}", flush=True)
        try:
            await rt.shutdown()
        except Exception:
            pass
        return False, str(exc), False

    probe_timeout = float(os.environ.get("AGENTMAX_RUNTIME_PROBE_SEC", "25"))
    probe = await _probe_health(timeout_sec=probe_timeout)
    if probe is None:
        print("[agentmax_server] runtime health probe timed out", flush=True)
        try:
            await rt.shutdown()
        except Exception:
            pass
        return False, "runtime health probe timed out", True

    backend = str(probe.get("backend", ""))
    if backend != "agentmax" or probe.get("runtime_mode") != "full" or probe.get("limited"):
        print(f"[agentmax_server] unexpected backend id: {backend!r}", flush=True)
        try:
            await rt.shutdown()
        except Exception:
            pass
        return False, f"unexpected runtime health payload: {probe}", False

    print(
        f"[agentmax_server] runtime ready backend={backend} version={probe.get('version')}",
        flush=True,
    )

    if not block:
        return True, "", False

    try:
        await rt.wait_for_shutdown()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await rt.shutdown()
    return True, "", False


def _set_event_loop_policy() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main() -> None:
    mode = os.environ.get("AGENTMAX_SERVER_MODE", _DEFAULT_MODE).strip().lower()

    if mode == "beta":
        import runpy

        runpy.run_path(str(_BETA_SCRIPT), run_name="__main__")
        return

    _set_event_loop_policy()

    if mode == "runtime":
        ok, _reason, _fallback_allowed = asyncio.run(_run_runtime(block=True))
        raise SystemExit(0 if ok else 1)

    async def _auto() -> None:
        ok, reason, fallback_allowed = await _run_runtime(block=True)
        if not ok:
            if not fallback_allowed:
                raise RuntimeError(reason)
            _run_beta_fallback(reason)

    try:
        asyncio.run(_auto())
    except Exception as exc:
        print(f"[agentmax_server] auto mode error: {exc}", flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
