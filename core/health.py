"""Startup dependency health checker for the lightweight AgentMax runtime."""

from __future__ import annotations

import importlib
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    critical: bool = True
    fix_hint: str = ""


@dataclass
class HealthReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_critical_ok(self) -> bool:
        return all(result.ok for result in self.results if result.critical)

    @property
    def failures(self) -> list[CheckResult]:
        return [result for result in self.results if not result.ok]

    def print_report(self) -> None:
        width = 52
        print("\n" + "=" * width)
        print("  AgentMax -- Dependency Health Check")
        print("=" * width)
        for result in self.results:
            icon = "[OK]" if result.ok else ("[FAIL]" if result.critical else "[WARN]")
            tag = "" if result.ok else (" [CRITICAL]" if result.critical else " [optional]")
            print(f"  {icon:<6} {result.name:<24} {result.message}{tag}")
            if not result.ok and result.fix_hint:
                print(f"    -> {result.fix_hint}")
        print("=" * width)
        if self.all_critical_ok:
            print("  All critical dependencies OK.\n")
        else:
            print(f"  {len(self.failures)} failure(s) -- fix them before running tasks.\n")


def _check_import(module: str, display: str | None = None, *, critical: bool = True) -> CheckResult:
    name = display or module.split(".")[0]
    try:
        importlib.import_module(module)
        return CheckResult(
            name=name, ok=True, message=_get_version(module.split(".")[0]), critical=critical
        )
    except ImportError as exc:
        return CheckResult(
            name=name,
            ok=False,
            message=str(exc)[:60],
            critical=critical,
            fix_hint=f"pip install {name.lower()}",
        )


def _get_version(pkg: str) -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version(pkg)
    except Exception:
        return "installed"


def check_rust_vision_bridge() -> CheckResult:
    """Check that the Python RustVisionBridge (wrapping PixelAnalyzer) works."""
    try:
        from core.rust_vision_bridge import RustVisionBridge

        bridge = RustVisionBridge()
        width, height = bridge._pixel.get_screen_dimensions()
        return CheckResult("rust vision bridge", ok=True, message=f"screen {width}x{height}")
    except Exception as exc:
        return CheckResult("rust vision bridge", ok=False, message=str(exc)[:60], critical=True)


def check_win32() -> CheckResult:
    if sys.platform != "win32":
        return CheckResult("win32api", ok=True, message="skipped (non-Windows)", critical=False)
    try:
        import win32api  # noqa: F401
        import win32gui  # noqa: F401

        return CheckResult("win32api", ok=True, message=_get_version("pywin32"))
    except ImportError:
        return CheckResult(
            "win32api",
            ok=False,
            message="not installed",
            critical=True,
            fix_hint="pip install pywin32 then run: python -m pywin32_postinstall -install",
        )


def check_input() -> CheckResult:
    try:
        import pydirectinput  # noqa: F401

        return CheckResult("input (pydirectinput)", ok=True, message=_get_version("pydirectinput"))
    except ImportError:
        pass
    try:
        import pyautogui  # noqa: F401

        return CheckResult("input (pyautogui fallback)", ok=True, message=_get_version("pyautogui"))
    except ImportError:
        return CheckResult(
            "input",
            ok=False,
            message="no input backend",
            critical=True,
            fix_hint="pip install pydirectinput",
        )


def check_anthropic() -> CheckResult:
    return _check_import("anthropic", "anthropic sdk", critical=False)


def check_chromadb() -> CheckResult:
    result = _check_import("chromadb", "chromadb (memory)", critical=False)
    if not result.ok:
        result.message = "not installed -- long-term memory disabled"
    return result


def check_network_ports(ws_port: int = 7788, api_port: int = 7789) -> CheckResult:
    busy = []
    for port in (ws_port, api_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(port)
    if busy:
        return CheckResult(
            "IPC ports",
            ok=False,
            message=f"ports {busy} already in use",
            critical=True,
            fix_hint=f"Stop any process using ports {busy} or change AGENTMAX_WS_PORT/API_PORT in .env",
        )
    return CheckResult("IPC ports", ok=True, message=f"{ws_port}, {api_port} free")


def run_health_check(
    ws_port: int = 7788,
    api_port: int = 7789,
    verbose: bool = True,
) -> HealthReport:
    checks: list[Callable[[], CheckResult]] = [
        check_rust_vision_bridge,
        check_win32,
        check_input,
        lambda: _check_import("fastapi", "fastapi"),
        lambda: _check_import("websockets", "websockets"),
        check_anthropic,
        check_chromadb,
        lambda: check_network_ports(ws_port, api_port),
    ]

    report = HealthReport()
    for check_fn in checks:
        try:
            result = check_fn()
        except Exception as exc:
            result = CheckResult(
                name=getattr(check_fn, "__name__", "unknown"),
                ok=False,
                message=f"check error: {exc}",
                critical=False,
            )
        report.results.append(result)
        log.debug("health.check", name=result.name, ok=result.ok, msg=result.message)

    if verbose:
        report.print_report()

    return report
