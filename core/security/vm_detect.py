"""
VM / sandbox / hypervisor detection for the Python layer.

Runs multiple heuristics:
  1. Known VM guest-agent process names (via psutil)
  2. Registry keys for VirtualBox / VMware / Hyper-V (Windows)
  3. DMI product/vendor strings (Linux)
  4. VM-specific device files and driver binaries

Strategy: detect → report to caller, never crash.  The Python watchdog
escalates to the degradation callback when this returns True.
"""

from __future__ import annotations

import sys
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_VM_PROCESSES = frozenset(
    {
        # Windows (lowercase .exe names)
        "vboxservice.exe",
        "vboxtray.exe",
        "vmtoolsd.exe",
        "vmwaretray.exe",
        "vmwareuser.exe",
        "vmacthlp.exe",
        "vmsrvc.exe",
        "vmusrvc.exe",
        "qemu-ga.exe",
        "xenservice.exe",
        "vmicvss.exe",
        "vmicshutdown.exe",
        "vmicguestinterface.exe",
        "vmicheartbeat.exe",
        "prl_tools.exe",
        "prl_cc.exe",
        "sandboxiedcomlaunch.exe",
        "sandboxierpcss.exe",
        "vboxheadless.exe",
        "vboxsvc.exe",
        # Linux / macOS (no .exe)
        "vboxservice",
        "vmtoolsd",
        "qemu-ga",
        "xenservice",
        "prl_tools",
        "prl_cc",
        "vdagent",
        "spice-vdagent",
        "vboxheadless",
        "vboxsvc",
    }
)

_VM_REGISTRY_KEYS = (
    r"SOFTWARE\Oracle\VirtualBox Guest Additions",
    r"SOFTWARE\VMware, Inc.\VMware Tools",
    r"SOFTWARE\Microsoft\Virtual Machine\Guest\Parameters",
    r"SYSTEM\CurrentControlSet\Services\VBoxGuest",
    r"SYSTEM\CurrentControlSet\Services\VBoxMouse",
    r"SYSTEM\CurrentControlSet\Services\VBoxSF",
    r"SYSTEM\CurrentControlSet\Services\vmci",
    r"SYSTEM\CurrentControlSet\Services\vmhgfs",
    r"SYSTEM\CurrentControlSet\Services\VMMouse",
)

_VM_DMI_PATHS = (
    "/sys/class/dmi/id/product_name",
    "/sys/class/dmi/id/sys_vendor",
    "/sys/class/dmi/id/board_vendor",
    "/sys/class/dmi/id/chassis_vendor",
)

_VM_DMI_KEYWORDS = frozenset(
    {
        "virtualbox",
        "vmware",
        "qemu",
        "kvm",
        "xen",
        "hyper-v",
        "bochs",
        "innotek",
        "parallels",
        "vrtual",  # "vrtual" = Hyper-V truncated string
        "microsoft corporation",  # Hyper-V reports this as chassis vendor
    }
)

_VM_DRIVER_FILES_WIN = (
    r"C:\Windows\System32\drivers\VBoxMouse.sys",
    r"C:\Windows\System32\drivers\VBoxGuest.sys",
    r"C:\Windows\System32\drivers\VBoxSF.sys",
    r"C:\Windows\System32\drivers\vmci.sys",
    r"C:\Windows\System32\drivers\vmhgfs.sys",
    r"C:\Windows\System32\drivers\vmmouse.sys",
    r"C:\Windows\System32\drivers\vmusbmouse.sys",
)

_VM_DEVICE_FILES_LINUX = (
    "/dev/vboxguest",
    "/dev/vboxuser",
)


# ── Public API ─────────────────────────────────────────────────────────────────


def is_vm() -> bool:
    """Return True if any VM / sandbox indicator is detected."""
    return _check_processes() or _check_registry() or _check_dmi() or _check_driver_files()


# ── Detection routines ─────────────────────────────────────────────────────────


def _check_processes() -> bool:
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in _VM_PROCESSES:
                log.warning("vm_detect.process_found", process=name)
                return True
    except Exception:
        pass
    return False


def _check_registry() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        for key_path in _VM_REGISTRY_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path):
                    log.warning("vm_detect.registry_key_found", key=key_path)
                    return True
            except (FileNotFoundError, PermissionError):
                continue
    except Exception:
        pass
    return False


def _check_dmi() -> bool:
    if sys.platform != "linux":
        return False
    for dmi_path in _VM_DMI_PATHS:
        try:
            content = Path(dmi_path).read_text(errors="ignore").lower().strip()
            if any(kw in content for kw in _VM_DMI_KEYWORDS):
                log.warning("vm_detect.dmi_indicator", path=dmi_path, value=content[:64])
                return True
        except Exception:
            continue
    return False


def _check_driver_files() -> bool:
    if sys.platform == "win32":
        for drv in _VM_DRIVER_FILES_WIN:
            if Path(drv).exists():
                log.warning("vm_detect.driver_found", driver=drv)
                return True
    elif sys.platform == "linux":
        for dev in _VM_DEVICE_FILES_LINUX:
            if Path(dev).exists():
                log.warning("vm_detect.device_found", device=dev)
                return True
    return False
