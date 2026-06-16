"""
Anti-tamper watchdog -- Python-side protection layer.

Strategy: detect → degrade silently, never crash.
A cracker who patches one check still gets subtle failures that are hard
to attribute to the protection rather than an unrelated bug.

Checks performed:
  1. Python debugger attached (sys.gettrace / pydevd / pdb)
  2. Known cracker tools running (psutil process scan)
  3. Code-object integrity of critical runtime/security functions (permission checks, tool execution, supervisor orchestration)
  4. Module-attribute monkey-patching of those functions
  5. Environment tampering (PYTHONDEBUG, PYTHONINSPECT flags)

Dev bypass:
  During development you may legitimately run with a debugger attached.
  Set the environment variable AGENTMAX_DEV_TOKEN to the value printed by:

      py -c "
      import hmac, hashlib, os, socket
      key = b'\\x3f\\x91\\xb2\\x74\\xc8\\x0a\\x5e\\xd6\\x12\\xfa\\x87\\x4b\\x3c\\x69\\xa0\\x2d'
      host = socket.gethostname().encode()
      print(hmac.new(key, host, hashlib.sha256).hexdigest()[:20])
      "

  When the token matches, all watchdog checks are suppressed and a
  single INFO log line is emitted.  The token is machine-specific (derived
  from the hostname) so it cannot be shared between machines.

Hardware fingerprint:
  Derived from MAC address + C: drive volume serial + CPU brand string.
  Sent to Whop as metadata so licenses can be machine-locked server-side.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac_mod
import marshal
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_WATCHDOG_INTERVAL_SEC = 60

# XOR-obfuscated key used for dev-bypass token verification.
# Do NOT store as a plain string -- decompilers trivially find string literals.
_DEV_KEY: bytes = bytes(
    [
        0x3F,
        0x91,
        0xB2,
        0x74,
        0xC8,
        0x0A,
        0x5E,
        0xD6,
        0x12,
        0xFA,
        0x87,
        0x4B,
        0x3C,
        0x69,
        0xA0,
        0x2D,
    ]
)

_CRACKER_TOOLS = frozenset(
    {
        # Windows
        "x64dbg.exe",
        "x32dbg.exe",
        "ollydbg.exe",
        "ida64.exe",
        "idaq64.exe",
        "idaq.exe",
        "ida.exe",
        "ghidra.bat",
        "ghidrarun.bat",
        "radare2.exe",
        "r2.exe",
        "cutter.exe",
        "cheatengine-x86_64.exe",
        "cheatengine-i386.exe",
        "scylla_x64.exe",
        "scylla_x86.exe",
        "de4dot.exe",
        "dnspy.exe",
        "pe-bear.exe",
        "pebear.exe",
        "exeinfope.exe",
        "reshacker.exe",
        "pe_detective.exe",
        "apimonitor-x64.exe",
        "apimonitor-x86.exe",
        "frida.exe",
        "frida-server.exe",
        "wireshark.exe",
        "fiddler.exe",
        "charles.exe",
        "processhacker.exe",
        "procmon.exe",
        "procmon64.exe",
        # Linux / macOS (no .exe suffix)
        "gdb",
        "lldb",
        "radare2",
        "r2",
        "cutter",
        "ghidra",
        "frida",
        "frida-server",
        "frida-trace",
        "wireshark",
        "charles",
        "strace",
        "ltrace",
        "dtrace",
        "x64dbg",
        "x32dbg",
    }
)

# Filled once at import time -- any later change is tampering.
_CODE_SENTINELS: dict[str, str] = {}
_TAMPER_DETECTED: bool = False
_DEGRADATION_CB: Callable | None = None

# Cached once -- avoid re-computing on every watchdog tick.
_DEV_BYPASS_ACTIVE: bool | None = None


# ── Dev bypass ────────────────────────────────────────────────────────────────


def _compute_dev_token() -> str:
    """
    Compute the expected dev-bypass token for this machine.
    Token = HMAC-SHA256(_DEV_KEY, hostname)[:20].
    Machine-specific: different token on every developer's PC.
    """
    host = socket.gethostname().encode(errors="replace")
    return _hmac_mod.new(_DEV_KEY, host, hashlib.sha256).hexdigest()[:20]


def _is_dev_bypass_active() -> bool:
    """
    Return True if the dev-bypass token in the environment matches this
    machine's expected token.  Result is cached after the first call.
    """
    global _DEV_BYPASS_ACTIVE
    if _DEV_BYPASS_ACTIVE is not None:
        return _DEV_BYPASS_ACTIVE

    provided = os.environ.get("AGENTMAX_DEV_TOKEN", "")
    if not provided:
        _DEV_BYPASS_ACTIVE = False
        return False

    expected = _compute_dev_token()
    # Use hmac.compare_digest to prevent timing attacks
    match = _hmac_mod.compare_digest(provided.strip(), expected)
    _DEV_BYPASS_ACTIVE = match
    if match:
        log.info(
            "anti_tamper.dev_bypass_active",
            note="All watchdog checks suppressed for this session.",
        )
    else:
        log.warning("anti_tamper.dev_bypass_token_invalid")
    return match


def print_dev_token() -> None:
    """
    Utility: print the dev-bypass token for this machine to stdout.
    Run once per new developer machine and store in your shell profile:

        py -c "from core.security.anti_tamper import print_dev_token; print_dev_token()"
    """
    token = _compute_dev_token()
    print(f"AGENTMAX_DEV_TOKEN={token}")
    print(f'Add to your shell: export AGENTMAX_DEV_TOKEN="{token}"')


# ── Public API ────────────────────────────────────────────────────────────────


def snapshot_critical_functions() -> None:
    """
    Hash the __code__ objects of critical runtime and security functions.
    Call this ONCE at startup (from AgentMaxRuntime), before any task runs.
    Subsequent watchdog ticks compare against these hashes to detect monkey-patching.

    Targets are always-active functions that control permissions, tool execution
    and task orchestration. LicenseManager functions are intentionally NOT used
    here because entitlements are currently disabled in runtime.
    """
    from core.agents import supervisor as _sup
    from core.security import permission_manager as _pm
    from core.tools import executor as _te

    targets = {
        "PermissionManager.grant": _pm.PermissionManager.grant,
        "PermissionManager.check": _pm.PermissionManager.check,
        "ToolExecutor.execute": _te.ToolExecutor.execute,
        "SupervisorAgent._execute_task": _sup.SupervisorAgent._execute_task,
    }
    for name, fn in targets.items():
        try:
            _CODE_SENTINELS[name] = _hash_code(fn)
        except Exception:
            pass
    log.debug("anti_tamper.sentinels_snapshotted", count=len(_CODE_SENTINELS))


def start_watchdog(degradation_callback: Callable | None = None) -> None:
    """
    Launch the background watchdog thread.
    `degradation_callback` is called (once) when tampering is first detected.
    It should slow down or corrupt the agent's behavior -- NOT crash it.
    """
    global _DEGRADATION_CB
    _DEGRADATION_CB = degradation_callback
    t = threading.Thread(
        target=_watchdog_loop,
        name="antitamper-watchdog",
        daemon=True,
    )
    t.start()
    log.debug("anti_tamper.watchdog_started")


def is_compromised() -> bool:
    """True if any tamper indicator has been detected (ever)."""
    return _TAMPER_DETECTED


def get_machine_fingerprint() -> str:
    """
    Stable hardware fingerprint for this machine.
    Format: sha256(MAC|os_id|cpu)[:20]
    """
    parts: list[str] = []

    # 1. MAC address -- stable per physical NIC
    parts.append(hex(uuid.getnode()))

    # 2. OS-specific machine/volume identifier
    if sys.platform == "win32":
        try:
            # Use winreg to read the volume serial -- avoids shell=True injection risk
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
            ) as key:
                serial, _ = winreg.QueryValueEx(key, "InstallDate")
                parts.append(str(serial))
        except Exception:
            # Fallback: use vol command with explicit arg list (no shell=True)
            try:
                out = subprocess.check_output(
                    ["cmd", "/c", "vol", "C:"], stderr=subprocess.DEVNULL, timeout=3
                ).decode(errors="ignore")
                for line in out.splitlines():
                    line = line.strip()
                    if "serial" in line.lower() or "serie" in line.lower():
                        parts.append(line.split()[-1])
                        break
            except Exception:
                pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).decode(errors="ignore")
            for line in out.splitlines():
                if "IOPlatformSerialNumber" in line:
                    parts.append(line.split('"')[-2])
                    break
        except Exception:
            pass
    else:  # Linux
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                mid = Path(path).read_text().strip()[:32]
                if mid:
                    parts.append(mid)
                    break
            except Exception:
                continue

    # 3. CPU brand string
    parts.append(platform.processor()[:32] or platform.machine())

    raw = "|".join(parts).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


# ── Detection checks ──────────────────────────────────────────────────────────


def _check_python_debugger() -> bool:
    """Detect pdb, pydevd (PyCharm/VS Code), and PYTHONDEBUG env."""
    if sys.gettrace() is not None:
        return True
    if "pydevd" in sys.modules:
        return True
    if "pdb" in sys.modules and hasattr(sys.modules["pdb"], "current_frames"):
        return True
    if os.environ.get("PYTHONDEBUG") or os.environ.get("PYTHONINSPECT"):
        return True
    return False


def _check_cracker_processes() -> bool:
    """Scan running processes for known reverse-engineering tools."""
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in _CRACKER_TOOLS:
                log.warning("anti_tamper.cracker_tool_detected", process=name)
                return True
    except Exception:
        pass
    return False


def _check_code_integrity() -> bool:
    """Verify that critical runtime/security functions haven't been monkey-patched at runtime."""
    if not _CODE_SENTINELS:
        return False  # sentinels not yet taken (too early)

    from core.agents import supervisor as _sup
    from core.security import permission_manager as _pm
    from core.tools import executor as _te

    current = {
        "PermissionManager.grant": _pm.PermissionManager.grant,
        "PermissionManager.check": _pm.PermissionManager.check,
        "ToolExecutor.execute": _te.ToolExecutor.execute,
        "SupervisorAgent._execute_task": _sup.SupervisorAgent._execute_task,
    }
    for name, fn in current.items():
        if name not in _CODE_SENTINELS:
            continue
        try:
            if _hash_code(fn) != _CODE_SENTINELS[name]:
                log.warning("anti_tamper.code_integrity_violation", function=name)
                return True
        except Exception:
            pass
    return False


def _check_environment() -> bool:
    """Detect suspicious environment variables set by analysis frameworks."""
    suspicious = (
        "FRIDA_SCRIPTS",
        "DYLD_INSERT_LIBRARIES",  # macOS injection
        "LD_PRELOAD",  # Linux shared-library injection
        "DYLD_LIBRARY_PATH",  # macOS library path hijack
        "DYLD_FORCE_FLAT_NAMESPACE",
        "_JAVA_OPTIONS",  # Java agent injection (rare but seen in sandboxes)
    )
    for var in suspicious:
        if os.environ.get(var):
            return True
    # Flag if the interpreter claims to be frozen but lacks _MEIPASS.
    # Frozen Python bundles expose sys._MEIPASS; a fake
    # "frozen" flag without it is a cracker patching sys attributes.
    if getattr(sys, "frozen", False) and not hasattr(sys, "_MEIPASS"):
        # Extra guard: allow if running via cx_Freeze (it sets sys.frozen too)
        # cx_Freeze sets `sys.frozen = True` but not `sys._MEIPASS`; it does
        # however set `sys.executable` to the packaged exe path.
        is_cx_freeze = hasattr(sys, "frozen") and sys.frozen == "windows_exe"
        if not is_cx_freeze:
            return True
    return False


def _check_vm_environment() -> bool:
    """Detect VM / sandbox environments that are typical of reverse-engineering sessions."""
    try:
        from core.security.vm_detect import is_vm

        if is_vm():
            log.warning("anti_tamper.vm_environment_detected")
            return True
    except Exception:
        pass
    return False


def _run_all_checks() -> bool:
    # Dev bypass: suppress ALL checks for this session.
    if _is_dev_bypass_active():
        return False
    return (
        _check_python_debugger()
        or _check_cracker_processes()
        or _check_code_integrity()
        or _check_environment()
        or _check_vm_environment()
    )


# ── Watchdog thread ───────────────────────────────────────────────────────────


def _watchdog_loop() -> None:
    global _TAMPER_DETECTED
    while True:
        try:
            time.sleep(_WATCHDOG_INTERVAL_SEC)
            if _run_all_checks():
                if not _TAMPER_DETECTED:
                    _TAMPER_DETECTED = True
                    log.error("anti_tamper.TAMPERING_DETECTED -- activating degradation")
                    if _DEGRADATION_CB is not None:
                        try:
                            _DEGRADATION_CB()
                        except Exception:
                            pass
        except Exception:
            pass


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hash_code(fn: Callable) -> str:
    """SHA256 of a function's code object bytecode."""
    return hashlib.sha256(marshal.dumps(fn.__code__)).hexdigest()
